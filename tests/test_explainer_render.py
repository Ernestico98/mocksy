"""Renders the reference scene for real (headless Chrome + ffmpeg); skipped when they aren't installed."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app import model_answer as ma
from app.explainer import script as sc


def chrome_available() -> bool:
    paths = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome",
             "/usr/bin/chromium", "/usr/bin/chromium-browser"]
    caches = [Path.home() / "Library/Caches/ms-playwright", Path.home() / ".cache/ms-playwright"]
    return any(os.path.exists(p) for p in paths) or any(c.exists() and any(c.glob("chromium*")) for c in caches)


pytestmark = pytest.mark.skipif(
    ma.explainer_ready() is not None or not shutil.which("ffprobe") or not chrome_available(),
    reason="needs node, ffmpeg, the renderer's node_modules and Chrome/Chromium")


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,codec_type,width,height,r_frame_rate:"
                          "format=duration:format_tags=title", "-show_chapters", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_render_reference_scene(tmp_path):
    work = tmp_path / "work"
    (work / "scenes").mkdir(parents=True)
    (work / "audio").mkdir()
    shutil.copy(ma.EXPLAINER / "reference.js", work / "scenes" / "ref.js")
    script = {"title": "The read path", "phases": [{"key": "design", "label": "Design"}],
              "scenes": [{"id": "ref", "phase": "design", "title": "Read path",
                          "beats": [{"say": "One.", "show": ""}, {"say": "Two.", "show": ""}, {"say": "Three.", "show": ""}]}]}
    timings = {"ref": {"duration": 1.5, "beats": [{"start": 0.1, "end": 0.5, "say": "One."},
                                                   {"start": 0.6, "end": 1.0, "say": "Two."},
                                                   {"start": 1.1, "end": 1.4, "say": "Three."}]}}
    (work / "script.json").write_text(json.dumps(script))
    (work / "timings.json").write_text(json.dumps(timings))
    sf.write(work / "audio" / "ref.wav", np.zeros(36000, dtype=np.float32), 24000)

    lines = []
    ma.run_node(work, ["video", "ref"], on_line=lines.append)
    assert any(l.startswith("progress ref 45 45") for l in lines)
    out = tmp_path / "final.mp4"
    ma.run_node(work, ["concat", str(out)])
    info = probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"], video["r_frame_rate"]) == ("h264", 1920, 1080, "30/1")
    assert audio["codec_name"] == "aac"
    assert info["format"]["tags"]["title"] == "The read path"
    assert [c["tags"]["title"] for c in info["chapters"]] == ["Read path"]

    ma.run_node(work, ["qa"])
    index = json.loads((work / "out" / "qa" / "index.json").read_text())
    assert [f["beat"] for f in index] == [1, 2, 3] and not any(f.get("error") for f in index)
    assert (work / index[0]["file"]).stat().st_size > 1000

    svg = tmp_path / "diagram.svg"
    svg.write_text(sc.diagram_svg({"title": "Demo", "nodes": [{"id": "a", "label": "A", "col": 0, "row": 0},
                                                              {"id": "b", "label": "B", "col": 1, "row": 0}],
                                   "edges": [{"from": "a", "to": "b", "label": "calls"}]}))
    ma.run_node(tmp_path, ["svg", str(svg), str(tmp_path / "diagram.png")])
    assert (tmp_path / "diagram.png").read_bytes()[:4] == b"\x89PNG"


def test_render_error_is_reported(tmp_path):
    with pytest.raises(ma.StageError, match="render.mjs preview failed"):
        ma.run_node(tmp_path, ["preview", "nope", "1"])


def test_shim_enforces_render_rounds(tmp_path):
    work = tmp_path / "work"
    (work / "scenes").mkdir(parents=True)
    shutil.copy(ma.EXPLAINER / "reference.js", work / "scenes" / "ref.js")
    (work / "script.json").write_text(json.dumps({"title": "T", "phases": [], "scenes": [
        {"id": "ref", "phase": "x", "title": "Ref", "beats": [{"say": "a", "show": ""}] * 3}]}))
    (work / "timings.json").write_text(json.dumps({"ref": {"duration": 1.5, "beats": [
        {"start": 0.1, "end": 0.5, "say": "a"}, {"start": 0.6, "end": 1.0, "say": "b"}, {"start": 1.1, "end": 1.4, "say": "c"}]}}))
    (work / "render.mjs").write_text(ma.SHIM.replace("RENDERER_URL", json.dumps((ma.EXPLAINER / "render.mjs").as_uri())))
    (work / ".render_limits.json").write_text(json.dumps({"ref": 2}))
    run = lambda: subprocess.run(["node", "render.mjs", "preview", "ref", "1"], cwd=work, capture_output=True, text=True)  # noqa: E731
    assert run().returncode == 0 and run().returncode == 0
    third = run()
    assert third.returncode == 3 and "Render limit reached for ref" in third.stderr
    assert json.loads((work / ".render_counts.json").read_text()) == {"ref": 2}
