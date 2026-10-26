"""HTTP API, end to end with the fake interviewer."""

import json

import pytest
from fastapi.testclient import TestClient

from app import server, sessions
from tests.conftest import FakeInterviewer

FEEDBACK = {"verdict": "hire", "summary": "s", "report": "r"}


@pytest.fixture
def client(monkeypatch):
    server.sessions.clear()
    monkeypatch.setattr(sessions, "evaluate", lambda s: dict(FEEDBACK))
    monkeypatch.setattr(sessions.time, "sleep", lambda s: None)
    with TestClient(server.app) as c:
        yield c


def new(client, **kw):
    body = {"pack": "system-design", "lang": "es", "model": "haiku", "feedback_model": "haiku"} | kw
    r = client.post("/api/sessions", json=body)
    assert r.status_code == 200, r.text
    return r.json()["meta"]["id"]


def test_meta_lists_packs_and_settings(client):
    m = client.get("/api/meta").json()
    assert {p["id"] for p in m["packs"]} >= {"system-design"}
    assert m["languages"] == {"en": "English", "es": "Spanish"}
    assert {"model", "feedback_model", "speed"} <= set(m["settings"])
    assert "sonnet" in m["models"]


def test_settings_roundtrip_and_ignore_unknown(client):
    r = client.post("/api/settings", json={"model": "opus", "speed": 1.1})
    assert r.json()["model"] == "opus" and r.json()["speed"] == 1.1
    assert client.get("/api/meta").json()["settings"]["model"] == "opus"
    client.post("/api/settings", json={"model": "sonnet", "speed": 1.0})


def test_full_session_flow(client):
    sid = new(client, company="Acme", level="Senior")
    it = FakeInterviewer.instances[-1]
    assert it.model == "haiku" and it.sent == ["[system] start"]
    assert "Spanish" in it.system_prompt and "Acme" in it.system_prompt

    it.reply(("speech", "Hola, diseñemos un sistema de notificaciones."))
    assert client.post(f"/api/sessions/{sid}/canvas", json={"lines": ['box "API"'], "png": None, "scene": "{}"}).status_code == 200
    assert client.post(f"/api/sessions/{sid}/say", json={"text": "¿Cuántos usuarios?"}).status_code == 200
    assert '+ box "API"' in it.sent[-1] and "¿Cuántos usuarios?" in it.sent[-1]

    assert client.post(f"/api/sessions/{sid}/phase", json={"key": "high_level"}).status_code == 200
    data = client.get(f"/api/sessions/{sid}").json()
    assert data["meta"]["phase"]["key"] == "high_level"
    assert [m["role"] for m in data["messages"]] == ["interviewer", "candidate", "event"]

    listed = client.get("/api/sessions").json()
    assert listed[0]["id"] == sid and listed[0]["status"] == "live"

    assert client.post(f"/api/sessions/{sid}/end").status_code == 200
    import time
    for _ in range(100):
        if client.get(f"/api/sessions/{sid}").json()["meta"]["status"] == "done":
            break
        time.sleep(0.02)
    data = client.get(f"/api/sessions/{sid}").json()
    assert data["meta"]["status"] == "done" and data["feedback"]["verdict"] == "hire"


def test_stop_endpoint_ends_without_evaluation(client):
    sid = new(client, problem="Design a chat app for 10k users")
    it = FakeInterviewer.instances[-1]
    assert "Design a chat app for 10k users" in it.system_prompt
    assert client.post(f"/api/sessions/{sid}/stop").status_code == 200
    data = client.get(f"/api/sessions/{sid}").json()
    assert data["meta"]["status"] == "ended" and data["feedback"] is None and it.stopped
    assert data["meta"]["problem"] == "Design a chat app for 10k users"


def test_resume_continues_a_stopped_interview_without_counting_the_pause(client):
    sid = new(client)
    it = FakeInterviewer.instances[-1]
    it.on_event("session", {"value": "claude-123"})
    client.post(f"/api/sessions/{sid}/stop")
    before = client.get(f"/api/sessions/{sid}").json()["meta"]
    assert client.post(f"/api/sessions/{sid}/resume").status_code == 200
    meta = client.get(f"/api/sessions/{sid}").json()["meta"]
    assert meta["status"] == "live" and "ended_at" not in meta
    assert meta["started_at"] > before["started_at"]
    resumed = FakeInterviewer.instances[-1]
    assert resumed is not it and resumed.resume == "claude-123"
    assert resumed.sent[-1].startswith("[system] resume · 00:0")
    client.post(f"/api/sessions/{sid}/say", json={"text": "I'm back"})
    assert "I'm back" in resumed.sent[-1]
    assert client.post(f"/api/sessions/{sid}/resume").status_code == 409  # already live


def test_new_session_stops_the_previous_live_one(client):
    new(client)
    first = FakeInterviewer.instances[-1]
    new(client)
    assert first.stopped


def test_interrupt_and_activity_endpoints(client):
    sid = new(client)
    it = FakeInterviewer.instances[-1]
    it.reply(("speech", "A fairly long first sentence here."), ("speech", "Second one."))
    msg_id = client.get(f"/api/sessions/{sid}").json()["messages"][0]["id"]
    assert client.post(f"/api/sessions/{sid}/interrupt", json={"message_id": msg_id, "heard_index": 0}).status_code == 200
    assert client.post(f"/api/sessions/{sid}/activity", json={"speaking": True}).status_code == 200
    assert server.sessions[sid].candidate_speaking is True
    assert client.get(f"/api/sessions/{sid}").json()["messages"][0]["interrupted"] is True


def test_transcribe_endpoint(client, monkeypatch):
    sid = new(client)
    seen = {}

    def fake(path, lang):
        seen["lang"] = lang
        return "hola"
    monkeypatch.setattr(server.speech, "transcribe", fake)
    r = client.post(f"/api/sessions/{sid}/transcribe", files={"audio": ("t.wav", b"RIFF....", "audio/wav")})
    assert r.json() == {"text": "hola"} and seen["lang"] == "es"


def test_files_are_served_but_cannot_escape_the_session(client):
    sid = new(client)
    assert client.get(f"/files/{sid}/meta.json").status_code == 200
    assert json.loads(client.get(f"/files/{sid}/meta.json").text)["id"] == sid
    assert client.get(f"/files/{sid}/../settings.json").status_code == 404
    assert client.get(f"/files/{sid}/%2e%2e/%2e%2e/config.toml").status_code == 404
    assert client.get(f"/files/{sid}/missing.png").status_code == 404


def test_unknown_session_is_404(client):
    assert client.get("/api/sessions/nope").status_code == 404
    assert client.post("/api/sessions/nope/say", json={"text": "x"}).status_code == 404
    assert client.get("/api/sessions/..%2F..%2Fapp").status_code == 404
