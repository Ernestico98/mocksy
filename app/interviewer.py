"""Runs the interviewer as a long-lived Claude Code process (stream-json in/out).

The process lives in the session folder, so its file tools (Read canvas.png,
Write notes.md) are confined there. It does not load CLAUDE.md, settings,
memory or MCP servers: only the tools listed in TOOLS.
"""

import json
import re
import subprocess
import threading
from typing import Callable

TOOLS = ["Read", "Write", "Edit", "WebSearch", "WebFetch"]
SENTENCE_END = re.compile(r"[.!?…:;](?=\s)|\n")
CLAUSE_END = re.compile(r"[,;:—–](?=\s)")
MIN_SENTENCE_CHARS = 25   # shorter sentences ("Okay.") stay with the next one, so audio isn't choppy
MIN_CLAUSE_WORDS = 6      # the first chunk of a reply may end at a comma once it has this many words
# A period after these is not the end of a sentence ("e.g. caching", "Dr. Smith", "p. ej.")
ABBREVIATIONS = {
    "e.g.", "i.e.", "vs.", "dr.", "mr.", "mrs.", "ms.", "prof.", "st.", "approx.", "fig.",
    "sr.", "sra.", "srta.", "dra.", "ej.", "p.", "pág.", "aprox.", "núm.", "ud.", "uds.",
}


def claude_args(model: str, system_prompt: str, resume: str | None = None) -> list[str]:
    args = [
        "claude", "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--include-partial-messages", "--verbose",
        "--model", model,
        "--system-prompt", system_prompt,
        "--tools", *TOOLS,
        "--permission-mode", "acceptEdits",
        "--setting-sources", "",
        "--strict-mcp-config",
    ]
    if resume:
        args += ["--resume", resume]
    return args


class TagStream:
    """Incremental parser for the interviewer's output.

    Emits ("speech", sentence), ("note", text), ("phase", key), ("silent", None), ("end", None).
    """

    def __init__(self, emit: Callable[[str, object], None]):
        self.emit = emit
        self.buf = ""       # raw text not yet classified
        self.speech = ""    # speech text waiting for a sentence boundary
        self.spoken = False  # a chunk was emitted already (the first one may be a clause)

    def feed(self, text: str):
        self.buf += text
        while self.buf:
            lt = self.buf.find("<")
            if lt == -1:
                self._speech(self.buf)
                self.buf = ""
                break
            if lt > 0:
                self._speech(self.buf[:lt])
                self.buf = self.buf[lt:]
            # buf starts with "<"
            m = re.match(r"<(silent|end)\s*/>", self.buf)
            if m:
                self._flush_speech()
                self.emit(m.group(1), None)
                self.buf = self.buf[m.end():]
                continue
            m = re.match(r"<(note|phase)>(.*?)</\1>", self.buf, flags=re.S)
            if m:
                self._flush_speech()
                self.emit(m.group(1), m.group(2).strip())
                self.buf = self.buf[m.end():]
                continue
            if re.match(r"<(?:/?\w*\s*/?|note>.*|phase>.*)$", self.buf, flags=re.S):
                break  # incomplete tag: wait for more text
            self._speech("<")  # a literal "<"
            self.buf = self.buf[1:]

    def _speech(self, text: str):
        self.speech += text
        while True:
            cut = self._cut()
            if cut is None:
                break
            chunk, self.speech = self.speech[:cut], self.speech[cut:]
            self._emit_speech(chunk)

    def _cut(self) -> int | None:
        """Where the next speakable chunk ends: the first full sentence, or for the first chunk
        of a reply, an earlier clause (comma, colon...) so synthesis can start sooner."""
        s = self.speech
        ends = [m for m in SENTENCE_END.finditer(s) if m.group() == "\n" or self._sentence_end(s, m)]
        if not self.spoken:
            ends += [m for m in CLAUSE_END.finditer(s) if self._clause_end(s, m)]
            ends.sort(key=lambda m: m.end())
        return next((m.end() for m in ends), None)

    @staticmethod
    def _sentence_end(s: str, m: re.Match) -> bool:
        if m.end() < MIN_SENTENCE_CHARS:
            return False
        if m.group() == ".":
            word = s[:m.end()].rsplit(None, 1)[-1].lstrip("¿¡(\"'").lower()
            if word in ABBREVIATIONS or re.fullmatch(r"(?:[a-z]\.)+", word):  # "e.g.", "U.S.", "J."
                return False
        return True

    @staticmethod
    def _clause_end(s: str, m: re.Match) -> bool:
        head = s[:m.end()]
        if len(head.split()) < MIN_CLAUSE_WORDS:
            return False
        # never inside an open Spanish question/exclamation or a parenthesis: it would change the intonation
        return (head.count("¿") <= head.count("?") and head.count("¡") <= head.count("!")
                and head.count("(") <= head.count(")"))

    def _emit_speech(self, chunk: str):
        if chunk.strip():
            self.spoken = True
            self.emit("speech", chunk.strip())

    def _flush_speech(self):
        self._emit_speech(self.speech)
        self.speech = ""

    def close(self):
        if self.buf:
            self._speech(re.sub(r"<[^>]*$", "", self.buf))
            self.buf = ""
        self._flush_speech()


class Interviewer:
    """One Claude Code process per live session. Thread-safe `send`."""

    def __init__(self, cwd, model: str, system_prompt: str, resume: str | None,
                 on_event: Callable[[str, dict], None]):
        self.on_event = on_event
        self.lock = threading.Lock()
        self.busy = False
        self.queue: list[str] = []
        self.session_id = resume
        self.proc = subprocess.Popen(
            claude_args(model, system_prompt, resume), cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self.parser: TagStream | None = None
        self.got_text = False
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    # ---------------------------------------------------------- input
    def send(self, text: str):
        with self.lock:
            if self.busy:
                self.queue.append(text)
                return
            self._write(text)

    def _write(self, text: str):
        self.busy = True
        self.got_text = False
        self.parser = TagStream(lambda kind, val: self.on_event(kind, {"value": val}))
        self.on_event("turn_start", {})
        msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self.busy = False
            self.on_event("error", {"value": f"interviewer process is not running ({e})"})

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self):
        if self.alive:
            self.proc.terminate()

    # ---------------------------------------------------------- output
    def _read(self):
        for line in self.proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            t = ev.get("type")
            if t == "system" and ev.get("subtype") == "init":
                self.session_id = ev.get("session_id")
                self.on_event("session", {"value": self.session_id})
            elif t == "stream_event":
                e = ev["event"]
                if e.get("type") == "content_block_delta" and e["delta"].get("type") == "text_delta":
                    if not self.got_text:
                        self.got_text = True
                        self.on_event("first_text", {})
                    self.parser.feed(e["delta"]["text"])
                elif e.get("type") == "content_block_start" and e["content_block"].get("type") in ("tool_use", "server_tool_use"):
                    self.on_event("tool", {"value": e["content_block"].get("name")})
                elif e.get("type") == "message_stop":
                    self.parser.close()  # a tool call may follow; speech so far is complete
            elif t == "result":
                self.parser.close()
                self.on_event("turn_end", {"value": ev.get("total_cost_usd")})
                if ev.get("is_error"):  # e.g. "Not logged in · Please run /login"
                    self.on_event("error", {"value": f"Claude Code: {ev.get('result') or 'request failed'}"})
                with self.lock:
                    self.busy = False
                    if self.queue:
                        text, self.queue = "\n\n".join(self.queue), []
                        self._write(text)
        self.on_event("exit", {"value": self.proc.returncode})

    def _drain_stderr(self):
        for line in self.proc.stderr:
            if line.strip():
                print("[claude]", line.rstrip())
