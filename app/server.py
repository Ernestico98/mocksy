"""HTTP API + static UI."""

import asyncio
import json
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import packs, speech
from .config import LANGUAGES, ROOT, SESSIONS, load_settings, save_settings
from .sessions import Session, create_session

listeners: set[asyncio.Queue] = set()
loop: asyncio.AbstractEventLoop | None = None
sessions: dict[str, Session] = {}


@asynccontextmanager
async def lifespan(_app):
    global loop
    loop = asyncio.get_running_loop()
    yield


app = FastAPI(lifespan=lifespan)


@app.exception_handler(packs.PackError)
def pack_error(_request, e: packs.PackError):
    return JSONResponse({"detail": "Invalid interview type", "errors": e.errors}, status_code=422)


def broadcast(event: dict):
    if loop:
        for q in list(listeners):
            loop.call_soon_threadsafe(q.put_nowait, event)


def get(sid: str) -> Session:
    if sid not in sessions:
        path = SESSIONS / Path(sid).name
        if not (path / "meta.json").exists():
            raise HTTPException(404, "session not found")
        sessions[sid] = Session(path, broadcast)
    return sessions[sid]


# ------------------------------------------------------------------ meta & settings

@app.get("/api/meta")
def meta():
    listed = [{k: p.get(k, "") for k in ("id", "title", "description", "company", "default_language",
                                          "phases", "whiteboard", "builtin")}
              for p in packs.load_packs()]
    return {"packs": listed, "languages": LANGUAGES, "voices": speech.voices(),
            "settings": load_settings(),
            "models": ["haiku", "sonnet", "opus", "fable"]}


class SettingsIn(BaseModel):
    model: str | None = None
    feedback_model: str | None = None
    speed: float | None = None
    voice_en: str | None = None
    voice_es: str | None = None


@app.post("/api/settings")
def settings(body: SettingsIn):
    s = save_settings(body.model_dump())
    broadcast({"type": "settings", "settings": s})
    return s


# ------------------------------------------------------------------ interview packs

class PackIn(BaseModel):
    id: str
    title: str = ""
    description: str = ""
    company: str = ""
    default_language: str = "en"
    whiteboard: bool = True
    phases: list[dict[str, Any]] = []
    rubric: list[str] = []
    body: str = ""
    extra: dict[str, Any] = {}  # header fields the editor doesn't know about, kept as they are


class RawIn(BaseModel):
    raw: str


def pack_out(p: dict) -> dict:
    known = {k: p.get(k, "") for k in ("id", "title", "description", "company", "default_language", "body")}
    return known | {
        "builtin": p.get("builtin", False), "whiteboard": p.get("whiteboard", True) is not False,
        "phases": p.get("phases", []), "rubric": p.get("rubric", []),
        "extra": {k: v for k, v in p.items() if k not in packs.FIELDS and k not in ("id", "body", "builtin")},
    }


def pack_in(body: PackIn) -> dict:
    d = body.model_dump()
    return d.pop("extra") | d


def find_pack(pid: str) -> dict:
    try:
        return packs.get_pack(pid)
    except KeyError:
        raise HTTPException(404, "interview type not found") from None


def editable(pid: str):
    find_pack(pid)
    if packs.is_builtin(pid):
        raise HTTPException(403, "Built-in interview types are read-only: duplicate it instead")


@app.get("/api/packs")
def list_packs():
    return [pack_out(p) for p in packs.load_packs()]


@app.get("/api/packs/{pid}")
def read_pack(pid: str):
    return pack_out(find_pack(pid))


@app.post("/api/packs")
def create_pack(body: PackIn):
    return pack_out(packs.save_pack(pack_in(body)))


@app.put("/api/packs/{pid}")
def update_pack(pid: str, body: PackIn):
    editable(pid)
    return pack_out(packs.save_pack(pack_in(body), old_id=pid))


@app.delete("/api/packs/{pid}")
def delete_pack(pid: str):
    editable(pid)
    packs.delete_pack(pid)
    return {"ok": True}


@app.post("/api/pack-markdown")
def pack_markdown(body: PackIn):
    """Form → raw markdown, for the editor's raw mode (not validated: it may be a draft)."""
    return {"raw": packs.serialize(pack_in(body))}


@app.post("/api/pack-markdown/parse")
def parse_pack_markdown(body: RawIn):
    """Raw markdown → form fields."""
    return pack_out(packs.parse(body.raw))


# ------------------------------------------------------------------ sessions

@app.get("/api/sessions")
def list_sessions():
    out = []
    for path in sorted(SESSIONS.glob("*/meta.json"), reverse=True):
        m = json.loads(path.read_text())
        dur = (m.get("ended_at") or m.get("started_at") or 0) - (m.get("started_at") or 0)
        out.append({k: m.get(k) for k in ("id", "created", "pack_title", "company", "status", "verdict", "lang")}
                   | {"minutes": round(dur / 60)})
    return out


class NewSession(BaseModel):
    pack: str
    company: str = ""
    level: str = ""
    focus: str = ""
    problem: str = ""
    lang: str = "en"
    model: str | None = None
    feedback_model: str | None = None


@app.post("/api/sessions")
def new_session(body: NewSession):
    find_pack(body.pack)
    for s in sessions.values():  # one live interview at a time
        if s.meta.get("status") == "live" and s.interviewer:
            s.interviewer.stop()
            s.meta["status"] = "ended"
            s.save()
    s = create_session(body.pack, body.company, body.level, body.focus, body.lang,
                       body.model, body.feedback_model, broadcast, body.problem)
    sessions[s.id] = s
    s.start()
    return s.public()


@app.get("/api/sessions/{sid}")
def session(sid: str):
    return get(sid).public()


class SayIn(BaseModel):
    text: str
    kind: str = "speech"


@app.post("/api/sessions/{sid}/say")
def say(sid: str, body: SayIn):
    get(sid).say(body.text, "typed" if body.kind == "typed" else "speech")
    return {"ok": True}


@app.post("/api/sessions/{sid}/look")
def look(sid: str, body: SayIn):
    get(sid).ask_to_look(body.text)
    return {"ok": True}


@app.post("/api/sessions/{sid}/transcribe")
async def transcribe(sid: str, audio: UploadFile = File(...), vad_at: float | None = Form(None),
                     silence_ms: float | None = Form(None)):
    """`vad_at` (epoch seconds) and `silence_ms` come from the browser's VAD, for latency stats."""
    received = time.time()
    s = get(sid)
    suffix = Path(audio.filename or "a.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(await audio.read())
    try:
        text = await asyncio.to_thread(speech.transcribe, f.name, s.lang)
    finally:
        Path(f.name).unlink(missing_ok=True)
    if text:
        s.record_stt(text, {"vad": vad_at, "received": received, "stt_done": time.time()}, silence_ms)
    return {"text": text}


class CanvasIn(BaseModel):
    lines: list[str]
    png: str | None = None
    scene: str | None = None


@app.post("/api/sessions/{sid}/canvas")
def canvas(sid: str, body: CanvasIn):
    get(sid).update_canvas(body.lines, body.png, body.scene)
    return {"ok": True}


class ActivityIn(BaseModel):
    speaking: bool


@app.post("/api/sessions/{sid}/activity")
def activity(sid: str, body: ActivityIn):
    get(sid).candidate_speaking = body.speaking
    return {"ok": True}


class PhaseIn(BaseModel):
    key: str


@app.post("/api/sessions/{sid}/phase")
def phase(sid: str, body: PhaseIn):
    get(sid).set_phase(body.key, by="candidate")
    return {"ok": True}


class InterruptIn(BaseModel):
    message_id: int
    heard_index: int


@app.post("/api/sessions/{sid}/interrupt")
def interrupt(sid: str, body: InterruptIn):
    get(sid).interrupt(body.message_id, body.heard_index)
    return {"ok": True}


class PlayedIn(BaseModel):
    message_id: int
    index: int = 0
    at: float | None = None  # epoch seconds, when playback started in the browser


@app.post("/api/sessions/{sid}/played")
def played(sid: str, body: PlayedIn):
    get(sid).played(body.message_id, body.index, body.at)
    return {"ok": True}


@app.post("/api/sessions/{sid}/stop")
def stop(sid: str):
    get(sid).stop()
    return {"ok": True}


@app.post("/api/sessions/{sid}/resume")
def resume(sid: str):
    s = get(sid)
    for other in sessions.values():  # one live interview at a time
        if other is not s and other.meta.get("status") == "live":
            other.stop()
    if not s.resume():
        raise HTTPException(409, "Only a stopped, not yet evaluated interview can be resumed")
    return {"ok": True}


@app.post("/api/sessions/{sid}/end")
async def end(sid: str):
    s = get(sid)
    if s.meta.get("status") == "ended":  # evaluation failed earlier: retry
        s.meta["status"] = "live"
    asyncio.get_running_loop().run_in_executor(None, s.finish)
    return {"ok": True}


@app.get("/files/{sid}/{path:path}")
def files(sid: str, path: str):
    base = get(sid).dir.resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)


# ------------------------------------------------------------------ events

@app.get("/api/events")
async def events():
    q: asyncio.Queue = asyncio.Queue()
    listeners.add(q)

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
            listeners.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


WEB = ROOT / "web" / "dist"
if WEB.exists():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
else:
    @app.get("/")
    def not_built():
        return {"error": "Frontend not built. Run: cd web && pnpm install && pnpm build (or ./run.sh)"}
