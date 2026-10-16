"""Local mock-interview server.

Candidate speaks in the browser -> Whisper (mlx) transcribes -> message queued.
Claude (the interviewer) long-polls /api/next, replies via /api/interviewer,
and Kokoro speaks the reply sentence by sentence back in the browser.
"""

import asyncio
import base64
import json
import queue
import re
import tempfile
import threading
import time
from pathlib import Path

import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from kokoro_onnx import Kokoro
from pydantic import BaseModel

ROOT = Path(__file__).parent
MODELS = ROOT / "models"
SESSIONS = ROOT / "sessions"
WHISPER_REPO = "mlx-community/whisper-large-v3-turbo"

VOICES = {"en": "af_heart", "es": "em_alex"}
KOKORO_LANG = {"en": "en-us", "es": "es"}

app = FastAPI()


# ---------------------------------------------------------------- state

class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.settings = {"lang": "en", "voice": VOICES["en"], "speed": 1.0}
        self.listeners: set[asyncio.Queue] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        if not self.resume_latest():
            self.new_session()

    def resume_latest(self) -> bool:
        """Pick up the most recent session after a server restart."""
        latest = sorted(SESSIONS.glob("*/transcript.jsonl"))
        if not latest:
            return False
        self.dir = latest[-1].parent
        self.messages = [json.loads(l) for l in latest[-1].read_text().splitlines() if l.strip()]
        self.next_id = max((m["id"] for m in self.messages), default=0) + 1
        return True

    def new_session(self):
        self.messages: list[dict] = []
        self.next_id = 1
        self.dir = SESSIONS / time.strftime("%Y%m%d-%H%M%S")
        (self.dir / "audio").mkdir(parents=True, exist_ok=True)
        (self.dir / "wireframes").mkdir(exist_ok=True)

    def add(self, role: str, text: str, kind: str = "speech", **extra) -> dict:
        with self.cond:
            msg = {"id": self.next_id, "role": role, "kind": kind, "text": text,
                   "ts": time.time(), **extra}
            self.next_id += 1
            self.messages.append(msg)
            with open(self.dir / "transcript.jsonl", "a") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.cond.notify_all()
        self.broadcast({"type": "message", "message": msg})
        return msg

    def broadcast(self, event: dict):
        if not self.loop:
            return
        for q in list(self.listeners):
            self.loop.call_soon_threadsafe(q.put_nowait, event)


S = State()


@app.on_event("startup")
async def _startup():
    S.loop = asyncio.get_running_loop()


# ---------------------------------------------------------------- TTS worker

kokoro = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
tts_jobs: queue.Queue = queue.Queue()


def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;:])\s+|\n+", text)
    chunks, buf = [], ""
    for p in (p.strip() for p in parts):
        if not p:
            continue
        buf = f"{buf} {p}".strip()
        if len(buf) > 40:  # avoid tiny choppy clips
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def tts_worker():
    while True:
        msg_id, text = tts_jobs.get()
        cfg = dict(S.settings)
        chunks = split_sentences(clean_for_speech(text))
        for i, chunk in enumerate(chunks):
            try:
                samples, sr = kokoro.create(chunk, voice=cfg["voice"], speed=cfg["speed"],
                                            lang=KOKORO_LANG[cfg["lang"]])
            except Exception as e:  # keep the session alive on odd input
                print("TTS error:", e)
                continue
            name = f"{msg_id}-{i}.wav"
            sf.write(S.dir / "audio" / name, samples, sr)
            for m in S.messages:
                if m["id"] == msg_id:
                    m.setdefault("audio", []).append(f"/audio/{name}")
            S.broadcast({"type": "audio", "message_id": msg_id, "index": i,
                         "last": i == len(chunks) - 1, "url": f"/audio/{name}"})


threading.Thread(target=tts_worker, daemon=True).start()


# ---------------------------------------------------------------- STT

stt_lock = threading.Lock()


def transcribe_file(path: str, lang: str) -> str:
    import mlx_whisper
    with stt_lock:
        # no conditioning on previous text: stops Whisper looping on trailing silence
        r = mlx_whisper.transcribe(path, path_or_hf_repo=WHISPER_REPO, language=lang,
                                   condition_on_previous_text=False)
    text = r["text"].strip()
    # drop a sentence repeated back-to-back (classic Whisper hallucination)
    out = []
    for s in re.split(r"(?<=[.!?])\s+", text):
        if not out or s.strip().lower() != out[-1].strip().lower():
            out.append(s)
    return " ".join(out)


# ---------------------------------------------------------------- API

class TextIn(BaseModel):
    text: str
    kind: str = "speech"


class SettingsIn(BaseModel):
    lang: str | None = None
    voice: str | None = None
    speed: float | None = None


@app.get("/api/state")
def get_state():
    return {"messages": S.messages, "settings": S.settings, "session": S.dir.name,
            "voices": sorted(kokoro.get_voices())}


@app.post("/api/settings")
def set_settings(body: SettingsIn):
    if body.lang:
        if body.lang not in VOICES:
            raise HTTPException(400, "lang must be en or es")
        if body.lang != S.settings["lang"] and not body.voice:
            S.settings["voice"] = VOICES[body.lang]
        S.settings["lang"] = body.lang
    if body.voice:
        S.settings["voice"] = body.voice
    if body.speed:
        S.settings["speed"] = max(0.6, min(1.6, body.speed))
    S.broadcast({"type": "settings", "settings": S.settings})
    return S.settings


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    suffix = Path(audio.filename or "a.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(await audio.read())
    text = await asyncio.to_thread(transcribe_file, f.name, S.settings["lang"])
    Path(f.name).unlink(missing_ok=True)
    return {"text": text}


@app.post("/api/candidate")
def candidate(body: TextIn):
    if not body.text.strip():
        raise HTTPException(400, "empty")
    return S.add("candidate", body.text.strip(), body.kind)


@app.post("/api/wireframe")
def wireframe(image: str = Form(...), note: str = Form("")):
    data = base64.b64decode(image.split(",", 1)[-1])
    path = S.dir / "wireframes" / f"{int(time.time())}.png"
    path.write_bytes(data)
    return S.add("candidate", note or "(sent wireframe)", "wireframe", image=str(path),
                 image_url=f"/wireframes/{path.name}")


@app.post("/api/interviewer")
def interviewer(body: TextIn):
    msg = S.add("interviewer", body.text.strip(), body.kind)
    if body.kind == "speech":
        tts_jobs.put((msg["id"], msg["text"]))
    return msg


@app.get("/api/next")
def next_messages(after: int = 0, timeout: float = 600):
    """Long-poll: block until a candidate message with id > after exists."""
    deadline = time.time() + timeout
    with S.cond:
        while True:
            new = [m for m in S.messages if m["id"] > after and m["role"] == "candidate"]
            if new:
                return {"messages": new, "last_id": S.messages[-1]["id"]}
            left = deadline - time.time()
            if left <= 0:
                return {"messages": [], "last_id": after}
            S.cond.wait(left)


@app.post("/api/reset")
def reset():
    with S.cond:
        S.new_session()
    S.broadcast({"type": "reset", "session": S.dir.name})
    return {"session": S.dir.name}


@app.get("/api/events")
async def events():
    q: asyncio.Queue = asyncio.Queue()
    S.listeners.add(q)

    async def gen():
        try:
            yield "retry: 1000\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            S.listeners.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/audio/{name}")
def audio(name: str):
    path = S.dir / "audio" / Path(name).name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


@app.get("/wireframes/{name}")
def wireframe_image(name: str):
    path = S.dir / "wireframes" / Path(name).name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png")


app.mount("/",StaticFiles(directory=ROOT / "static", html=True), name="static")
