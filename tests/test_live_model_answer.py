"""Opt-in: a short real model-answer video, end to end (Claude Code with haiku for every stage, Kokoro,
headless Chrome, ffmpeg). Two scenes, a tight budget; takes a few minutes.

    MOCKSY_LIVE=1 .venv/bin/pytest tests/test_live_model_answer.py -s
"""

import json
import os
import subprocess

import pytest

from app import model_answer as ma
from app import sessions, speech
from app.explainer import script as sc

REAL_SYNTHESIZE = speech.synthesize  # conftest swaps in a silent fake per test

pytestmark = pytest.mark.skipif(not os.environ.get("MOCKSY_LIVE"), reason="set MOCKSY_LIVE=1 to run")

BUDGET = 1.5


def test_short_real_video(monkeypatch):
    if reason := ma.explainer_ready():
        pytest.skip(reason)
    monkeypatch.setattr(speech, "synthesize", REAL_SYNTHESIZE)
    settings = ma.load_settings() | {
        "ma_script_model": "haiku", "ma_scene_model": "haiku", "ma_qa_model": "haiku",
        "ma_scenes": 2, "ma_budget_usd": BUDGET, "ma_fix_rounds": 1, "ma_name_company": False,
    }
    monkeypatch.setattr(ma, "load_settings", lambda: settings)

    s = sessions.create_session("system-design", "Initech", "Senior", "", "en", "haiku", "haiku", lambda e: None,
                                problem="Design a rate limiter for a public API")
    s.meta["status"] = "done"
    s.save()
    (s.dir / "notes.md").write_text("Scenario: rate limiter for a public REST API, 50k requests per second at peak, "
                                    "limits per API key, several gateway instances.")
    (s.dir / "feedback.json").write_text(json.dumps({
        "verdict": "lean hire", "summary": "Reasonable design.",
        "top3": ["Explain why the counter must be shared across gateway instances", "Compare token bucket and sliding window"],
        "review": [{"id": 4, "topic": "Atomicity", "what": "Use an atomic INCR with expiry (or a Lua script) in Redis."}],
        "report": "## Verdict\nLean hire.\n## Top 3\n- shared counters\n- algorithms\n- failure mode when Redis is down"}))

    g = ma.Generation(s.dir, s.meta, lambda e: None, "video")
    g.run()
    st = g.state
    print(f"\nlive model answer: {st['status']}, ${st['cost_usd']:.3f}, folder {g.dir}")
    for r in st["runs"]:
        print(f"  {r['stage']:8} {r['worker']:16} {r['model']:6} ${r['cost_usd']:.4f} {r['duration_s']}s {r['usage']}")
    print("  stages:", {k: (v["status"], v.get("detail")) for k, v in st["stages"].items()})
    print("  QA issues:", len((st.get("qa") or {}).get("issues", [])))
    assert st["status"] == "done", st["error"]
    assert st["cost_usd"] <= BUDGET

    d = g.dir
    for f in ("model-answer.mp4", "youtube.txt", "script.md", "diagram.svg", "diagram.png"):
        assert (d / f).stat().st_size > 0, f
    script = json.loads((d / "script.json").read_text())
    assert sc.neutrality(script, "Initech") == []
    assert "Initech" not in (d / "youtube.txt").read_text()
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height", "-show_chapters", "-of", "json",
         str(d / "model-answer.mp4")], capture_output=True, text=True, check=True).stdout)
    codecs = {x["codec_name"] for x in info["streams"]}
    assert {"h264", "aac"} <= codecs
    assert len(info["chapters"]) == len(script["scenes"])
