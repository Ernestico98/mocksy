"""Opt-in: talks to a real Claude Code process (uses your subscription, ~10-30 s).

    MOCKSY_LIVE=1 .venv/bin/pytest tests/test_live.py -s

Catches breaking changes in the CLI's stream-json protocol or flags.
"""

import os
import tempfile
import threading
from pathlib import Path

import pytest

from app.interviewer import Interviewer

pytestmark = pytest.mark.skipif(not os.environ.get("MOCKSY_LIVE"), reason="set MOCKSY_LIVE=1 to run")

PROMPT = ("You are an interviewer in a test. Everything outside tags is spoken. "
          "On '[system] start' say exactly one short greeting sentence. "
          "On any other message reply with one short sentence, then <phase>design</phase>.")


def run_turn(it, events, done, text):
    done.clear()
    it.send(text)
    assert done.wait(120), "no turn_end from claude"


def test_real_interviewer_round_trip():
    events: list[tuple[str, object]] = []
    done = threading.Event()

    def on_event(kind, data):
        events.append((kind, data.get("value")))
        if kind == "turn_end":
            done.set()

    cwd = Path(tempfile.mkdtemp(prefix="mocksy-live-"))
    it = Interviewer(cwd, "haiku", PROMPT, None, on_event)
    try:
        run_turn(it, events, done, "[system] start")
        assert it.session_id, "no session id from init event"
        assert any(k == "speech" and v for k, v in events)

        events.clear()
        run_turn(it, events, done, "[candidate · 00:10 total]\nHi, ready.")
        assert ("phase", "design") in events
        spoken = " ".join(v for k, v in events if k == "speech")
        assert "<" not in spoken
    finally:
        it.stop()
