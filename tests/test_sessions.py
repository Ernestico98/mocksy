"""Session logic: conversation flow, canvas diffs, interrupts, phases, evaluation."""

import json
import time
import types

import pytest

from app import sessions
from tests.conftest import FakeInterviewer


def interviewer(s) -> FakeInterviewer:
    return s.interviewer


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- start

def test_start_spawns_interviewer_and_opens(session):
    it = interviewer(session)
    assert session.meta["status"] == "live"
    assert session.meta["phase"]["key"] == "problem"
    assert it.sent == ["[system] start"]
    assert it.cwd == session.dir                      # file tools confined to the session folder
    assert "Product Decomposition" in it.system_prompt and "Acme" in it.system_prompt
    assert json.loads((session.dir / "meta.json").read_text())["status"] == "live"


# ---------------------------------------------------------------- interviewer turns

def test_streamed_speech_builds_one_message_and_queues_tts(session):
    it = interviewer(session)
    it.reply(("speech", "Great to meet you."), ("speech", "Where would you start?"))
    msgs = [m for m in session.messages if m["role"] == "interviewer"]
    assert len(msgs) == 1
    assert msgs[0]["text"] == "Great to meet you. Where would you start?"
    assert msgs[0]["done"] is True
    queued = [sessions.tts_queue.get_nowait()[3] for _ in range(sessions.tts_queue.qsize())]
    assert queued == ["Great to meet you.", "Where would you start?"]


def test_silent_turn_leaves_no_message(session):
    interviewer(session).reply(("silent", None))
    assert not [m for m in session.messages if m["role"] == "interviewer"]


def test_note_and_phase_tags(session, events):
    interviewer(session).reply(("speech", "Let's move to the wireframe."), ("phase", "wireframe"),
                               ("note", "**Requirements**\n- a"))
    assert session.meta["phase"]["key"] == "wireframe"
    kinds = [(m["role"], m["kind"]) for m in session.messages]
    assert ("event", "phase") in kinds and ("interviewer", "note") in kinds
    assert any(e["type"] == "meta" for e in events)


def test_unknown_phase_is_ignored(session):
    interviewer(session).reply(("phase", "nonsense"))
    assert session.meta["phase"]["key"] == "problem"


def test_tool_use_is_broadcast_as_status(session, events):
    it = interviewer(session)
    it.on_event("turn_start", {})
    it.on_event("tool", {"value": "Read"})
    assert {"type": "status", "state": "tool", "detail": "Read", "session": session.id} in events


def test_session_id_is_persisted_for_resume(session):
    interviewer(session).on_event("session", {"value": "abc-123"})
    assert json.loads((session.dir / "meta.json").read_text())["claude_session_id"] == "abc-123"


def test_dead_process_is_resumed_on_next_message(session):
    it = interviewer(session)
    it.on_event("session", {"value": "abc-123"})
    it.stop()
    session.say("hello again")
    new = interviewer(session)
    assert new is not it and new.resume == "abc-123"
    assert "hello again" in new.sent[-1]


# ---------------------------------------------------------------- candidate turns

def test_say_sends_header_with_clock(session):
    session.say("Who are the users?")
    sent = interviewer(session).sent[-1]
    assert sent.startswith("[candidate · ") and "problem" in sent and "/15:00]" in sent
    assert sent.endswith("Who are the users?")
    assert session.messages[-1]["role"] == "candidate"


def test_typed_and_empty_messages(session):
    session.say("   ")
    assert len(interviewer(session).sent) == 1          # only the start message
    session.say("typed answer", kind="typed")
    assert interviewer(session).sent[-1].startswith("[candidate typed · ")


def test_say_is_ignored_after_the_interview(session):
    session.meta["status"] = "done"
    session.say("too late")
    assert all(m["text"] != "too late" for m in session.messages)


def test_canvas_changes_are_sent_once_as_a_diff(session):
    session.update_canvas(['box "API"', 'box "Queue"'], None, None)
    session.say("I drew two boxes")
    first = interviewer(session).sent[-1]
    assert '[canvas]' in first and '+ box "API"' in first and '+ box "Queue"' in first

    session.say("no drawing changes now")
    assert "[canvas]" not in interviewer(session).sent[-1]

    session.update_canvas(['box "API"', 'arrow box "API" → box "Queue"'], None, None)
    session.say("connected them")
    third = interviewer(session).sent[-1]
    assert '+ arrow box "API" → box "Queue"' in third and '- box "Queue"' in third


def test_canvas_png_and_scene_are_saved_and_snapshotted(session):
    png = "data:image/png;base64,iVBORw0KGgo="
    session.update_canvas(["box"], png, '{"elements": []}')
    assert (session.dir / "canvas.png").read_bytes().startswith(b"\x89PNG")
    assert (session.dir / "canvas.excalidraw").exists()
    session.say("look")
    assert len(list((session.dir / "sketches").glob("*.png"))) == 1


def test_ask_to_look_forces_canvas_and_read_hint(session):
    session.ask_to_look("")
    sent = interviewer(session).sent[-1]
    assert "[canvas]" in sent and "Read canvas.png" in sent
    assert session.messages[-1]["kind"] == "look"


def test_candidate_phase_change_reaches_interviewer_with_next_message(session):
    session.set_phase("wireframe", by="candidate")
    assert len(interviewer(session).sent) == 1                  # a phase click alone doesn't start a turn
    session.say("ready to draw")
    assert "[phase] The candidate moved to the wireframe phase." in interviewer(session).sent[-1]


# ---------------------------------------------------------------- interrupts

def test_interrupt_records_what_was_heard_and_cancels_tts(session):
    it = interviewer(session)
    it.reply(("speech", "First sentence here, quite long."), ("speech", "Second sentence never heard."))
    msg = next(m for m in session.messages if m["role"] == "interviewer")
    gen = session.tts_gen
    session.interrupt(msg["id"], heard_index=0)
    assert session.tts_gen == gen + 1
    assert msg["interrupted"] and msg["heard"] == "First sentence here, quite long."
    session.say("Sorry, quick question")
    sent = it.sent[-1]
    assert '[interrupted]' in sent and 'They heard: "First sentence here, quite long."' in sent


def test_speech_after_interrupt_is_not_spoken(session):
    it = interviewer(session)
    it.on_event("turn_start", {})
    it.on_event("speech", {"value": "Part one of a long answer."})
    msg = next(m for m in session.messages if m["role"] == "interviewer")
    session.interrupt(msg["id"], 0)
    while not sessions.tts_queue.empty():
        sessions.tts_queue.get_nowait()
    it.on_event("speech", {"value": "Part two should stay silent."})
    assert sessions.tts_queue.empty()


# ---------------------------------------------------------------- silent drawing

def test_silent_drawing_prompt_rules(session):
    it = interviewer(session)
    now = time.time()
    session.update_canvas(['box "API"'], None, None)
    assert not session.maybe_prompt_silent_drawing(now)                 # they just spoke/drew

    later = now + 60
    session.candidate_speaking = True
    assert not session.maybe_prompt_silent_drawing(later)               # never while they talk
    session.candidate_speaking = False
    it.busy = True
    assert not session.maybe_prompt_silent_drawing(later)               # never while the model works
    it.busy = False

    assert session.maybe_prompt_silent_drawing(later)
    assert it.sent[-1].count("[drawing silently") == 1 and '+ box "API"' in it.sent[-1]
    session.update_canvas(['box "API"', 'box "DB"'], None, None)
    session.last_canvas_change = later - 30
    assert not session.maybe_prompt_silent_drawing(later + 10)          # cooldown
    assert session.maybe_prompt_silent_drawing(later + 70)


def test_no_silent_prompt_without_new_drawing(session):
    session.last_candidate = session.last_turn_end = 0
    assert not session.maybe_prompt_silent_drawing(time.time())


# ---------------------------------------------------------------- end + evaluation

FEEDBACK = {"verdict": "lean hire", "summary": "ok", "scores": {"Problem framing": 3}, "top3": ["a", "b", "c"],
            "review": [{"id": 2, "topic": "t", "what": "w"}], "report": "## Veredicto\nbien"}


def test_end_tag_finishes_and_evaluates(session, monkeypatch):
    monkeypatch.setattr(sessions, "evaluate", lambda s: dict(FEEDBACK))
    monkeypatch.setattr(sessions.time, "sleep", lambda s: None)
    it = interviewer(session)
    it.reply(("speech", "Thanks, that's time."), ("end", None))
    assert wait_for(lambda: session.meta["status"] == "done")
    assert it.stopped
    assert session.meta["verdict"] == "lean hire"
    assert json.loads((session.dir / "feedback.json").read_text())["top3"] == ["a", "b", "c"]


def test_failed_evaluation_can_be_retried(session, monkeypatch):
    monkeypatch.setattr(sessions.time, "sleep", lambda s: None)

    def boom(s):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(sessions, "evaluate", boom)
    session.finish()
    assert session.meta["status"] == "ended"
    monkeypatch.setattr(sessions, "evaluate", lambda s: dict(FEEDBACK))
    session.meta["status"] = "live"   # what POST /end does for an "ended" session
    session.finish()
    assert session.meta["status"] == "done"


def test_evaluate_parses_json_and_report(session, monkeypatch):
    out = "```json\n" + json.dumps({k: v for k, v in FEEDBACK.items() if k != "report"}) + "\n```\n\n## Veredicto\nBien hecho."
    calls = {}

    def fake_run(args, **kw):
        calls["args"], calls["cwd"] = args, kw["cwd"]
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"result": out}), stderr="")
    monkeypatch.setattr(sessions.subprocess, "run", fake_run)
    session.say("Users are linguists.")
    fb = sessions.evaluate(session)
    assert fb["verdict"] == "lean hire" and fb["report"] == "## Veredicto\nBien hecho."
    assert calls["cwd"] == session.dir and calls["args"][calls["args"].index("--model") + 1] == "haiku"
    assert "Users are linguists." in (session.dir / "transcript.md").read_text()
    assert (session.dir / "feedback.md").read_text() == "## Veredicto\nBien hecho."


def test_evaluate_without_json_block_keeps_report(session, monkeypatch):
    monkeypatch.setattr(sessions.subprocess, "run", lambda args, **kw: types.SimpleNamespace(
        returncode=0, stdout=json.dumps({"result": "Just prose."}), stderr=""))
    assert sessions.evaluate(session) == {"report": "Just prose."}


def test_evaluate_raises_on_cli_error(session, monkeypatch):
    monkeypatch.setattr(sessions.subprocess, "run", lambda args, **kw: types.SimpleNamespace(
        returncode=1, stdout="", stderr="not logged in"))
    with pytest.raises(RuntimeError, match="not logged in"):
        sessions.evaluate(session)


def test_session_reloads_from_disk(session, events):
    session.say("persist me")
    again = sessions.Session(session.dir, events.append)
    assert again.messages[-1]["text"] == "persist me"
    assert again.next_id == session.next_id
