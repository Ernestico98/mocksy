"""Per-turn latency: from the end of the candidate's speech to the interviewer's first audio.

A `Turn` collects wall-clock marks (time.time(); the browser sends Date.now() / 1000, which
matches on one machine) and turns them into a stage breakdown in milliseconds:

    vad ─upload→ received ─stt→ stt_done ─handoff→ sent ─model→ first_text
        ─chunk→ first_chunk ─tts→ tts_ready ─play→ played

`silence_ms` is the pause the browser waited before deciding the turn was over, so what the
candidate perceives is silence_ms + total_ms. Typed turns start at `received`.
"""

import json
import time

STAGES = [
    ("upload", "vad", "received"),          # browser → server (includes encoding the WAV)
    ("stt", "received", "stt_done"),        # Whisper
    ("handoff", "stt_done", "sent"),        # back to the browser, whiteboard sync, /say, queueing
                                            # (typed turns: from `received`)
    ("model", "sent", "first_text"),        # Claude: time to the first text token
    ("chunk", "first_text", "first_chunk"),  # waiting for the first speakable chunk
    ("tts", "first_chunk", "tts_ready"),    # Kokoro synthesis of that chunk
    ("play", "tts_ready", "played"),        # SSE → browser fetch → playback starts
]


class Turn:
    def __init__(self, kind: str, marks: dict[str, float] | None = None, silence_ms: float | None = None):
        self.kind = kind
        self.marks: dict[str, float] = {k: v for k, v in (marks or {}).items() if v}
        self.silence_ms = silence_ms
        self.reply_id: int | None = None
        self.chunk_chars: int | None = None
        self.tools = 0          # tool calls before the first spoken chunk
        self.logged = False

    def mark(self, name: str, at: float | None = None):
        self.marks.setdefault(name, at or time.time())

    def has(self, name: str) -> bool:
        return name in self.marks

    def summary(self) -> dict:
        out: dict = {"kind": self.kind}
        if self.silence_ms is not None:
            out["silence_ms"] = round(self.silence_ms)
        for name, a, b in STAGES:
            if a == "stt_done" and a not in self.marks:
                a = "received"
            if a in self.marks and b in self.marks:
                out[f"{name}_ms"] = round((self.marks[b] - self.marks[a]) * 1000)
        start = self.marks.get("vad") or self.marks.get("received")
        end = self.marks.get("played") or self.marks.get("tts_ready")
        if start and end:
            out["total_ms"] = round((end - start) * 1000)
            out["total_to"] = "played" if "played" in self.marks else "tts_ready"
        if self.reply_id is not None:
            out["reply_id"] = self.reply_id
        if self.chunk_chars is not None:
            out["first_chunk_chars"] = self.chunk_chars
        if self.tools:
            out["tools_before_speech"] = self.tools
        return out

    def log(self, session_id: str, message_id: int):
        self.logged = True
        print("[latency]", json.dumps({"session": session_id, "message": message_id, **self.summary()}), flush=True)
