"""Latency benchmark, no microphone needed: synthetic candidate turns through the real pipeline.

    .venv/bin/python scripts/bench_latency.py                 # 3 turns with haiku
    .venv/bin/python scripts/bench_latency.py --turns 4 --model sonnet --silence-ms 800

Each turn: a Kokoro-synthesised candidate answer (16 kHz WAV, like the browser sends) →
real Whisper → a real interviewer session (Claude Code, the built-in system design pack) →
the real TTS worker. Prints the stage breakdown recorded in the session (see app/latency.py),
up to "first audio ready" (playback happens in a browser, so it isn't measured here).
Uses a throwaway data folder; costs a few cents of Claude usage per run.
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UTTERANCES = {
    "en": [
        "Okay, before I start designing anything, I'd like to clarify the requirements. "
        "How many users are we expecting, and is this mostly read heavy or write heavy?",
        "Got it. So I'd put a load balancer in front of a stateless API tier, and store the mappings "
        "in a key value store, with a cache in front of it for the hot keys.",
        "For the ID generation I'd hand out counter ranges to each server and base sixty two encode them, "
        "so we never get collisions and don't need to coordinate on every write.",
        "I think the main bottleneck is the database on writes, so I'd shard by a hash of the short code "
        "and keep a couple of read replicas per shard.",
        "If a cache node goes down we just fall back to the database for those keys, "
        "and the consistent hashing keeps most of the other keys where they were.",
    ],
    "es": [
        "Vale, antes de diseñar nada me gustaría aclarar los requisitos. ¿Cuántos usuarios esperamos, "
        "y es un sistema sobre todo de lectura o de escritura?",
        "Entendido. Pondría un balanceador de carga delante de una capa de API sin estado, y guardaría "
        "los datos en un almacén clave valor con una caché delante para las claves más usadas.",
        "Para generar los identificadores repartiría rangos de un contador entre los servidores, "
        "así nunca hay colisiones y no hace falta coordinarse en cada escritura.",
    ],
}
VOICE = {"en": "am_adam", "es": "em_alex"}


def ms(seconds: float) -> str:
    return f"{seconds * 1000:7.0f}"


def synth_wav(path: Path, text: str, lang: str, silence_ms: int):
    """Candidate audio as the browser would upload it: pre-roll, speech, trailing pause, 16 kHz."""
    import numpy as np
    import soundfile as sf

    from app import speech

    samples, sr = speech.synthesize(text, VOICE[lang], lang, 1.0)
    samples = np.concatenate([np.zeros(int(0.35 * sr)), samples, np.zeros(int(silence_ms / 1000 * sr))])
    n = int(len(samples) * 16000 / sr)
    resampled = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples)
    sf.write(path, resampled.astype(np.float32), 16000, subtype="PCM_16")
    return len(samples) / sr


def cache_usage(session_id: str | None) -> list[dict]:
    """Token usage per API call, from Claude Code's own transcript of the interviewer session."""
    if not session_id:
        return []
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
    files = list(base.glob(f"*/{session_id}.jsonl"))
    out, seen = [], set()
    for line in files[0].read_text().splitlines() if files else []:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        msg = ev.get("message") or {}
        if ev.get("type") == "assistant" and msg.get("usage") and msg.get("id") not in seen:
            seen.add(msg.get("id"))
            u = msg["usage"]
            out.append({k: u.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                  "cache_read_input_tokens", "output_tokens")})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--effort", default="default", help="interviewer effort: default, low, medium, high")
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--lang", default="en", choices=sorted(UTTERANCES))
    ap.add_argument("--pack", default="system-design")
    ap.add_argument("--silence-ms", type=int, default=1300,
                    help="trailing pause in each WAV, i.e. what the VAD waited (default 1300)")
    ap.add_argument("--cold-stt", action="store_true",
                    help="only load Whisper instead of the server's startup warm-up (the old behaviour)")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway data folder")
    args = ap.parse_args()

    data = Path(tempfile.mkdtemp(prefix="mocksy-bench-"))
    os.environ["MOCKSY_DATA_DIR"] = str(data)  # before importing app.config
    os.environ.setdefault("MOCKSY_WARMUP", "0")

    from app import sessions, speech

    t = time.time()
    speech.synthesize("Warm up.", "af_heart", "en", 1.0)
    print(f"Kokoro cold start      {ms(time.time() - t)} ms")
    t = time.time()
    if args.cold_stt:
        speech.preload_stt()
        print(f"Whisper load           {ms(time.time() - t)} ms  ({speech.stt_engine()}, no warm-up)")
    else:
        speech.warm_up()  # what the server does in the background at startup
        print(f"Speech warm-up         {ms(time.time() - t)} ms  ({speech.stt_engine()})")

    wavs = []
    for i, text in enumerate((UTTERANCES[args.lang] * 3)[:args.turns]):
        p = data / f"turn{i}.wav"
        secs = synth_wav(p, text, args.lang, args.silence_ms)
        wavs.append((p, text, secs))

    idle = threading.Event()

    def broadcast(ev):
        if ev.get("type") == "status" and ev.get("state") in ("idle", "error"):
            if ev.get("state") == "error":
                print("  ! error:", ev.get("detail"))
            idle.set()

    s = sessions.create_session(args.pack, "", "Senior", "", args.lang, args.model, args.model, broadcast,
                                effort=args.effort)
    t = time.time()
    s.start()
    if not idle.wait(240):
        sys.exit("no greeting from the interviewer")
    greeting = next((m for m in s.messages if m["role"] == "interviewer" and m["kind"] == "speech"), None)
    deadline = time.time() + 60  # all of it synthesised, so turn 1 doesn't queue behind the greeting
    while greeting and len(greeting.get("audio", {})) < len(greeting["chunks"]) and time.time() < deadline:
        time.sleep(0.02)
    print(f"Greeting (process start → reply done) {ms(time.time() - t)} ms\n")

    rows = []
    for i, (wav, text, secs) in enumerate(wavs):
        idle.clear()
        vad = time.time()
        received = time.time()  # no network hop: the upload stage is ~0 on one machine
        heard = speech.transcribe(str(wav), args.lang)
        s.record_stt(heard, {"vad": vad, "received": received, "stt_done": time.time()}, args.silence_ms)
        s.say(heard)
        cand = s.messages[-1] if s.messages[-1]["role"] == "candidate" else None
        if not idle.wait(240):
            sys.exit("the interviewer didn't answer")
        deadline = time.time() + 30
        while s.turn and s.turn.has("first_chunk") and not s.turn.has("tts_ready") and time.time() < deadline:
            time.sleep(0.01)
        if s.turn:
            s._publish_latency()
        lat = (cand or {}).get("latency", {})
        reply = next((m for m in s.messages if m["id"] == lat.get("reply_id")), None)
        rows.append(lat)
        print(f"Turn {i + 1}: {secs:.1f} s of audio → “{heard[:70]}…”")
        print("  " + " · ".join(f"{k[:-3]} {v} ms" for k, v in lat.items() if k.endswith("_ms")))
        if reply:
            print(f"  first chunk: “{reply['chunks'][0]}”"
                  + (f"  ({lat['tools_before_speech']} tool call(s) before speaking)"
                     if lat.get("tools_before_speech") else ""))
        # the candidate listens to the whole reply before answering: let its audio finish synthesising
        deadline = time.time() + 60
        while reply and len(reply.get("audio", {})) < len(reply["chunks"]) and time.time() < deadline:
            time.sleep(0.05)

    keys = ["stt_ms", "handoff_ms", "model_ms", "chunk_ms", "tts_ms", "total_ms"]
    print(f"\nMedian over {len(rows)} turns ({args.model}, effort {args.effort}, {args.lang}, pause {args.silence_ms} ms not included):")
    for k in keys:
        vals = [r[k] for r in rows if k in r]
        if vals:
            print(f"  {k[:-3]:<8} {statistics.median(vals):7.0f} ms   (min {min(vals)}, max {max(vals)})")

    usage = cache_usage(s.meta.get("claude_session_id"))
    if usage:
        print("\nPrompt cache per API call (input / cache write / cache read / output tokens):")
        for u in usage:
            print("  " + " / ".join(str(u[k]) for k in ("input_tokens", "cache_creation_input_tokens",
                                                         "cache_read_input_tokens", "output_tokens")))
    print(f"\nCost: ${s.meta.get('cost_usd', 0):.4f}")
    s.stop()
    if not args.keep:
        import shutil
        shutil.rmtree(data, ignore_errors=True)
    else:
        print("Data kept in", data)


if __name__ == "__main__":
    main()
