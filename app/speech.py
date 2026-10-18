"""Local speech: Kokoro (TTS) and Whisper (STT): mlx on Apple Silicon, faster-whisper elsewhere."""

import platform
import re
import sys
import threading

from kokoro_onnx import Kokoro

from .config import DEFAULTS, MODELS

KOKORO_LANG = {"en": "en-us", "es": "es"}

_kokoro: Kokoro | None = None
_faster = None
_tts_lock = threading.Lock()
_stt_lock = threading.Lock()


def kokoro() -> Kokoro:
    """Loaded on first use (~1 s), so importing the app stays fast."""
    global _kokoro
    with _tts_lock:
        if _kokoro is None:
            _kokoro = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
    return _kokoro


def voices() -> list[str]:
    return sorted(v for v in kokoro().get_voices() if v[:2] in ("af", "am", "bf", "bm", "ef", "em"))


def synthesize(text: str, voice: str, lang: str, speed: float):
    k = kokoro()
    with _tts_lock:
        return k.create(text, voice=voice, speed=speed, lang=KOKORO_LANG.get(lang, "en-us"))


def speakable(text: str) -> str:
    """Strip anything a TTS engine would read literally."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`#>|]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------------ STT

# Phrases Whisper is known to invent on silence or noise.
HALLUCINATIONS = {
    "thank you.", "thank you", "thanks for watching!", "thanks for watching.", "you",
    "gracias.", "gracias", "subtítulos realizados por la comunidad de amara.org",
    "¡gracias por ver!", "bye.", "okay.", ".",
}


def _collapse_loops(text: str) -> str:
    """Remove a sentence or phrase repeated back-to-back (Whisper looping)."""
    parts = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    for p in parts:
        if out and p.strip().lower() == out[-1].strip().lower():
            continue
        out.append(p)
    text = " ".join(out)
    # short phrase loops without punctuation: "as in your previous version as in your previous version ..."
    return re.sub(r"\b((?:\w+\W+){2,8}?)(?:\1){2,}", r"\1", text, flags=re.I)


def stt_engine() -> str:
    engine = DEFAULTS["speech"].get("stt_engine", "auto")
    if engine == "auto":
        return "mlx" if sys.platform == "darwin" and platform.machine() == "arm64" else "faster-whisper"
    return engine


def _segments(path, lang: str | None) -> list[dict]:
    """Whisper segments as dicts with text, no_speech_prob, avg_logprob and compression_ratio."""
    if stt_engine() == "mlx":
        import mlx_whisper

        r = mlx_whisper.transcribe(path, path_or_hf_repo=DEFAULTS["speech"]["whisper_model"],
                                   language=lang, condition_on_previous_text=False)
        return r.get("segments", [])

    global _faster
    if _faster is None:
        from faster_whisper import WhisperModel

        # CUDA if available, otherwise CPU, with the fastest compute type for the device
        _faster = WhisperModel(DEFAULTS["speech"].get("faster_whisper_model", "large-v3-turbo"),
                               device="auto", compute_type="auto")
    segments, _ = _faster.transcribe(path, language=lang, condition_on_previous_text=False)
    return [{"text": s.text, "no_speech_prob": s.no_speech_prob, "avg_logprob": s.avg_logprob,
             "compression_ratio": s.compression_ratio} for s in segments]


def preload_stt():
    """Downloads and loads the Whisper model (used by setup)."""
    import numpy as np

    with _stt_lock:
        _segments(np.zeros(16000, dtype=np.float32), "en")


def transcribe(path: str, lang: str) -> str:
    with _stt_lock:
        segments = _segments(path, lang)
    kept = []
    for seg in segments:
        if seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -0.7:
            continue  # probably silence
        if seg.get("compression_ratio", 0) > 2.6:
            seg = {**seg, "text": _collapse_loops(seg["text"])}
        kept.append(seg["text"].strip())
    text = _collapse_loops(" ".join(kept).strip())
    if text.lower() in HALLUCINATIONS or len(text) < 2:
        return ""
    return text
