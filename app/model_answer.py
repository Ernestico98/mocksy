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
from .packs import get_pack
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
# Tokens (cache write, cache read, output) measured on real runs (state.json "runs"): a scene worker used
# 25-52k / 116-224k / 6-21k for ONE scene; the script ~10k / 10k / 4k for 2 scenes (opus writes longer: budgeted
# at 15k output for 8); QA ~1.2k / 2.4k / 0.25k per frame (about 4 frames per scene); a fix touches ~40% of
# the scenes at about half a scene's cost each.
PROFILE = {"script": ((30_000, 15_000, 15_000), 0),           # (fixed, per scene)
           "scenes": ((0, 0, 0), (40_000, 180_000, 14_000)),
           "qa": ((5_000, 5_000, 500), (5_000, 10_000, 1_000)),
           "fixes": ((0, 0, 0), (8_000, 36_000, 2_800))}
# The script worker's folder holds exactly these inputs (the prompt lists them from here too)
SCRIPT_INPUTS = {
    "brief.md": "the problem, the kind of round it comes from, and its phases.",
    "scenario.md": "the problem as it was posed: the opening prompt and, when the notes have one, the hidden "
                   "context and numbers.",
    "emphasis.md": "which areas deserve the most explanation time.",
    "format.md": "the exact format of `script.json`, with an example. Follow it.",
}

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

def path_rule(tool: str, path: Path, glob: str = "") -> str:
    """A permission rule for an absolute path ("//" prefix in Claude Code rules). Writes are granted with
    `Edit(...)` rules, which cover every file-writing tool (`Write(...)` path rules don't grant anything)."""
    return f"{tool}(/{path.resolve()}{'/' + glob if glob else ''})"


def run_claude(cwd: Path, prompt: str, *, system: str, model: str, tools: list[str], allow: list[str],
               deny: list[str] = (), budget_usd: float, resume: str | None = None,
               on_start: Callable | None = None) -> dict:
    """One bounded Claude Code run. Returns result, cost_usd, usage, session_id, is_error, subtype.

    `tools` is the only tool set the model sees; `allow` and `deny` are permission rules scoped to paths.
    In the default permission mode a non-interactive run refuses everything the rules don't allow, so a
    worker can't read or write outside its folder. `budget_usd` is passed to --max-budget-usd as is
    (for a resumed session the caller adds what the session already cost)."""
    args = ["claude", "-p", prompt, "--output-format", "json", "--model", model, "--system-prompt", system,
            "--tools", *tools, "--allowedTools", *allow, "--permission-mode", "default",
            "--setting-sources", "", "--strict-mcp-config", "--max-budget-usd", f"{max(budget_usd, 0.01):.2f}"]
    if deny:
        args += ["--disallowedTools", *deny]
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


def renderer_ready() -> str | None:
    """Why frames and PNGs can't be rendered here (None: they can, if a browser is found)."""
    if not shutil.which("node"):
        return "Node.js isn't installed (needed to render the video)."
    if not (EXPLAINER / "node_modules" / "playwright-core").exists():
        return "The renderer isn't installed: run scripts/setup.sh (or `cd app/explainer && pnpm install`)."
    return None


def explainer_ready() -> str | None:
    """Why the video can't be made here (None: it can)."""
    if reason := renderer_ready():
        return reason
    if not shutil.which("ffmpeg"):
        return "ffmpeg isn't installed (needed to encode the video)."
    return None


LEGACY_HINT = ("This session predates the model-answer setting: set `model_answer = true` in its interview "
               "type's header to enable it.")


def availability(meta: dict) -> tuple[bool, str]:
    """Whether a session may get a model answer, and why not. New sessions carry `model_answer` (from the pack:
    explicit, or `whiteboard` by default). Older sessions don't: the pack file decides, only if it says so
    explicitly; otherwise no (a conversation-only round, such as personal stories, makes no sense and its brief
    may hold personal details)."""
    v = meta.get("model_answer")
    if isinstance(v, bool):
        return v, "" if v else "This interview type has no model answer (its pack sets model_answer = false)."
    try:
        v = get_pack(meta.get("pack", "")).get("model_answer")
    except KeyError:
        v = None
    if isinstance(v, bool):
        return v, "" if v else "This interview type has no model answer (its pack sets model_answer = false)."
    return False, LEGACY_HINT


def allowed(meta: dict) -> bool:
    return availability(meta)[0]


# ------------------------------------------------------------------ estimates

def _price(model: str):
    return next((p for k, p in PRICES.items() if k in model.lower()), PRICES["sonnet"])


def estimate(mode: str, settings: dict) -> dict:
    n = int(settings["ma_scenes"])
    models = {"script": settings["ma_script_model"], "scenes": settings["ma_scene_model"],
              "qa": settings["ma_qa_model"], "fixes": settings["ma_scene_model"]}
    usd, tokens = 0.0, {}
    for stage in (["script"] if mode == "diagram" else list(PROFILE)):
        if stage == "fixes" and int(settings["ma_fix_rounds"]) < 1:
            continue
        fixed, per = PROFILE[stage]
        write, read, out = (f + (per[i] * n if per else 0) for i, f in enumerate(fixed))
        _, pout, pread, pwrite = _price(models[stage])
        usd += (write * pwrite + read * pread + out * pout) / 1e6
        tokens[models[stage]] = tokens.get(models[stage], 0) + round(write + read + out)
    lo, hi = MINUTES[mode]
    if mode == "video":
        lo, hi = round(lo * (0.5 + n / 16)), round(hi * (0.5 + n / 16))
    return {"usd": [round(usd * 0.75, 2), round(usd * 1.6, 2)], "typical_usd": round(usd, 2), "minutes": [lo, hi],
            "tokens": tokens, "budget_usd": float(settings["ma_budget_usd"])}


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
               allow: list[str], deny: list[str] = (), resume: str | None = None, share: int = 1) -> dict:
        self.check()
        if self.remaining() < 0.01:
            raise self.budget_error()
        # Claude Code reports (and caps) a resumed session's cost cumulatively: count only this run's share
        costs = self.state.setdefault("session_costs", {})
        prior = costs.get(resume, 0.0) if resume else 0.0
        r = run_claude(cwd, prompt, system=system, model=model, tools=tools, allow=allow, deny=list(deny),
                       budget_usd=prior + self.remaining() / share, resume=resume, on_start=self.track)
        reported = r["cost_usd"]
        cost = reported - prior if resume and reported >= prior else reported
        u = r.get("usage") or {}
        with self.lock:
            if r.get("session_id"):
                costs[r["session_id"]] = round(prior + cost, 6)
            self.state["runs"].append({
                "stage": stage, "worker": name, "model": model, "cost_usd": round(cost, 4), "resumed": bool(resume),
                "session_id": r.get("session_id"), "duration_s": r.get("duration_s"), "subtype": r.get("subtype"),
                "usage": {k: u.get(k, 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                   "cache_creation_input_tokens")},
            })
            self.state["cost_usd"] = round(self.state["cost_usd"] + cost, 4)
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
        return (f"# Problem\n\nRequested problem: {m.get('problem') or 'not fixed in advance: scenario.md has the one that was used'}\n"
                f"Level: {m.get('level') or 'not specified'}\nFocus: {m.get('focus') or 'none'}\n"
                f"Narration language: {LANGUAGES.get(m['lang'], m['lang'])}\n\n"
                f"# Phases of the round (structure the explainer the same way)\n\n{phases}\n\n"
                f"# The round, as briefed to the interviewer (context only)\n\n{pack['body']}\n")

    def emphasis(self) -> str:
        """Which areas get more time, built in code from the pack's rubric and the scores: never free text from
        the evaluation (it has the verdict, quotes and personal details)."""
        try:
            scores = json.loads((self.sdir / "feedback.json").read_text()).get("scores") or {}
        except (OSError, ValueError, AttributeError):
            scores = {}
        rubric = list(self.meta.get("rubric") or pack_snapshot(self.meta).get("rubric") or scores)
        weak = sorted((k for k in rubric if not isinstance(scores.get(k), (int, float)) or scores[k] <= 3),
                      key=lambda k: scores[k] if isinstance(scores.get(k), (int, float)) else 3.5)  # unobserved after low
        out = ["# Areas to cover", "Cover every area below; give the first list the most explanation time.", ""]
        if weak:
            out += ["## Explain in depth", *[f"- {k}" for k in weak], ""]
        out += ["## Cover clearly", *[f"- {k}" for k in rubric if k not in weak]]
        return "\n".join(out).rstrip() + "\n"

    OBSERVATION = re.compile(r"observ|timeline|progress|signal|assess|evaluat|so far|strength|weak|rubric|verdict|"
                             r"did well|went well|missed|struggl|impression|summary|log\b", re.I)

    def scenario(self) -> str:
        """The problem as it was posed: the opening prompt plus the scenario part of the interviewer's notes
        (its hidden context and numbers), never the observations about the candidate."""
        opening = next((m["text"] for m in self.session_messages() if m.get("role") == "interviewer"
                        and m.get("kind") == "speech" and m.get("text")), "")
        notes = ""
        try:
            notes = (self.sdir / "notes.md").read_text()
        except OSError:
            pass
        # only a headed scenario section (the interviewer is asked for "## Scenario"); unheaded notes mix the
        # scenario with remarks about the candidate, so they're not used at all
        sections = re.split(r"(?m)^(?=#{1,3} )", notes)
        scen = [x for x in sections if re.match(r"#{1,3} .*(scenario|problem|hidden|context)", x, re.I)
                and not self.OBSERVATION.search(x.splitlines()[0])]
        kept = []
        for line in "".join(scen).splitlines()[1:] if scen else []:
            if line.lstrip().startswith("#"):
                continue
            if (re.search(r"\b\d{1,2}:\d{2}\b|\bcandidate|\b(they|he|she|their|his|her|my guess|guess|seems?|seemed|"
                          r"works? at|worked at|background|resume|cv|nervous|confident|senior-ish|junior-ish)\b", line, re.I)
                    or self.OBSERVATION.search(line) or sc.check_text(line)):
                continue  # timestamps and remarks about the person
            kept.append(line)
        out = ["# Scenario", ""]
        if opening:
            out += ["Opening prompt, as it was posed:", f"> {opening.strip()}", ""]
        if "".join(kept).strip():
            out += ["Hidden context and numbers:", "\n".join(kept).strip()]
        return "\n".join(out).rstrip() + "\n"

    def session_messages(self) -> list[dict]:
        try:
            return json.loads((self.sdir / "messages.json").read_text())
        except (OSError, ValueError):
            return []

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
            language_name=LANGUAGES.get(self.meta["lang"], self.meta["lang"]), company_rule=rule,
            inputs="\n".join(f"- `{name}`: {what}" for name, what in SCRIPT_INPUTS.items()),
            output=str(self.script_dir() / "script.json")))

    def script_dir(self) -> Path:
        return (self.dir / "script").resolve()

    def review_items(self) -> list[str]:
        """What a person should look at before publishing: soft check hits in the script and on screen, and
        review findings about the narration that aren't tied to a scene."""
        items = [f"Script: {w}" for w in self.state.get("warnings") or []]
        items += [f"On screen in {sid}: {w}" for sid, ws in (self.state.get("scene_warnings") or {}).items() for w in ws]
        items += [f"Review: {i['issue']}" + (f" (suggested: {i['fix']})" if i.get("fix") else "")
                  for i in (self.state.get("qa") or {}).get("narration", [])]
        return items

    def script(self) -> dict:
        return json.loads((self.dir / "script.json").read_text())

    # ---------------------------------------------------------------- stages
    def check_script(self, d: Path) -> tuple[list[str], list[str]]:
        """(problems that must be fixed, warnings worth a second look)."""
        try:
            data = json.loads((d / "script.json").read_text())
        except OSError:
            return [f"the script wasn't written to {d / 'script.json'}"], []
        except ValueError as e:
            return [f"script.json is not valid JSON: {e}"], []
        problems = sc.validate(data, int(self.settings["ma_scenes"]))
        if problems:
            return problems, []
        return ([f"publishing rule: {v}" for v in sc.neutrality(data, self.company())],
                [f"check: {v}" for v in sc.neutrality(data, self.company(), hard=False)])

    def stage_script(self):
        d = self.script_dir()
        shutil.rmtree(d, ignore_errors=True)  # exactly the inputs, nothing left from an earlier run
        d.mkdir(parents=True)
        inputs = {"brief.md": self.brief(), "scenario.md": self.scenario(), "emphasis.md": self.emphasis(),
                  "format.md": (PROMPTS_X / "format.md").read_text()}
        assert set(inputs) == set(SCRIPT_INPUTS)
        for name, text in inputs.items():
            (d / name).write_text(text)
        out = d / "script.json"
        system, model = self.script_prompt(), self.settings["ma_script_model"]
        tools = ["Read", "Write"]
        allow = [path_rule("Read", d, "**"), path_rule("Edit", d / "script.json")]
        r = self.worker("script", "script", d, f"Write the script to {out} now.", system, model, tools, allow)
        problems, warnings = self.check_script(d)
        if problems or warnings:  # one revision, shared by both kinds
            self.stage("script", detail=f"{len(problems)} problem(s), {len(warnings)} warning(s): one revision")
            msg = f"Revise the script and write it again to exactly {out} (that path, nowhere else), then reply with one line."
            if problems:
                msg += "\nFix every problem below:\n" + "\n".join(f"- {p}" for p in problems[:40])
            if warnings:
                msg += ("\nReword these if they refer to a person, a practice round or someone's attempt (leave them only "
                        "if they are plainly technical):\n" + "\n".join(f"- {w}" for w in warnings[:20]))
            self.worker("script", "script revision", d, msg, system, model, tools, allow, resume=r.get("session_id"))
            problems, warnings = self.check_script(d)
            if problems:
                raise StageError("The script still breaks the rules after one revision:\n"
                                 + "\n".join(f"- {p}" for p in problems[:12]))
        self.state["warnings"] = warnings  # shown in the tab and script.md, and checked by the QA pass
        data = json.loads((d / "script.json").read_text())
        (self.dir / "script.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
        (self.dir / "script.md").write_text(sc.markdown(data, None, self.review_items()))
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
        (self.dir / "diagram.png").unlink(missing_ok=True)
        outputs.pop("diagram_png", None)
        reason = renderer_ready()
        if not reason:
            try:
                run_node(self.dir, ["svg", "diagram.svg", "diagram.png"], env=self.node_env(), on_start=self.track)
                outputs["diagram_png"] = "diagram.png"
                return
            except StageError as e:
                self.check()
                reason = str(e).splitlines()[0][:200]
        self.stage("diagram", detail=f"SVG only (no PNG: {reason})")

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
        self.write_protected("timings.json", json.dumps(timings, indent=1, ensure_ascii=False))
        total = sum(s["duration"] for s in timings.values())
        self.state["duration_s"] = round(total, 1)
        self.stage("voice", detail=f"{sc.clock(total)} of narration")

    def timings(self) -> dict:
        return json.loads((self.work / "timings.json").read_text())

    # Files the scene workers run or read but must never change (also made read-only on disk)
    PROTECTED = ("render.mjs", "lib.js", "player.html", "reference.js", "BRIEF.md", "script.json", "timings.json",
                 ".render_limits.json", ".render_counts.json")

    def write_protected(self, name: str, text: str):
        f = self.work / name
        if f.exists():
            f.chmod(0o644)
        f.write_text(text)
        f.chmod(0o444)

    def prepare_work(self):
        (self.work / "scenes").mkdir(parents=True, exist_ok=True)
        (self.work / "jobs").mkdir(exist_ok=True)
        for f in ("BRIEF.md", "lib.js", "reference.js", "player.html"):
            self.write_protected(f, (EXPLAINER / f).read_text())
        self.write_protected("render.mjs", SHIM.replace("RENDERER_URL", json.dumps((EXPLAINER / "render.mjs").as_uri())))
        self.write_protected("script.json", (self.dir / "script.json").read_text())
        self.write_protected("timings.json", (self.work / "timings.json").read_text())

    def grant_renders(self, scene_ids: list[str], rounds: int):
        """Lets the shim run `node render.mjs beats|preview <id>` `rounds` more times for these scenes."""
        def load(name):
            try:
                return json.loads((self.work / name).read_text())
            except (OSError, ValueError):
                return {}
        counts, limits = load(".render_counts.json"), load(".render_limits.json")
        for i in scene_ids:
            limits[i] = counts.get(i, 0) + rounds
        self.write_protected(".render_limits.json", json.dumps(limits))

    def scene_system(self) -> str:
        return (PROMPTS_X / "scene.md").read_text()

    SCENE_TOOLS = ["Read", "Write", "Edit", "Bash"]

    def scene_rules(self) -> tuple[list[str], list[str]]:
        w = self.work
        allow = [path_rule("Read", w, "**"), path_rule("Edit", w / "scenes", "**"),
                 "Bash(node render.mjs beats:*)", "Bash(node render.mjs preview:*)"]
        deny = [path_rule(t, w / f) for f in self.PROTECTED for t in ("Write", "Edit")]
        return allow, deny

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
        self.grant_renders([s["id"] for s in missing], 2)  # at most 2 still-render rounds per scene

        errors: list[Exception] = []
        done = threading.Event()

        def one(name: str, scene_ids: list[str]):
            prompt = (f"Your scenes: {', '.join(scene_ids)}. Your job file: jobs/{name}.json. Read BRIEF.md first, then "
                      "follow it: write each scene, check it with `node render.mjs beats <id>` (at most 2 rounds per "
                      "scene), then report.")
            try:
                r = self.worker("scenes", name, self.work, prompt, self.scene_system(), self.settings["ma_scene_model"],
                                self.SCENE_TOOLS, *self.scene_rules(), share=len(jobs))
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

    def scene_flags(self, hard: bool = True) -> dict[str, list[str]]:
        """On-screen strings in the scene sources that break the publishing rules (deterministic check): the
        hard ones, or with hard=False the ones worth a second look."""
        flags = {}
        for f in sorted((self.work / "scenes").glob("*.js")):
            found = [f'"{s[:80]}": {msg}' for s in sc.scene_literals(f.read_text())
                     for h, msg in sc.findings(s, self.company()) if h == hard]
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
        found = [self.work / "out" / "qa" / "qa.json"]
        for f in found:
            f.unlink(missing_ok=True)
        allow = [path_rule("Read", self.work / "out" / "qa", "**"), path_rule("Edit", self.work / "out" / "qa" / "qa.json")]
        deny = [path_rule("Read", self.work / "scenes", "**")]
        warnings = list(self.state.get("warnings") or []) + [
            f"{sid}: {w}" for sid, ws in self.scene_flags(hard=False).items() for w in ws]
        prompt = "Review the frames now."
        if warnings:
            prompt += ("\nA text check flagged these phrases for a second look. Report one as an issue (kind neutrality) "
                       "only if it really refers to a person, a practice round or someone's attempt:\n"
                       + "\n".join(f"- {w}" for w in warnings[:30]))
        self.worker("qa", "qa", self.work, prompt, (PROMPTS_X / "qa.md").read_text(),
                    self.settings["ma_qa_model"], ["Read", "Write"], allow, deny)
        try:
            qa = json.loads(next((f for f in found if f.exists()), found[0]).read_text())
        except (OSError, ValueError) as e:
            raise StageError(f"The QA pass didn't write a valid qa.json ({e.__class__.__name__}).") from e
        issues, narration = [], []
        for i in qa.get("issues", []) if isinstance(qa, dict) else []:
            if not isinstance(i, dict):
                continue
            if i.get("scene") not in ids:  # about the narration as a whole: a person reviews it before publishing
                narration.append({"issue": str(i.get("issue", ""))[:400], "fix": str(i.get("fix", ""))[:400],
                                  "severity": str(i.get("severity", ""))[:10], "kind": str(i.get("kind", ""))[:20]})
                continue
            issues.append({"scene": i["scene"], "t": i.get("t"),
                           "severity": i.get("severity") if i.get("severity") in ("high", "medium", "low") else "medium",
                           "kind": i.get("kind") or "other", "issue": str(i.get("issue", ""))[:400],
                           "fix": str(i.get("fix", ""))[:400]})
            # the fix round only edits scenes: the recorded narration can't change, so a person has to look
            if issues[-1]["kind"] == "neutrality" or NARRATION.search(issues[-1]["issue"]):
                narration.append({k: issues[-1][k] for k in ("issue", "fix", "severity", "kind")}
                                 | {"issue": f"{i['scene']}: {issues[-1]['issue']}"})
        for sid, flagged in (self.state.get("scene_flags") or {}).items():
            for f in flagged:
                issues.append({"scene": sid, "t": None, "severity": "high", "kind": "neutrality",
                               "issue": f"On-screen text breaks the publishing rules: {f}",
                               "fix": "Reword it as a neutral explanation of the topic."})
        try:  # counted here, not taken from the model's own summary
            frames = len(json.loads((self.work / "out" / "qa" / "index.json").read_text()))
        except (OSError, ValueError):
            frames = 0
        counts = {s: sum(i["severity"] == s for i in issues) for s in ("high", "medium", "low")}
        found = ", ".join(f"{v} {k}" for k, v in counts.items() if v) or "no issues"
        self.state["scene_warnings"] = self.scene_flags(hard=False)
        self.state["qa"] = {"issues": issues, "narration": narration, "frames": frames,
                            "summary": f"{frames} frames reviewed: {found}."}
        self.stage("qa", detail=found)

    def stage_fixes(self):
        issues = [i for i in (self.state.get("qa") or {}).get("issues", [])
                  if i["severity"] in FIX_SEVERITIES or i["kind"] == "neutrality"]
        used, rounds = self.state.get("fix_rounds_used", 0), int(self.settings["ma_fix_rounds"])
        if not issues or used >= rounds:  # one fix round in total, retries included
            self.finish_fixes(issues, "nothing to fix" if not issues else
                              "turned off in settings" if rounds < 1 else "the fix round was already used")
            return

        workers = self.state.get("workers", {})
        owner = {s: name for name, w in workers.items() for s in w["scenes"]}
        by_worker: dict[str, list[dict]] = {}
        done_workers = set(self.state.get("fix_done") or [])  # a retry skips the workers that already fixed theirs
        for i in issues:
            name = owner.get(i["scene"], "w1")
            if name not in done_workers:
                by_worker.setdefault(name, []).append(i)
        errors: list[Exception] = []

        def one(name: str, items: list[dict]):
            scenes = sorted({i["scene"] for i in items})
            lines = "\n".join(f"- {i['scene']}" + (f" at {i['t']}s" if i.get("t") is not None else "")
                              + f" ({i['severity']}, {i['kind']}): {i['issue']} Fix: {i['fix']}" for i in items)
            msg = (f"A review of the rendered frames found these problems in {', '.join(scenes)}. Fix them by editing only "
                   f"those scene files; one `node render.mjs beats <id>` check per scene is allowed. Then reply with one "
                   f"line.\n{lines}")
            w = workers.get(name) or {}
            if not w.get("session_id"):
                msg = f"Read BRIEF.md and jobs/{name}.json first (your scenes already exist in scenes/).\n" + msg
            try:
                self.worker("fixes", f"{name} fixes", self.work, msg, self.scene_system(), self.settings["ma_scene_model"],
                            self.SCENE_TOOLS, *self.scene_rules(), resume=w.get("session_id"), share=len(by_worker))
                with self.lock:
                    self.state.setdefault("fix_done", []).append(name)
                self.save()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        self.stage("fixes", detail=f"{len(issues)} issue(s) in {len({i['scene'] for i in issues})} scene(s)")
        self.prepare_work()
        self.grant_renders(sorted({i["scene"] for items in by_worker.values() for i in items}), 1)
        threads = [threading.Thread(target=one, args=(n, items), daemon=True) for n, items in by_worker.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for kind in (Cancelled, BudgetExceeded, Exception):
            e = next((e for e in errors if isinstance(e, kind)), None)
            if e:
                raise e  # the round isn't used up: a retry runs it again
        self.state["fix_rounds_used"] = used + 1
        self.save()
        self.state["qa"]["fixed"] = len(issues)
        self.finish_fixes(issues, f"{len(issues)} issue(s) sent back")

    def finish_fixes(self, issues: list[dict], detail: str):
        """After the (single) fix round: publishing check on the scene text, then re-render what changed."""
        flags = self.scene_flags()
        self.state["scene_flags"] = flags
        self.state["scene_warnings"] = self.scene_flags(hard=False)
        if flags:
            raise StageError("Scene text still breaks the publishing rules after the fixes:\n"
                             + "\n".join(f"- {k}: {v[0]}" for k, v in flags.items()))
        todo = self.render_changed("fixes")
        self.stage("fixes", detail=f"{detail}; {len(todo)} scene(s) re-rendered" if todo else detail)

    def stage_package(self):
        data = self.script()
        outputs = self.state.setdefault("outputs", {})
        diagram = "diagram.svg" if (self.dir / "diagram.svg").exists() else None
        self.state["review"] = self.review_items()
        (self.dir / "script.md").write_text(sc.markdown(data, diagram, self.state["review"]))
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


# A QA issue about what is said (not drawn): only a person can change the recorded narration
NARRATION = re.compile(r"narrat|voice|spoken|says\b|said\b|subtitle|audio|wording|sentence", re.I)


# The work folder's render.mjs: scene workers may only run it, and only within their render rounds.
SHIM = """// Runs the shared renderer on this folder (see BRIEF.md), within the render rounds each scene is allowed.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const [mode, id] = process.argv.slice(2);
if (mode === "beats" || mode === "preview") {
  const read = (f) => (existsSync(join(here, f)) ? JSON.parse(readFileSync(join(here, f), "utf8")) : {});
  const limits = read(".render_limits.json"), counts = read(".render_counts.json");
  if ((counts[id] || 0) >= (limits[id] || 0)) {
    console.error(`Render limit reached for ${id}: ${limits[id] || 0} round(s) allowed. Finish without rendering it again.`);
    process.exit(3);
  }
  counts[id] = (counts[id] || 0) + 1;
  writeFileSync(join(here, ".render_counts.json"), JSON.stringify(counts));
}
process.env.EXPLAINER_WORK = here;
await import(RENDERER_URL);
"""


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
