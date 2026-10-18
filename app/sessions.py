"""Interview sessions: state on disk, the live interviewer, TTS and evaluation."""

import base64
import json
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Callable

import soundfile as sf

from . import speech
from .config import PROMPTS, SESSIONS, get_pack, load_settings
from .interviewer import Interviewer

LANGUAGES = {"en": "English", "es": "Spanish"}

# How long the candidate must draw without talking before the interviewer may chime in.
SILENT_DRAWING_S = 25
SILENT_PROMPT_COOLDOWN_S = 60


def fmt(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class Session:
    def __init__(self, path: Path, broadcast: Callable[[dict], None]):
        self.dir = path
        self.id = path.name
        self.broadcast_raw = broadcast
        self.lock = threading.RLock()
        self.meta = json.loads((path / "meta.json").read_text())
        mpath = path / "messages.json"
        self.messages: list[dict] = json.loads(mpath.read_text()) if mpath.exists() else []
        self.next_id = max((m["id"] for m in self.messages), default=0) + 1
        self.interviewer: Interviewer | None = None
        self.current: dict | None = None      # interviewer message being generated
        self.pending: list[str] = []          # context to prepend to the next candidate message
        self.end_requested = False
        self.tts_gen = 0
        self.canvas_lines: list[str] = []
        self.canvas_sent: Counter = Counter()
        self.last_canvas_change = 0.0
        self.last_candidate = time.time()
        self.last_silent_prompt = 0.0
        self.last_turn_end = time.time()
        self.candidate_speaking = False
        if self.meta.get("status") == "live":  # server restarted mid-interview
            threading.Thread(target=self.watchdog, daemon=True).start()

    # ---------------------------------------------------------------- basics
    def broadcast(self, event: dict):
        self.broadcast_raw({**event, "session": self.id})

    def save(self):
        (self.dir / "meta.json").write_text(json.dumps(self.meta, indent=2, ensure_ascii=False))
        (self.dir / "messages.json").write_text(json.dumps(self.messages, indent=1, ensure_ascii=False))

    def add(self, role: str, kind: str, text: str, **extra) -> dict:
        with self.lock:
            msg = {"id": self.next_id, "role": role, "kind": kind, "text": text,
                   "ts": time.time(), **extra}
            self.next_id += 1
            self.messages.append(msg)
            self.save()
        self.broadcast({"type": "message", "message": msg})
        return msg

    def update(self, msg: dict):
        self.save()
        self.broadcast({"type": "message", "message": msg})

    def public(self) -> dict:
        fb = self.dir / "feedback.json"
        return {"meta": self.meta, "messages": self.messages,
                "feedback": json.loads(fb.read_text()) if fb.exists() else None}

    @property
    def lang(self) -> str:
        return self.meta["lang"]

    # ---------------------------------------------------------------- clock
    def clock(self) -> str:
        now = time.time()
        total = now - self.meta.get("started_at", now)
        ph = self.meta.get("phase") or {}
        phase = next((p for p in self.meta["phases"] if p["key"] == ph.get("key")), None)
        if not phase:
            return f"{fmt(total)} total"
        return f"{fmt(total)} total · {phase['key']} {fmt(now - ph['started_at'])}/{phase['minutes']:02d}:00"

    def set_phase(self, key: str, by: str):
        if key not in [p["key"] for p in self.meta["phases"]]:
            return
        if (self.meta.get("phase") or {}).get("key") == key:
            return
        self.meta["phase"] = {"key": key, "started_at": time.time()}
        label = next(p["label"] for p in self.meta["phases"] if p["key"] == key)
        self.add("event", "phase", label, by=by)
        if by == "candidate":
            self.pending.append(f"[phase] The candidate moved to the {key} phase.")
        self.broadcast({"type": "meta", "meta": self.meta})

    # ---------------------------------------------------------------- interviewer
    def system_prompt(self) -> str:
        return render(PROMPTS / "interviewer.md", self.meta)

    def ensure_interviewer(self):
        if self.interviewer and self.interviewer.alive:
            return
        self.interviewer = Interviewer(
            self.dir, self.meta["model"], self.system_prompt(),
            self.meta.get("claude_session_id"), self.on_interviewer)

    def start(self):
        self.meta["started_at"] = time.time()
        self.meta["status"] = "live"
        first = self.meta["phases"][0]["key"]
        self.meta["phase"] = {"key": first, "started_at": time.time()}
        self.save()
        self.ensure_interviewer()
        self.status("preparing")
        self.interviewer.send("[system] start")
        threading.Thread(target=self.watchdog, daemon=True).start()

    def status(self, state: str, detail: str = ""):
        self.broadcast({"type": "status", "state": state, "detail": detail})

    def on_interviewer(self, kind: str, data: dict):
        val = data.get("value")
        with self.lock:
            if kind == "turn_start":
                self.current = None
                self.status("thinking")
            elif kind == "speech":
                msg = self._current()
                msg["chunks"].append(val)
                msg["text"] = " ".join(msg["chunks"])
                if not msg.get("interrupted"):
                    tts_queue.put((self, msg["id"], len(msg["chunks"]) - 1, val, self.tts_gen))
                self.update(msg)
            elif kind == "note":
                self.add("interviewer", "note", val)
            elif kind == "phase":
                self.set_phase(val, by="interviewer")
            elif kind == "end":
                self.end_requested = True
            elif kind == "tool":
                self.status("tool", val or "")
            elif kind == "session":
                if self.meta.get("claude_session_id") != val:
                    self.meta["claude_session_id"] = val
                    self.save()
            elif kind == "turn_end":
                self.last_turn_end = time.time()
                if val:
                    self.meta["cost_usd"] = round(self.meta.get("cost_usd", 0) + val, 4)
                if self.current:
                    self.current["done"] = True
                    self.update(self.current)
                self.current = None
                self.status("idle")
                if self.end_requested:
                    threading.Thread(target=self.finish, daemon=True).start()
            elif kind == "error":
                self.status("error", val)
            elif kind == "exit":
                if self.meta.get("status") == "live" and not self.end_requested:
                    self.status("error", "The interviewer stopped; it will resume with your next message.")

    def _current(self) -> dict:
        if not self.current:
            self.current = self.add("interviewer", "speech", "", chunks=[], audio={})
        return self.current

    # ---------------------------------------------------------------- candidate input
    def say(self, text: str, kind: str = "speech"):
        text = text.strip()
        if not text or self.meta.get("status") != "live":
            return
        self.add("candidate", kind, text)
        self.last_candidate = time.time()
        header = "[candidate typed" if kind == "typed" else "[candidate"
        parts = self.pending + self._canvas_diff()
        self.pending = []
        parts.append(f"{header} · {self.clock()}]\n{text}")
        self.ensure_interviewer()
        self.interviewer.send("\n\n".join(parts))

    def ask_to_look(self, text: str):
        self.add("candidate", "look", text or "(asks you to look at the whiteboard)")
        self.last_candidate = time.time()
        parts = self.pending + self._canvas_diff(force=True)
        self.pending = []
        parts.append(f"[candidate · {self.clock()}]\n{text or 'Can you take a look at my whiteboard?'}\n"
                     "(They explicitly asked you to look: Read canvas.png before answering.)")
        self.ensure_interviewer()
        self.interviewer.send("\n\n".join(parts))

    def interrupt(self, message_id: int, heard_index: int):
        with self.lock:
            self.tts_gen += 1
            msg = next((m for m in self.messages if m["id"] == message_id), None)
            if not msg or msg.get("interrupted"):
                return
            heard = " ".join(msg.get("chunks", [])[: heard_index + 1])
            msg["interrupted"] = True
            msg["heard"] = heard
            self.update(msg)
            self.pending.append(f'[interrupted] The candidate started talking while you were speaking. '
                                f'They heard: "{heard}"')

    # ---------------------------------------------------------------- canvas
    def update_canvas(self, lines: list[str], png: str | None, scene: str | None):
        if png:
            (self.dir / "canvas.png").write_bytes(base64.b64decode(png.split(",", 1)[-1]))
        if scene:
            (self.dir / "canvas.excalidraw").write_text(scene)
        if Counter(lines) != Counter(self.canvas_lines):
            self.canvas_lines = lines
            self.last_canvas_change = time.time()

    def _canvas_diff(self, force: bool = False) -> list[str]:
        cur = Counter(self.canvas_lines)
        added, removed = cur - self.canvas_sent, self.canvas_sent - cur
        if not added and not removed and not force:
            return []
        self.canvas_sent = cur
        if (self.dir / "canvas.png").exists():
            snaps = self.dir / "sketches"
            snaps.mkdir(exist_ok=True)
            shutil.copy(self.dir / "canvas.png", snaps / f"{int(time.time())}.png")
        lines = [f"+ {l}" for l in added.elements()] + [f"- {l}" for l in removed.elements()]
        if len(lines) > 40:
            lines = lines[:40] + [f"... and {len(lines) - 40} more changes (Read canvas.png for the full picture)"]
        body = "\n".join(lines) if lines else "(no changes since you last looked)"
        return [f"[canvas] Whiteboard changes since you last heard (full drawing: canvas.png):\n{body}"]

    def watchdog(self):
        """Lets the interviewer react while the candidate draws in silence."""
        while self.meta.get("status") == "live":
            time.sleep(4)
            self.maybe_prompt_silent_drawing(time.time())

    def maybe_prompt_silent_drawing(self, now: float) -> bool:
        it = self.interviewer
        if not it or it.busy or self.candidate_speaking:
            return False
        if Counter(self.canvas_lines) == self.canvas_sent:
            return False  # nothing new on the board
        if (now - self.last_canvas_change < 6 or now - self.last_candidate < SILENT_DRAWING_S
                or now - self.last_turn_end < SILENT_DRAWING_S
                or now - self.last_silent_prompt < SILENT_PROMPT_COOLDOWN_S):
            return False
        self.last_silent_prompt = now
        quiet = fmt(now - max(self.last_candidate, self.last_turn_end))
        parts = self.pending + self._canvas_diff()
        self.pending = []
        parts.append(f"[drawing silently · {self.clock()}] The candidate has been drawing without "
                     f"speaking for {quiet}. Reply <silent/> unless a real interviewer would say something now.")
        it.send("\n\n".join(parts))
        return True

    # ---------------------------------------------------------------- end + evaluation
    def stop(self):
        """End the interview without evaluating it (it can still be evaluated later)."""
        with self.lock:
            if self.meta.get("status") != "live":
                return
            self.meta["status"] = "ended"
            self.meta["ended_at"] = time.time()
            self.tts_gen += 1  # drop speech still queued
            self.save()
        if self.interviewer:
            self.interviewer.stop()
        self.broadcast({"type": "meta", "meta": self.meta})
        self.status("idle")

    def finish(self):
        with self.lock:
            if self.meta.get("status") != "live":
                return
            self.meta["status"] = "evaluating"
            self.meta.setdefault("ended_at", time.time())  # keep it when evaluating a stopped session
            self.save()
        self.broadcast({"type": "meta", "meta": self.meta})
        time.sleep(1)  # let the closing line finish synthesizing
        if self.interviewer:
            self.interviewer.stop()
        self.status("evaluating")
        try:
            fb = evaluate(self)
            (self.dir / "feedback.json").write_text(json.dumps(fb, indent=2, ensure_ascii=False))
            self.meta["verdict"] = fb.get("verdict")
            self.meta["status"] = "done"
        except Exception as e:  # keep the session usable; evaluation can be retried
            print("evaluation failed:", e)
            self.meta["status"] = "ended"
            self.status("error", f"Couldn't generate the evaluation: {e}")
        self.save()
        self.broadcast({"type": "meta", "meta": self.meta})
        self.broadcast({"type": "feedback", "feedback": self.public()["feedback"]})
        self.status("idle")


# -------------------------------------------------------------------- helpers

class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render(template: Path, meta: dict, lang: str | None = None) -> str:
    pack = get_pack(meta["pack"])
    phases = ", ".join(f"{p['label']} ({p['key']}, {p['minutes']} min)" for p in meta["phases"])
    values = SafeDict(
        pack=pack["body"], company=meta.get("company") or "none (generic)",
        level=meta.get("level") or "not specified", focus=meta.get("focus") or "none",
        problem=meta.get("problem") or "none (choose one as described above)",
        rubric=", ".join(f'"{r}"' for r in meta.get("rubric") or pack.get("rubric", [])),
        phases=phases, phase_keys=", ".join(p["key"] for p in meta["phases"]),
        language_name=LANGUAGES.get(lang or meta["lang"], lang or meta["lang"]),
    )
    return template.read_text().format_map(values)


def transcript_markdown(s: Session) -> str:
    start = s.meta.get("started_at") or (s.messages[0]["ts"] if s.messages else 0)
    out = [f"# Transcript ({s.meta['pack_title']})\n"]
    for m in s.messages:
        who = {"interviewer": "Interviewer", "candidate": "Candidate"}.get(m["role"], "—")
        extra = " (interrupted; candidate heard: " + m["heard"] + ")" if m.get("interrupted") else ""
        out.append(f"#{m['id']} [{fmt(m['ts'] - start)}] {who} ({m['kind']}): {m['text']}{extra}")
    return "\n".join(out)


def evaluate(s: Session) -> dict:
    (s.dir / "transcript.md").write_text(transcript_markdown(s))
    prompt = render(PROMPTS / "feedback.md", s.meta, load_settings()["feedback_language"])
    # Read can't list directories, so name the whiteboard images explicitly
    images = sorted(str(p.relative_to(s.dir)) for p in (s.dir / "sketches").glob("*.png"))
    if (s.dir / "canvas.png").exists():
        images.append("canvas.png")
    request = "Write the evaluation now."
    if images:
        request += "\nWhiteboard images, oldest first (canvas.png is the final board): " + ", ".join(images)
    r = subprocess.run(
        ["claude", "-p", request, "--output-format", "json",
         "--model", s.meta["feedback_model"], "--system-prompt", prompt,
         "--tools", "Read", "--setting-sources", "", "--strict-mcp-config"],
        cwd=s.dir, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        try:  # errors such as "Not logged in" come back as JSON on stdout
            reason = json.loads(r.stdout).get("result")
        except ValueError:
            reason = None
        raise RuntimeError(reason or r.stderr.strip()[-300:] or "claude exited with an error")
    text = json.loads(r.stdout)["result"]
    m = re.search(r"```json\s*(.*?)```", text, flags=re.S)
    data = json.loads(m.group(1)) if m else {}
    data["report"] = text[m.end():].strip() if m else text
    (s.dir / "feedback.md").write_text(data["report"])
    return data


def create_session(pack_id: str, company: str, level: str, focus: str, lang: str,
                   model: str | None, feedback_model: str | None,
                   broadcast: Callable[[dict], None], problem: str = "") -> Session:
    pack = get_pack(pack_id)
    settings = load_settings()
    slug = re.sub(r"[^a-z0-9]+", "-", (company or pack_id).lower()).strip("-")[:24]
    base = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"
    path, n = SESSIONS / base, 2
    while path.exists():  # two sessions in the same second (double click, tests)
        path, n = SESSIONS / f"{base}-{n}", n + 1
    (path / "audio").mkdir(parents=True)
    meta = {
        "id": path.name, "created": time.time(), "status": "setup",
        "pack": pack_id, "pack_title": pack["title"], "company": company or pack.get("company", ""),
        "level": level, "focus": focus, "problem": problem, "lang": lang, "phases": pack["phases"],
        "rubric": pack.get("rubric", []),
        "model": model or settings["model"], "feedback_model": feedback_model or settings["feedback_model"],
    }
    (path / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return Session(path, broadcast)


# -------------------------------------------------------------------- TTS worker

tts_queue: "queue.Queue[tuple]" = queue.Queue()


def _tts_worker():
    while True:
        s, msg_id, index, text, gen = tts_queue.get()
        if gen != s.tts_gen:
            continue  # the candidate interrupted this message
        settings = load_settings()
        voice = settings.get(f"voice_{s.lang}") or settings["voice_en"]
        try:
            samples, sr = speech.synthesize(speech.speakable(text), voice, s.lang, settings["speed"])
        except Exception as e:
            print("TTS error:", e)
            continue
        if gen != s.tts_gen:
            continue
        name = f"{msg_id}-{index}.wav"
        sf.write(s.dir / "audio" / name, samples, sr)
        url = f"/files/{s.id}/audio/{name}"
        with s.lock:
            msg = next((m for m in s.messages if m["id"] == msg_id), None)
            if msg is not None:
                msg.setdefault("audio", {})[str(index)] = url
                s.save()
        s.broadcast({"type": "audio", "message_id": msg_id, "index": index, "url": url})


threading.Thread(target=_tts_worker, daemon=True).start()
