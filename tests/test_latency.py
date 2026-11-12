"""Per-turn latency records: marks from the browser, STT, the interviewer, TTS and playback."""

import time

import pytest
from fastapi.testclient import TestClient

from app import server, sessions
from app.latency import Turn
from tests.conftest import FakeInterviewer


def test_turn_summary_breaks_down_the_stages():
    t = Turn("speech", {"vad": 100.0, "received": 100.05, "stt_done": 100.35}, silence_ms=1300)
    for name, at in [("sent", 100.4), ("first_text", 101.4), ("first_chunk", 101.6), ("tts_ready", 101.8),
                     ("played", 101.9)]:
        t.mark(name, at)
    t.mark("sent", 999)  # first mark wins
    s = t.summary()
    assert s["silence_ms"] == 1300
    assert (s["upload_ms"], s["stt_ms"], s["handoff_ms"], s["model_ms"]) == (50, 300, 50, 1000)
    assert (s["chunk_ms"], s["tts_ms"], s["play_ms"]) == (200, 200, 100)
    assert s["total_ms"] == 1900 and s["total_to"] == "played"


def test_partial_turn_only_reports_known_stages():
    t = Turn("typed", {"received": 10.0})
    t.mark("sent", 10.01)
    t.mark("first_text", 10.5)
    s = t.summary()
    assert s == {"kind": "typed", "handoff_ms": 10, "model_ms": 490}


def test_session_records_latency_on_the_candidate_message(session, capsys):
    it = session.interviewer
    it.reply(("speech", "Hello there, welcome to the interview."))  # the greeting: no candidate turn
    session.record_stt("I would start with the users.", {"vad": time.time() - 0.5, "received": time.time() - 0.4,
                                                         "stt_done": time.time() - 0.1}, 1300)
    session.say("I would start with the users.")
    cand = session.messages[-1]
    it.on_event("turn_start", {})
    it.on_event("tool", {"value": "Write"})
    it.on_event("first_text", {})
    it.on_event("speech", {"value": "Good, who are they?"})
    reply = session.messages[-1]
    assert reply["role"] == "interviewer"
    it.on_event("turn_end", {"value": 0})
    session.tts_ready(reply["id"], 0)
    session.played(reply["id"], 0, time.time())

    lat = cand["latency"]
    assert lat["kind"] == "speech" and lat["silence_ms"] == 1300 and lat["reply_id"] == reply["id"]
    assert {"upload_ms", "stt_ms", "handoff_ms", "model_ms", "chunk_ms", "tts_ms", "play_ms"} <= set(lat)
    assert lat["total_to"] == "played" and lat["total_ms"] >= 500
    assert lat["tools_before_speech"] == 1 and lat["first_chunk_chars"] == len("Good, who are they?")
    out = capsys.readouterr().out
    assert out.count("[latency]") == 1 and f'"message": {cand["id"]}' in out
    session.played(reply["id"], 0, time.time())  # replaying later doesn't log again
    assert "[latency]" not in capsys.readouterr().out


def test_silent_reply_closes_the_record_at_turn_end(session, capsys):
    session.say("Let me draw this first.", "typed")
    session.interviewer.reply(("silent", None))
    lat = session.messages[-1]["latency"]
    assert lat["kind"] == "typed" and "handoff_ms" in lat and "tts_ms" not in lat
    assert "[latency]" in capsys.readouterr().out


def test_events_of_the_turn_in_progress_are_not_attributed_to_a_queued_message(session):
    it = session.interviewer
    it.on_event("turn_start", {})          # the interviewer is still answering an earlier message
    session.say("A follow-up while it talks.")
    cand = session.messages[-1]
    it.on_event("first_text", {})
    it.on_event("speech", {"value": "This belongs to the earlier turn."})
    it.on_event("turn_end", {"value": 0})
    assert "latency" not in cand or "model_ms" not in cand["latency"]
    it.reply(("speech", "Now answering the follow-up."))
    assert session.turn.has("first_chunk") and session.turn.reply_id == session.messages[-1]["id"]


@pytest.fixture
def client(monkeypatch):
    server.sessions.clear()
    with TestClient(server.app) as c:
        yield c


def test_transcribe_and_played_endpoints_feed_the_record(client, monkeypatch):
    sid = client.post("/api/sessions", json={"pack": "system-design", "lang": "en", "model": "haiku",
                                             "feedback_model": "haiku"}).json()["meta"]["id"]
    monkeypatch.setattr(server.speech, "transcribe", lambda path, lang: "Use a queue.")
    vad = time.time() - 0.2
    client.post(f"/api/sessions/{sid}/transcribe", files={"audio": ("t.wav", b"RIFF", "audio/wav")},
                data={"vad_at": str(vad), "silence_ms": "800"})
    client.post(f"/api/sessions/{sid}/say", json={"text": "Use a queue."})
    it = FakeInterviewer.instances[-1]
    it.reply(("speech", "Why a queue rather than direct calls?"))
    s = server.sessions[sid]
    reply_id = s.messages[-1]["id"]
    s.tts_ready(reply_id, 0)
    assert client.post(f"/api/sessions/{sid}/played", json={"message_id": reply_id, "index": 0,
                                                            "at": time.time()}).status_code == 200
    cand = next(m for m in client.get(f"/api/sessions/{sid}").json()["messages"] if m["role"] == "candidate")
    assert cand["latency"]["silence_ms"] == 800 and cand["latency"]["upload_ms"] >= 0
    assert cand["latency"]["total_to"] == "played"
    assert sessions.tts_queue.qsize() >= 1
