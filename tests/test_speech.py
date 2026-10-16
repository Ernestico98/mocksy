"""Speech helpers: TTS text cleanup and Whisper hallucination filtering."""

import sys
import types

import pytest

from app import speech


def test_speakable_strips_markdown():
    assert speech.speakable("**Great** point, see [docs](http://x.y) and `code`.") == "Great point, see docs and code."


@pytest.mark.parametrize("text,expected", [
    ("I'm going to show you the video. I'm going to show you the video. I'm going to show you the video.",
     "I'm going to show you the video."),
    ("As in your previous version. " * 20, "As in your previous version."),
    ("as in your previous version as in your previous version as in your previous version ",
     "as in your previous version "),
    ("Use a queue. Then a worker. Then a queue.", "Use a queue. Then a worker. Then a queue."),
])
def test_collapse_loops(text, expected):
    assert speech._collapse_loops(text).strip() == expected.strip()


def fake_whisper(monkeypatch, segments):
    mod = types.SimpleNamespace(transcribe=lambda *a, **k: {"segments": segments,
                                                            "text": " ".join(s["text"] for s in segments)})
    monkeypatch.setitem(sys.modules, "mlx_whisper", mod)


def seg(text, no_speech=0.01, logprob=-0.2, ratio=1.2):
    return {"text": text, "no_speech_prob": no_speech, "avg_logprob": logprob, "compression_ratio": ratio}


def test_transcribe_keeps_normal_speech(monkeypatch):
    fake_whisper(monkeypatch, [seg(" I would start with the users."), seg(" Then scale.")])
    assert speech.transcribe("x.wav", "en") == "I would start with the users. Then scale."


@pytest.mark.parametrize("text", ["Thank you.", " Gracias.", "Thanks for watching!", "."])
def test_transcribe_drops_known_hallucinations(monkeypatch, text):
    fake_whisper(monkeypatch, [seg(text)])
    assert speech.transcribe("x.wav", "en") == ""


def test_transcribe_drops_silence_segments(monkeypatch):
    fake_whisper(monkeypatch, [seg(" Okay so the API.", no_speech=0.1),
                               seg(" you you you", no_speech=0.9, logprob=-1.2)])
    assert speech.transcribe("x.wav", "en") == "Okay so the API."


def test_transcribe_collapses_repetition_loops(monkeypatch):
    fake_whisper(monkeypatch, [seg(" Is that okay? " + "As in your previous version. " * 15, ratio=4.0)])
    assert speech.transcribe("x.wav", "en") == "Is that okay? As in your previous version."
