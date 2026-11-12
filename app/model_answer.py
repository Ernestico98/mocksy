"""Model answer: a neutral, publishable explainer of how to solve a session's problem.

Two modes:
- "video": script -> diagram -> voice -> scenes -> render -> qa -> fixes -> package
  (an MP4 with chapters, youtube.txt, the script as markdown and the solution diagram)
- "diagram": script -> diagram -> package (the write-up and the diagram only; one model call)

The backend orchestrates; every model step is a fresh, bounded `claude -p` process with a minimal tool set,
`--setting-sources "" --strict-mcp-config` and `--max-budget-usd` (whatever is left of the generation's cap).
Cost bounds (see README): one script worker (the only judgment step, it reads the evaluation, the notes, the
pack brief and a format reference, never the transcript); two scene workers, each reading only the brief, the
toolkit, one reference scene and its own beats, with at most 2 still-render rounds per scene; one QA pass on
960 px end-of-beat frames; one fix round. Voice, render and packaging use no model, and this module never
reads an image.

State lives in <session>/model_answer/state.json, so a failed or cancelled generation retries from the stage
that stopped, and a later "video" run reuses the script and diagram of a "diagram" run.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from . import speech
from .config import LANGUAGES, PROMPTS, ROOT, load_settings
from .explainer import script as sc
from .sessions import SafeDict, pack_snapshot

EXPLAINER = ROOT / "app" / "explainer"
PROMPTS_X = PROMPTS / "explainer"
STAGES = {
    "video": ["script", "diagram", "voice", "scenes", "render", "qa", "fixes", "package"],
    "diagram": ["script", "diagram", "package"],
}
LABELS = {"script": "Script", "diagram": "Diagram", "voice": "Voice", "scenes": "Scenes", "render": "Render",
          "qa": "QA", "fixes": "Fixes", "package": "Package"}
LEAD, GAP, TAIL = 0.35, 0.45, 0.6   # seconds of silence before, between and after beats
SCENE_WORKERS = 2
FPS = 30
FIX_SEVERITIES = {"high", "medium"}
VIDEO = "model-answer.mp4"
WORKER_TIMEOUT = 40 * 60

# Estimates. USD per million tokens: input, output, cache read, cache write (Claude Code caches for 1 h).
PRICES = {"opus": (4, 20, 0.2, 8), "sonnet": (2, 10, 0.2, 4), "haiku": (0.1, 0.5, 0.01, 0.2), "fable": (10, 50, 0.25, 20)}
# Tokens per stage (cache write, cache read, output) for an 8-scene video, from the prototype runs that set the
# cost bounds: ~57k Opus tokens for the script and ~300k Sonnet tokens (mostly cache reads) for scenes, QA, fixes.
PROFILE = {"script": (30_000, 15_000, 12_000), "scenes": (30_000, 170_000, 20_000),
           "qa": (25_000, 40_000, 4_000), "fixes": (5_000, 30_000, 5_000)}
MINUTES = {"video": (17, 25), "diagram": (2, 5)}


class Cancelled(Exception):
    pass


class StageError(Exception):
    pass


class BudgetExceeded(StageError):
    pass


class Busy(Exception):
    pass


# ------------------------------------------------------------------ processes (patched in tests)

def run_claude(cwd: Path, prompt: str, *, system: str, model: str, tools: list[str], allowed: list[str],
               budget_usd: float, resume: str | None = None, on_start: Callable | None = None) -> dict:
    """One bounded Claude Code run. Returns result, cost_usd, usage, session_id, is_error, subtype."""
    args = ["claude", "-p", prompt, "--output-format", "json", "--model", model, "--system-prompt", system,
            "--tools", *tools, "--allowedTools", *allowed, "--permission-mode", "acceptEdits",
            "--setting-sources", "", "--strict-mcp-config", "--max-budget-usd", f"{max(budget_usd, 0.01):.2f}"]
    if resume:
        args += ["--resume", resume]
    t0 = time.time()
    proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            stdin=subprocess.DEVNULL)
    if on_start:
        on_start(proc)
    try:
        out, err = proc.communicate(timeout=WORKER_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        err = f"timed out after {WORKER_TIMEOUT // 60} min. {err}"
    try:
        data = json.loads(out)
    except ValueError:
        data = {"is_error": True, "result": (err or out or f"claude exited with {proc.returncode}").strip()[-500:]}
    return {
        "result": data.get("result") or "", "cost_usd": float(data.get("total_cost_usd") or 0),
        "usage": data.get("usage") or {}, "session_id": data.get("session_id"),
        "is_error": bool(data.get("is_error")) or proc.returncode not in (0, None) and not data.get("result"),
        "subtype": data.get("subtype") or "", "duration_s": round(time.time() - t0, 1),
    }


def run_node(cwd: Path, args: list[str], *, env: dict | None = None, on_line: Callable[[str], None] | None = None,
             on_start: Callable | None = None):
    """`node render.mjs <args>` (the toolkit's renderer); raises StageError with its stderr on failure."""
    proc = subprocess.Popen(["node", str(EXPLAINER / "render.mjs"), *args], cwd=cwd, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            env={**os.environ, "EXPLAINER_WORK": str(cwd), **(env or {})})
    if on_start:
        on_start(proc)
    err: list[str] = []
    t = threading.Thread(target=lambda: err.extend(proc.stderr), daemon=True)
    t.start()
    for line in proc.stdout:
        if on_line:
            on_line(line.strip())
    proc.wait()
    t.join(5)
    if proc.returncode:
        raise StageError(f"render.mjs {args[0]} failed: {''.join(err)[-600:].strip() or proc.returncode}")


def explainer_ready() -> str | None:
    """Why the video can't be rendered here (None: it can)."""
    if not shutil.which("node"):
        return "Node.js isn't installed (needed to render the video)."
    if not shutil.which("ffmpeg"):
        return "ffmpeg isn't installed (needed to encode the video)."
    if not (EXPLAINER / "node_modules" / "playwright-core").exists():
        return "The renderer isn't installed: run scripts/setup.sh (or `cd app/explainer && pnpm install`)."
    return None


# ------------------------------------------------------------------ estimates

def _price(model: str):
    return next((p for k, p in PRICES.items() if k in model.lower()), PRICES["sonnet"])


def estimate(mode: str, settings: dict) -> dict:
    scale = int(settings["ma_scenes"]) / 8
    models = {"script": settings["ma_script_model"], "scenes": settings["ma_scene_model"],
              "qa": settings["ma_qa_model"], "fixes": settings["ma_scene_model"]}
    usd, tokens = 0.0, {}
    for stage in (["script"] if mode == "diagram" else list(PROFILE)):
        write, read, out = PROFILE[stage]
        if stage != "script":
            write, read, out = write * scale, read * scale, out * scale
        if stage == "fixes" and int(settings["ma_fix_rounds"]) < 1:
            continue
        pin, pout, pread, pwrite = _price(models[stage])
        usd += (write * pwrite + read * pread + out * pout) / 1e6
        tokens[models[stage]] = tokens.get(models[stage], 0) + round(write + read + out)
    lo, hi = MINUTES[mode]
    if mode == "video":
        lo, hi = round(lo * (0.5 + scale / 2)), round(hi * (0.5 + scale / 2))
    return {"usd": [round(usd * 0.7, 2), round(usd * 2, 2)], "minutes": [lo, hi], "tokens": tokens,
            "budget_usd": float(settings["ma_budget_usd"])}


# ------------------------------------------------------------------ state

def _now() -> float:
    return round(time.time(), 3)


def load_state(d: Path) -> dict | None:
    try:
        return json.loads((d / "state.json").read_text())
    except (OSError, ValueError):
        return None


def slug(text: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")[:60].strip("-") or "model-answer"


OUTPUT_NAMES = {"video": ".mp4", "script": ".md", "youtube": "-youtube.txt", "diagram_svg": "-diagram.svg",
                "diagram_png": "-diagram.png"}


def public(sid: str, state: dict | None) -> dict | None:
    if not state:
        return None
    base = slug((state.get("script") or {}).get("title", ""))
    out = {k: {"url": f"/files/{sid}/model_answer/{f}?v={int(state.get('ended_at') or 0)}", "name": base + OUTPUT_NAMES[k]}
           for k, f in (state.get("outputs") or {}).items()}
    snapshot = json.loads(json.dumps(state))  # broadcast a copy: the live state keeps changing
    return {**snapshot, "outputs": out, "labels": {k: LABELS[k] for k in STAGES[state["mode"]]}}


class Generation:
    def __init__(self, sdir: Path, meta: dict, broadcast: Callable[[dict], None], mode: str, fresh: bool = False):
        self.sdir, self.meta, self.broadcast_raw, self.mode = sdir, meta, broadcast, mode
        self.sid = sdir.name
        self.dir = sdir / "model_answer"
        self.work = self.dir / "work"
        self.settings = load_settings()
        self.cancel_event = threading.Event()
        self.lock = threading.RLock()
        self.procs: set = set()
        self.alive = True
        self.last_push = 0.0
        old = None if fresh else load_state(self.dir)
        if old is None:
            shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        stages = {}
        for k in STAGES[mode]:
            prev = (old or {}).get("stages", {}).get(k, {})
            reusable = (old or {}).get("mode") == mode or k in ("script", "diagram")  # a diagram run feeds a video run
            stages[k] = prev if reusable and prev.get("status") in ("done", "skipped") else {"status": "pending"}
        self.state = {
            **(old or {}),
            "mode": mode, "status": "running", "error": None, "stages": stages, "started_at": _now(), "ended_at": None,
            "budget_usd": float(self.settings["ma_budget_usd"]), "cost_usd": (old or {}).get("cost_usd", 0.0),
            "runs": (old or {}).get("runs", []),
            "models": {k: self.settings[f"ma_{k}_model"] for k in ("script", "scene", "qa")},
        }
        self.save(force=True)

    # ---------------------------------------------------------------- bookkeeping
    def save(self, force: bool = True):
        with self.lock:
            tmp = self.dir / "state.json.tmp"
            tmp.write_text(json.dumps(self.state, indent=1, ensure_ascii=False))
            tmp.replace(self.dir / "state.json")
            now = time.time()
            if force or now - self.last_push > 1:
                self.last_push = now
                self.broadcast_raw({"type": "model_answer", "session": self.sid, "state": public(self.sid, self.state)})

    def stage(self, key: str, **fields):
        with self.lock:
            self.state["stages"][key].update(fields)
        self.save(force="progress" not in fields)

    def check(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def track(self, proc):
        with self.lock:
            self.procs.add(proc)
        if self.cancel_event.is_set():
            proc.terminate()

    def cancel(self):
        self.cancel_event.set()
        with self.lock:
            procs = list(self.procs)
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass

    def remaining(self) -> float:
        return self.state["budget_usd"] - self.state["cost_usd"]

    def budget_error(self) -> BudgetExceeded:
        return BudgetExceeded(
            f"Stopped: the budget cap of ${self.state['budget_usd']:.2f} was reached "
            f"(${self.state['cost_usd']:.2f} spent). Raise the cap in the settings and retry, or start over.")

    def worker(self, stage: str, name: str, cwd: Path, prompt: str, system: str, model: str, tools: list[str],
               allowed: list[str], resume: str | None = None, share: int = 1) -> dict:
        self.check()
        if self.remaining() < 0.01:
            raise self.budget_error()
        r = run_claude(cwd, prompt, system=system, model=model, tools=tools, allowed=allowed,
                       budget_usd=self.remaining() / share, resume=resume, on_start=self.track)
        u = r.get("usage") or {}
        with self.lock:
            self.state["runs"].append({
                "stage": stage, "worker": name, "model": model, "cost_usd": round(r["cost_usd"], 4),
                "session_id": r.get("session_id"), "duration_s": r.get("duration_s"), "subtype": r.get("subtype"),
                "usage": {k: u.get(k, 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                   "cache_creation_input_tokens")},
            })
            self.state["cost_usd"] = round(self.state["cost_usd"] + r["cost_usd"], 4)
        self.save()
        self.check()
        if "budget" in (r.get("subtype") or "") or self.remaining() < 0:
            raise self.budget_error()
        if r["is_error"]:
            raise StageError(f"{name} failed: {(r['result'] or r.get('subtype') or 'unknown error')[:400]}")
        return r

    # ---------------------------------------------------------------- run
    def run(self):
        key = None
        try:
            for key in STAGES[self.mode]:
                if self.state["stages"][key].get("status") in ("done", "skipped"):
                    continue
                self.check()
                self.stage(key, status="running", started=_now(), detail="", progress=None)
                getattr(self, f"stage_{key}")()
                self.stage(key, status="done", ended=_now(), progress=None)
            self.state["status"] = "done"
        except Cancelled:
            self.state["status"] = "cancelled"
            self.state["error"] = "Cancelled. Retry to continue from this stage."
            if key:
                self.state["stages"][key].update(status="cancelled", progress=None, ended=_now())
        except Exception as e:  # noqa: BLE001 — any failure leaves a retryable state
            self.state["status"] = "failed"
            self.state["error"] = str(e) or e.__class__.__name__
            if not isinstance(e, StageError):
                print("model answer failed:", repr(e), flush=True)
            if key:
                self.state["stages"][key].update(status="failed", progress=None, ended=_now())
        finally:
            self.state["ended_at"] = _now()
            self.alive = False
            self.save(force=True)

    # ---------------------------------------------------------------- inputs
    def company(self) -> str:
        return "" if self.settings["ma_name_company"] else (self.meta.get("company") or "")

    def brief(self) -> str:
        m = self.meta
        pack = pack_snapshot(m)
        phases = "\n".join(f"- {p['label']} ({p['minutes']} min)" for p in m.get("phases", []))
        return (f"# Problem\n\nRequested problem: {m.get('problem') or 'not fixed in advance: notes.md has the one that was used'}\n"
                f"Level: {m.get('level') or 'not specified'}\nFocus: {m.get('focus') or 'none'}\n"
                f"Narration language: {LANGUAGES.get(m['lang'], m['lang'])}\n\n"
                f"# Phases of the round (structure the explainer the same way)\n\n{phases}\n\n"
                f"# The round, as briefed to the interviewer (context only)\n\n{pack['body']}\n")

    def emphasis(self) -> str:
        try:
            fb = json.loads((self.sdir / "feedback.json").read_text())
        except (OSError, ValueError):
            fb = {}
        out = ["# What to emphasise",
               "Use this only to decide which concepts get the most explanation time. Never refer to it, "
               "to anyone's attempt, or to how it went.", ""]
        if fb.get("top3"):
            out += ["## Concepts to explain thoroughly", *[f"- {t}" for t in fb["top3"]], ""]
        if fb.get("review"):
            out += ["## Topics worth a clear model explanation", *[f"- {r.get('topic')}: {r.get('what')}" for r in fb["review"]], ""]
        if fb.get("report"):
            out += ["## Review notes", fb["report"]]
        return "\n".join(out) + "\n"

    def script_prompt(self) -> str:
        n = int(self.settings["ma_scenes"])
        company = self.meta.get("company") or ""
        if company and self.settings["ma_name_company"]:
            rule = f"You may name {company} where it helps."
        elif company:
            rule = f"Never name {company} or its products: describe the product generically."
        else:
            rule = "Don't name any real company as the target."
        words = n * sc.WORDS_PER_SCENE
        return (PROMPTS_X / "script.md").read_text().format_map(SafeDict(
            scenes=n, scenes_min=max(1, n - 1), words=words, minutes=f"{words / 140:.0f}-{words / 125:.0f}",
            language_name=LANGUAGES.get(self.meta["lang"], self.meta["lang"]), company_rule=rule))

    def script(self) -> dict:
        return json.loads((self.dir / "script.json").read_text())

    # ---------------------------------------------------------------- stages
    def check_script(self, d: Path) -> list[str]:
        try:
            data = json.loads((d / "script.json").read_text())
        except OSError:
            return ["script.json was not written"]
        except ValueError as e:
            return [f"script.json is not valid JSON: {e}"]
        problems = sc.validate(data, int(self.settings["ma_scenes"]))
        if not problems:
            problems = [f"publishing rule: {v}" for v in sc.neutrality(data, self.company())]
        return problems

    def stage_script(self):
        d = self.dir / "script"
        d.mkdir(exist_ok=True)
        (d / "brief.md").write_text(self.brief())
        (d / "emphasis.md").write_text(self.emphasis())
        notes = self.sdir / "notes.md"
        if notes.exists():
            shutil.copy(notes, d / "notes.md")
        shutil.copy(PROMPTS_X / "format.md", d / "format.md")
        (d / "script.json").unlink(missing_ok=True)
        system, model = self.script_prompt(), self.settings["ma_script_model"]
        tools = ["Read", "Write"]
        r = self.worker("script", "script", d, "Write script.json now.", system, model, tools, tools)
        problems = self.check_script(d)
        if problems:
            self.stage("script", detail=f"{len(problems)} problem(s): one revision")
            msg = ("script.json was rejected. Fix every problem below, write script.json again, then reply with "
                   "one line.\n" + "\n".join(f"- {p}" for p in problems[:40]))
            self.worker("script", "script revision", d, msg, system, model, tools, tools, resume=r.get("session_id"))
            problems = self.check_script(d)
            if problems:
                raise StageError("The script still breaks the rules after one revision:\n"
                                 + "\n".join(f"- {p}" for p in problems[:12]))
        data = json.loads((d / "script.json").read_text())
        (self.dir / "script.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
        (self.dir / "script.md").write_text(sc.markdown(data, None))
        self.state["script"] = {"title": data["title"], "description": data["description"],
                                "words": sc.words(data), "scenes": len(data["scenes"]),
                                "assumptions": data.get("assumptions", [])}
        self.state.setdefault("outputs", {})["script"] = "script.md"
        self.stage("script", detail=f"{len(data['scenes'])} scenes, {sc.words(data)} words")

    def stage_diagram(self):
        data = self.script()
        (self.dir / "diagram.svg").write_text(sc.diagram_svg(data["diagram"]))
        outputs = self.state.setdefault("outputs", {})
        outputs["diagram_svg"] = "diagram.svg"
        reason = explainer_ready()
        if reason:
            self.stage("diagram", detail=f"SVG only: {reason}")
            return
        run_node(self.dir, ["svg", "diagram.svg", "diagram.png"], env=self.node_env(), on_start=self.track)
        outputs["diagram_png"] = "diagram.png"

    def node_env(self) -> dict:
        return {"EXPLAINER_CHROME": str(self.settings.get("ma_chrome") or "auto")}

    def stage_voice(self):
        data = self.script()
        lang = self.meta["lang"]
        voice = self.settings.get(f"voice_{lang}") or self.settings["voice_en"]
        audio = self.work / "audio"
        audio.mkdir(parents=True, exist_ok=True)
        timings = {}
        for n, scene in enumerate(data["scenes"]):
            self.check()
            sr, parts, beats, t = 24000, [], [], LEAD
            parts.append(np.zeros(int(LEAD * sr), dtype=np.float32))
            for i, beat in enumerate(scene["beats"]):
                samples, sr = speech.synthesize(speech.speakable(beat["say"]), voice, lang, self.settings["speed"])
                dur = len(samples) / sr
                beats.append({"start": round(t, 3), "end": round(t + dur, 3), "say": beat["say"]})
                parts.append(np.asarray(samples, dtype=np.float32))
                gap = GAP if i < len(scene["beats"]) - 1 else TAIL
                parts.append(np.zeros(int(gap * sr), dtype=np.float32))
                t += dur + gap
            wav = np.concatenate(parts)
            wav *= 0.89 / max(1e-6, float(np.abs(wav).max()))  # peak at -1 dBFS
            sf.write(audio / f"{scene['id']}.wav", wav, sr)
            timings[scene["id"]] = {"duration": round(len(wav) / sr, 3), "beats": beats}
            self.stage("voice", progress=(n + 1) / len(data["scenes"]), detail=f"{n + 1}/{len(data['scenes'])} scenes")
        (self.work / "timings.json").write_text(json.dumps(timings, indent=1, ensure_ascii=False))
        total = sum(s["duration"] for s in timings.values())
        self.state["duration_s"] = round(total, 1)
        self.stage("voice", detail=f"{sc.clock(total)} of narration")

    def timings(self) -> dict:
        return json.loads((self.work / "timings.json").read_text())

    def prepare_work(self):
        (self.work / "scenes").mkdir(parents=True, exist_ok=True)
        (self.work / "jobs").mkdir(exist_ok=True)
        for f in ("BRIEF.md", "lib.js", "reference.js", "player.html"):
            shutil.copy(EXPLAINER / f, self.work / f)
        (self.work / "render.mjs").write_text(
            f"// Runs the shared renderer on this folder (see BRIEF.md).\nimport {json.dumps((EXPLAINER / 'render.mjs').as_uri())};\n")
        shutil.copy(self.dir / "script.json", self.work / "script.json")

    def scene_system(self) -> str:
        return (PROMPTS_X / "scene.md").read_text()

    SCENE_TOOLS = ["Read", "Write", "Edit", "Bash"]
    SCENE_ALLOWED = ["Read", "Write", "Edit", "Bash(node render.mjs beats:*)", "Bash(node render.mjs preview:*)"]

    def stage_scenes(self):
        self.prepare_work()
        data, timings = self.script(), self.timings()
        ids = [s["id"] for s in data["scenes"]]
        scene_file = lambda i: self.work / "scenes" / f"{i}.js"  # noqa: E731
        missing = [s for s in data["scenes"] if not scene_file(s["id"]).exists()]
        workers = self.state.setdefault("workers", {})
        groups, size = [], -(-len(missing) // SCENE_WORKERS) if missing else 0
        for k in range(0, len(missing), max(size, 1)):
            groups.append(missing[k:k + size])
        jobs = []
        for group in groups:
            name = f"w{len(workers) + len(jobs) + 1}"
            job = {"scenes": [{"id": s["id"], "title": s["title"], "duration": timings[s["id"]]["duration"],
                               "beats": [{**b, **{k: timings[s["id"]]["beats"][j][k] for k in ("start", "end")}}
                                         for j, b in enumerate(s["beats"])]} for s in group]}
            (self.work / "jobs" / f"{name}.json").write_text(json.dumps(job, indent=1, ensure_ascii=False))
            jobs.append((name, [s["id"] for s in group]))

        errors: list[Exception] = []
        done = threading.Event()

        def one(name: str, scene_ids: list[str]):
            prompt = (f"Your scenes: {', '.join(scene_ids)}. Your job file: jobs/{name}.json. Read BRIEF.md first, then "
                      "follow it: write each scene, check it with `node render.mjs beats <id>` (at most 2 rounds per "
                      "scene), then report.")
            try:
                r = self.worker("scenes", name, self.work, prompt, self.scene_system(), self.settings["ma_scene_model"],
                                self.SCENE_TOOLS, self.SCENE_ALLOWED, share=len(jobs))
                with self.lock:
                    workers[name] = {"session_id": r.get("session_id"), "scenes": scene_ids}
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def progress():
            while not done.wait(2):
                n = sum(scene_file(i).exists() for i in ids)
                self.stage("scenes", progress=n / len(ids), detail=f"{n}/{len(ids)} scenes")

        threads = [threading.Thread(target=one, args=job, daemon=True) for job in jobs]
        watcher = threading.Thread(target=progress, daemon=True)
        watcher.start()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        done.set()
        for kind in (Cancelled, BudgetExceeded, Exception):
            e = next((e for e in errors if isinstance(e, kind)), None)
            if e:
                raise e
        still = [i for i in ids if not scene_file(i).exists()]
        if still:
            raise StageError(f"The scene workers didn't write {', '.join(still)}. Retry to write the missing scenes.")
        self.state["scene_flags"] = self.scene_flags()
        self.stage("scenes", detail=f"{len(ids)}/{len(ids)} scenes")

    def scene_flags(self) -> dict[str, list[str]]:
        """On-screen strings in the scene sources that break the publishing rules (deterministic check)."""
        flags = {}
        for f in sorted((self.work / "scenes").glob("*.js")):
            found = [f'"{s[:80]}": {v}' for s in sc.scene_literals(f.read_text()) for v in sc.check_text(s, self.company())]
            if found:
                flags[f.stem] = found
        return flags

    def scene_hash(self, sid: str, timings: dict, data: dict) -> str:
        h = hashlib.sha1()
        h.update((self.work / "scenes" / f"{sid}.js").read_bytes())
        h.update(json.dumps([timings[sid], next(s for s in data["scenes"] if s["id"] == sid)], sort_keys=True).encode())
        return h.hexdigest()

    def render_changed(self, stage: str):
        data, timings = self.script(), self.timings()
        rendered = self.state.setdefault("rendered", {})
        todo = [s["id"] for s in data["scenes"]
                if rendered.get(s["id"]) != self.scene_hash(s["id"], timings, data)
                or not (self.work / "out" / f"{s['id']}.mp4").exists()]
        if todo:
            expected = {i: max(1, round(timings[i]["duration"] * FPS)) for i in todo}
            frames = dict.fromkeys(todo, 0)

            def on_line(line: str):
                m = re.match(r"progress (\S+) (\d+) (\d+)", line)
                if m and m.group(1) in frames:
                    frames[m.group(1)] = int(m.group(2))
                    pct = sum(frames.values()) / sum(expected.values())
                    self.stage(stage, progress=pct, detail=f"rendering {len(todo)} scene(s): {pct * 100:.0f}%")

            self.check()
            run_node(self.work, ["video", *todo], env=self.node_env(), on_line=on_line, on_start=self.track)
            for i in todo:
                rendered[i] = self.scene_hash(i, timings, data)
        self.check()
        self.stage(stage, detail="joining scenes")
        run_node(self.work, ["concat", str(self.dir / VIDEO)], env=self.node_env(), on_start=self.track)
        self.state.setdefault("outputs", {})["video"] = VIDEO
        return todo

    def stage_render(self):
        todo = self.render_changed("render")
        self.stage("render", detail=f"{len(todo)} scene(s) rendered")

    def stage_qa(self):
        run_node(self.work, ["qa"], env=self.node_env(), on_start=self.track)
        ids = [s["id"] for s in self.script()["scenes"]]
        found = [self.work / "qa.json", self.work / "out" / "qa" / "qa.json"]  # the second: a common slip
        for f in found:
            f.unlink(missing_ok=True)
        self.worker("qa", "qa", self.work, "Review the frames now.", (PROMPTS_X / "qa.md").read_text(),
                    self.settings["ma_qa_model"], ["Read", "Write"], ["Read", "Write"])
        try:
            qa = json.loads(next((f for f in found if f.exists()), found[0]).read_text())
        except (OSError, ValueError) as e:
            raise StageError(f"The QA pass didn't write a valid qa.json ({e.__class__.__name__}).") from e
        issues = []
        for i in qa.get("issues", []) if isinstance(qa, dict) else []:
            if not isinstance(i, dict) or i.get("scene") not in ids:
                continue
            issues.append({"scene": i["scene"], "t": i.get("t"),
                           "severity": i.get("severity") if i.get("severity") in ("high", "medium", "low") else "medium",
                           "kind": i.get("kind") or "other", "issue": str(i.get("issue", ""))[:400],
                           "fix": str(i.get("fix", ""))[:400]})
        for sid, found in (self.state.get("scene_flags") or {}).items():
            for f in found:
                issues.append({"scene": sid, "t": None, "severity": "high", "kind": "neutrality",
                               "issue": f"On-screen text breaks the publishing rules: {f}",
                               "fix": "Reword it as a neutral explanation of the topic."})
        self.state["qa"] = {"issues": issues, "summary": str((qa or {}).get("summary", ""))[:300]}
        counts = {s: sum(i["severity"] == s for i in issues) for s in ("high", "medium", "low")}
        self.stage("qa", detail=", ".join(f"{v} {k}" for k, v in counts.items() if v) or "no issues")

    def stage_fixes(self):
        if int(self.settings["ma_fix_rounds"]) < 1:
            self.stage("fixes", detail="turned off in settings")
            return
        issues = [i for i in (self.state.get("qa") or {}).get("issues", [])
                  if i["severity"] in FIX_SEVERITIES or i["kind"] == "neutrality"]
        if not issues:
            self.stage("fixes", detail="nothing to fix")
            return
        workers = self.state.get("workers", {})
        owner = {s: name for name, w in workers.items() for s in w["scenes"]}
        by_worker: dict[str, list[dict]] = {}
        for i in issues:
            by_worker.setdefault(owner.get(i["scene"], "w1"), []).append(i)
        errors: list[Exception] = []

        def one(name: str, items: list[dict]):
            scenes = sorted({i["scene"] for i in items})
            lines = "\n".join(f"- {i['scene']}" + (f" at {i['t']}s" if i.get("t") is not None else "")
                              + f" ({i['severity']}, {i['kind']}): {i['issue']} Fix: {i['fix']}" for i in items)
            msg = (f"A review of the rendered frames found these problems in {', '.join(scenes)}. Fix them by editing only "
                   f"those scene files; at most one `node render.mjs beats <id>` check per scene. Then reply with one line."
                   f"\n{lines}")
            w = workers.get(name) or {}
            if not w.get("session_id"):
                msg = f"Read BRIEF.md and jobs/{name}.json first (your scenes already exist in scenes/).\n" + msg
            try:
                self.worker("fixes", f"{name} fixes", self.work, msg, self.scene_system(), self.settings["ma_scene_model"],
                            self.SCENE_TOOLS, self.SCENE_ALLOWED, resume=w.get("session_id"), share=len(by_worker))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        self.stage("fixes", detail=f"{len(issues)} issue(s) in {len({i['scene'] for i in issues})} scene(s)")
        threads = [threading.Thread(target=one, args=(n, items), daemon=True) for n, items in by_worker.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for kind in (Cancelled, BudgetExceeded, Exception):
            e = next((e for e in errors if isinstance(e, kind)), None)
            if e:
                raise e
        flags = self.scene_flags()
        self.state["scene_flags"] = flags
        if flags:
            raise StageError("Scene text still breaks the publishing rules after the fixes:\n"
                             + "\n".join(f"- {k}: {v[0]}" for k, v in flags.items()))
        todo = self.render_changed("fixes")
        self.state["qa"]["fixed"] = len(issues)
        self.stage("fixes", detail=f"{len(issues)} issue(s) sent back; {len(todo)} scene(s) re-rendered")

    def stage_package(self):
        data = self.script()
        outputs = self.state.setdefault("outputs", {})
        diagram = "diagram.svg" if (self.dir / "diagram.svg").exists() else None
        (self.dir / "script.md").write_text(sc.markdown(data, diagram))
        outputs["script"] = "script.md"
        if self.mode == "video" and (self.dir / VIDEO).exists():
            timings = self.timings()
            (self.dir / "youtube.txt").write_text(sc.youtube(data, timings))
            outputs["youtube"] = "youtube.txt"
            t, chapters = 0.0, []
            for s in data["scenes"]:
                chapters.append({"t": round(t, 2), "title": s["title"]})
                t += timings[s["id"]]["duration"]
            self.state["chapters"] = chapters
        names = {"video": "MP4", "youtube": "YouTube text", "script": "script", "diagram_svg": "diagram"}
        self.stage("package", detail=", ".join(names[k] for k in names if k in outputs))


# ------------------------------------------------------------------ module API

_current: Generation | None = None
_glock = threading.Lock()


def current() -> Generation | None:
    return _current if _current and _current.alive else None


def status(sdir: Path) -> dict:
    sid = sdir.name
    d = sdir / "model_answer"
    state = load_state(d)
    g = current()
    if state and state.get("status") == "running" and not (g and g.sid == sid):
        state["status"] = "failed"
        state["error"] = "Interrupted (the server stopped). Retry to continue from this stage."
        for st in state.get("stages", {}).values():
            if st.get("status") == "running":
                st.update(status="failed", progress=None)
        (d / "state.json").write_text(json.dumps(state, indent=1, ensure_ascii=False))
    settings = load_settings()
    return {"state": public(sid, state), "ready": explainer_ready(),
            "estimates": {m: estimate(m, settings) for m in STAGES},
            "settings": {k: v for k, v in settings.items() if k.startswith("ma_")}}


def start(sdir: Path, meta: dict, broadcast: Callable[[dict], None], mode: str, fresh: bool = False) -> dict:
    global _current
    if mode not in STAGES:
        raise ValueError(f"unknown mode {mode}")
    with _glock:
        g = current()
        if g:
            raise Busy("A model answer is already being generated" + ("" if g.sid == sdir.name else " for another session")
                       + ": wait for it or cancel it.")
        if mode == "video" and (reason := explainer_ready()):
            raise StageError(reason)
        g = Generation(sdir, meta, broadcast, mode, fresh)
        _current = g
    threading.Thread(target=g.run, daemon=True).start()
    return status(sdir)


def cancel(sid: str) -> bool:
    g = current()
    if g and g.sid == sid:
        g.cancel()
        return True
    return False


def delete(sdir: Path):
    g = current()
    if g and g.sid == sdir.name:
        raise Busy("Cancel the generation first.")
    shutil.rmtree(sdir / "model_answer", ignore_errors=True)
