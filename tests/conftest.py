"""Test setup: isolated data dir, fake TTS, fake interviewer process."""

import os
import queue
import tempfile

# Must happen before any `app.*` import: config resolves the data dir at import time.
os.environ["MOCKSY_DATA_DIR"] = tempfile.mkdtemp(prefix="mocksy-test-")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from app import sessions, speech  # noqa: E402


class FakeInterviewer:
    """Stands in for the Claude Code process: records what it's sent."""

    instances: list["FakeInterviewer"] = []

    def __init__(self, cwd, model, system_prompt, resume, on_event):
        self.cwd, self.model, self.system_prompt, self.resume = cwd, model, system_prompt, resume
        self.on_event = on_event
        self.sent: list[str] = []
        self.busy = False
        self.stopped = False
        FakeInterviewer.instances.append(self)

    @property
    def alive(self):
        return not self.stopped

    def send(self, text):
        self.sent.append(text)

    def stop(self):
        self.stopped = True

    # helper: replay what the real parser would emit for one assistant turn
    def reply(self, *events):
        self.on_event("turn_start", {})
        if events:
            self.on_event("first_text", {})
        for kind, value in events:
            self.on_event(kind, {"value": value})
        self.on_event("turn_end", {"value": 0.001})


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeInterviewer.instances.clear()
    monkeypatch.setattr(sessions, "Interviewer", FakeInterviewer)
    monkeypatch.setattr(speech, "synthesize", lambda text, voice, lang, speed: (np.zeros(2400, dtype=np.float32), 24000))
    monkeypatch.setattr(speech, "voices", lambda: ["af_heart", "am_adam", "em_alex", "ef_dora"])
    # a private TTS queue nobody consumes, so tests can inspect what would be spoken
    monkeypatch.setattr(sessions, "tts_queue", queue.Queue())
    yield


@pytest.fixture
def events():
    got: list[dict] = []
    return got


@pytest.fixture
def session(events):
    s = sessions.create_session("product-decomposition", "Acme", "Mid", "", "en",
                                "haiku", "haiku", events.append)
    s.start()
    return s
