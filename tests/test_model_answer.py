"""Model answer: stage machine with fake Claude workers and a fake renderer (no model, no Chrome)."""

import json
import os
import threading
import time

import pytest

from app import model_answer as ma
from app import sessions
from app.explainer import script as sc

SETTINGS = {
    "model": "haiku", "feedback_model": "haiku", "feedback_language": "en", "effort": "default", "speed": 1.0,
    "voice_en": "af_heart", "voice_es": "em_alex",
    "ma_script_model": "opus", "ma_scene_model": "sonnet", "ma_qa_model": "sonnet", "ma_budget_usd": 5.0,
    "ma_scenes": 2, "ma_fix_rounds": 1, "ma_name_company": False, "ma_chrome": "auto",
}


def make_script(n=2, say_extra=""):
    scenes = []
    for i in range(1, n + 1):
        scenes.append({"id": f"s{i:02d}", "phase": "design" if i > 1 else "scope", "title": f"Part {i}",
                       "beats": [{"say": f"A token bucket refills at a steady rate. {say_extra}".strip(),
                                  "show": "A bucket fills with tokens."},
                                 {"say": "Each request takes one token, or waits.", "show": "A request takes a token."}]})
    return {"title": "Designing a rate limiter", "description": "How to build a distributed rate limiter.",
            "phases": [{"key": "scope", "label": "Scope"}, {"key": "design", "label": "Design"}],
            "scenes": scenes,
            "diagram": {"title": "Rate limiter", "nodes": [
                {"id": "c", "label": "Client", "col": 0, "row": 0, "color": "muted"},
                {"id": "gw", "label": "API gateway", "sub": "checks the limit", "col": 1, "row": 0},
                {"id": "r", "label": "Redis", "sub": "token buckets", "col": 2, "row": 0, "color": "teal"}],
                "edges": [{"from": "c", "to": "gw", "label": "request"}, {"from": "gw", "to": "r", "label": "INCR"}]},
            "assumptions": ["100 requests per second per user"]}


class FakeClaude:
    """Stands in for `claude -p`: writes what each stage's worker would write."""

    def __init__(self):
        self.calls: list[dict] = []
        self.scripts = [make_script()]   # successive script.json contents (last one repeats)
        self.scene_text = "A token bucket"
        self.qa_issues: list[dict] = []
        self.cost = 0.1
        self.block: threading.Event | None = None  # scene workers wait on it (cancel tests)
        self.fix_edit = True
        self.session_cost: dict[str, float] = {}
        self.lock = threading.RLock()

    def __call__(self, cwd, prompt, *, system, model, tools, allow, deny=(), budget_usd, resume=None, on_start=None):
        with self.lock:
            return self.handle(cwd, prompt, system, model, tools, allow, deny, budget_usd, resume)

    def handle(self, cwd, prompt, system, model, tools, allow, deny, budget_usd, resume):
        call = {"cwd": cwd, "prompt": prompt, "system": system, "model": model, "tools": tools, "allow": allow,
                "deny": list(deny), "budget_usd": budget_usd, "resume": resume}
        self.calls.append(call)
        if cwd.name == "script":
            call["stage"] = "script"
            data = self.scripts[min(len([c for c in self.calls if c.get("stage") == "script"]) - 1, len(self.scripts) - 1)]
            (cwd / "script.json").write_text(json.dumps(data))
        elif prompt.startswith("Your scenes"):
            call["stage"] = "scenes"
            if self.block:
                self.block.wait(10)
            job = prompt.split("Your job file: ")[1].split(".json")[0] + ".json"
            for s in json.loads((cwd / job).read_text())["scenes"]:
                (cwd / "scenes" / f"{s['id']}.js").write_text(
                    f'scene("{s["id"]}", (t, B) => L.text({{x: 960, y: 500, t: "{self.scene_text}"}}));')
        elif prompt.startswith("Review the frames"):
            call["stage"] = "qa"
            (cwd / "out" / "qa" / "qa.json").write_text(json.dumps({"issues": self.qa_issues, "summary": "ok"}))
        else:
            call["stage"] = "fixes"
            if self.fix_edit:
                for sid in {i["scene"] for i in self.qa_issues if f"- {i['scene']}" in prompt}:
                    (cwd / "scenes" / f"{sid}.js").write_text(f'scene("{sid}", (t, B) => "fixed");')
        with self.lock:
            sid = resume or f"sess-{len(self.calls)}"
            self.session_cost[sid] = self.session_cost.get(sid, 0) + self.cost  # reported cumulatively per session
            reported = self.session_cost[sid]
        return {"result": "done", "cost_usd": reported, "session_id": sid, "is_error": False,
                "subtype": "success", "usage": {"input_tokens": 10, "output_tokens": 20}, "duration_s": 0.1}


class FakeNode:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.fail_on: str | None = None

    def __call__(self, cwd, args, env=None, on_line=None, on_start=None):
        self.calls.append(list(args))
        if self.fail_on == args[0]:
            self.fail_on = None
            raise ma.StageError(f"render.mjs {args[0]} failed: boom")
        if args[0] == "svg":
            (cwd / args[2]).write_bytes(b"png")
        elif args[0] == "video":
            (cwd / "out").mkdir(exist_ok=True)
            for sid in args[1:]:
                if on_line:
                    on_line(f"progress {sid} 30 30")
                (cwd / "out" / f"{sid}.mp4").write_bytes(b"mp4")
        elif args[0] == "concat":
            (cwd / args[1]).parent.mkdir(exist_ok=True)
            open(args[1], "wb").write(b"mp4")
        elif args[0] == "qa":
            (cwd / "out" / "qa").mkdir(parents=True, exist_ok=True)
            (cwd / "out" / "qa" / "index.json").write_text("[]")

    def video_calls(self):
        return [c[1:] for c in self.calls if c[0] == "video"]


@pytest.fixture
def fake(monkeypatch):
    claude, node = FakeClaude(), FakeNode()
    settings = dict(SETTINGS)
    monkeypatch.setattr(ma, "run_claude", claude)
    monkeypatch.setattr(ma, "run_node", node)
    monkeypatch.setattr(ma, "explainer_ready", lambda: None)
    monkeypatch.setattr(ma, "load_settings", lambda: settings)
    claude.settings, claude.node = settings, node
    yield claude
    g = ma.current()
    if g:
        g.cancel()


@pytest.fixture
def done_session(events):
    s = sessions.create_session("system-design", "Acme", "Senior", "", "en", "haiku", "haiku", events.append)
    s.meta["status"] = "done"
    s.save()
    (s.dir / "feedback.json").write_text(json.dumps({
        "verdict": "lean hire", "summary": "SECRET SUMMARY", "top3": ["SECRET TOP3 works at Globex"],
        "scores": {"Requirements & scoping": 4, "Estimation": 2, "High-level architecture": 3, "Data model & APIs": None,
                   "Scalability & reliability": 5, "Trade-offs & communication": 4},
        "review": [{"id": 3, "topic": "SECRET TOPIC", "what": "SECRET WHAT"}],
        "report": "## Verdict\nSECRET REPORT: lean hire, didn't land."}))
    (s.dir / "notes.md").write_text(
        "# Scenario: rate limiter\nHidden: 50k requests per second, 100 per minute per key.\n"
        "The candidate works at Globex.\n\n## Observations\n- 03:10 SECRET OBSERVATION: missed atomicity\n")
    (s.dir / "messages.json").write_text(json.dumps([
        {"id": 1, "role": "interviewer", "kind": "speech", "text": "Let's design a rate limiter for a public API.", "ts": 1},
        {"id": 2, "role": "candidate", "kind": "speech", "text": "SECRET CANDIDATE SPEECH", "ts": 2}]))
    (s.dir / "transcript.md").write_text("SECRET TRANSCRIPT")
    return s


def run(s, mode="video", fresh=False):
    events = []
    g = ma.Generation(s.dir, s.meta, events.append, mode, fresh)
    g.run()
    return g.state, events


# ------------------------------------------------------------------ stage machine

def test_video_runs_every_stage_in_order(fake, done_session):
    fake.qa_issues = [{"scene": "s02", "t": 1.0, "severity": "high", "kind": "layout", "issue": "overlap", "fix": "move"}]
    state, events = run(done_session)
    assert state["status"] == "done", state["error"]
    assert list(state["stages"]) == ma.STAGES["video"]
    assert all(st["status"] == "done" for st in state["stages"].values())
    assert [c["stage"] for c in fake.calls] == ["script", "scenes", "scenes", "qa", "fixes"]
    assert [c["model"] for c in fake.calls] == ["opus", "sonnet", "sonnet", "sonnet", "sonnet"]
    # the cost of every run is recorded and summed
    assert len(state["runs"]) == 5 and state["cost_usd"] == pytest.approx(0.5)
    assert all(r["usage"]["output_tokens"] == 20 for r in state["runs"])
    # outputs
    d = done_session.dir / "model_answer"
    for f in ("model-answer.mp4", "youtube.txt", "script.md", "diagram.svg", "diagram.png", "work/timings.json"):
        assert (d / f).exists(), f
    yt = (d / "youtube.txt").read_text()
    assert "0:00 Part 1" in yt and "Designing a rate limiter" in yt and "100 requests per second" in yt
    assert set(state["outputs"]) == {"video", "youtube", "script", "diagram_svg", "diagram_png"}
    assert [c["title"] for c in state["chapters"]] == ["Part 1", "Part 2"]
    # progress went out over SSE
    assert any(e["type"] == "model_answer" and e["state"]["stages"]["render"]["status"] == "running" for e in events)
    pub = ma.public(done_session.id, state)
    assert pub["outputs"]["video"]["name"] == "designing-a-rate-limiter.mp4"
    assert done_session.id not in pub["outputs"]["video"]["name"]


def test_script_worker_reads_only_bounded_inputs(fake, done_session):
    run(done_session, "diagram")
    call = fake.calls[0]
    d = call["cwd"]
    files = sorted(p.name for p in d.iterdir())
    assert files == ["brief.md", "emphasis.md", "format.md", "scenario.md", "script.json"]
    text = "".join(p.read_text() for p in d.iterdir())
    # no transcript, verdict, evaluation free text, observations or personal details
    for secret in ("SECRET", "lean hire", "Globex", "didn't land", "03:10"):
        assert secret not in text, secret
    emphasis = (d / "emphasis.md").read_text()
    assert emphasis.index("Estimation") < emphasis.index("High-level architecture") < emphasis.index("Data model & APIs")
    assert "Explain in depth" in emphasis and "Scalability & reliability" in emphasis.split("Cover clearly")[1]
    scenario = (d / "scenario.md").read_text()
    assert "Let's design a rate limiter" in scenario and "50k requests per second" in scenario
    # tools and path-scoped permissions
    assert call["tools"] == ["Read", "Write"]
    root = "/" + str(d.resolve())
    assert call["allow"] == [f"Read({root}/**)", f"Edit({root}/script.json)"]
    assert "Never name Acme" in call["system"]


def test_scenario_without_sections_drops_remarks(fake, done_session):
    (done_session.dir / "notes.md").write_text(
        "Problem: URL shortener, 10M links a day.\nCandidate seemed nervous.\nThey asked about TTLs at 04:12.\n"
        "Keys are 7 characters.\n## Observations so far\n- strong on estimates\n")
    g = ma.Generation(done_session.dir, done_session.meta, lambda e: None, "diagram")
    text = g.scenario()
    assert "10M links a day" in text and "7 characters" in text
    assert "nervous" not in text and "04:12" not in text and "strong on estimates" not in text


def test_scene_workers_are_bounded(fake, done_session):
    run(done_session)
    scene_calls = [c for c in fake.calls if c["stage"] == "scenes"]
    assert len(scene_calls) == 2
    work = done_session.dir / "model_answer" / "work"
    root = "/" + str(work.resolve())
    for c in scene_calls:
        assert c["tools"] == ["Read", "Write", "Edit", "Bash"]
        assert c["allow"] == [f"Read({root}/**)", f"Edit({root}/scenes/**)",
                              "Bash(node render.mjs beats:*)", "Bash(node render.mjs preview:*)"]
        for f in ("render.mjs", "lib.js", "player.html", "reference.js", "BRIEF.md", ".render_limits.json"):
            assert f"Write({root}/{f})" in c["deny"] and f"Edit({root}/{f})" in c["deny"]
        assert c["budget_usd"] <= (5.0 - 0.1) / 2 + 1e-9
    for f in ("render.mjs", "lib.js", "player.html", "reference.js", "BRIEF.md", "script.json", "timings.json"):
        assert not os.access(work / f, os.W_OK), f  # read-only on disk too
    assert json.loads((work / ".render_limits.json").read_text()) == {"s01": 2, "s02": 2}
    qa = next(c for c in fake.calls if c["stage"] == "qa")
    assert qa["tools"] == ["Read", "Write"]
    assert qa["allow"] == [f"Read({root}/out/qa/**)", f"Edit({root}/out/qa/qa.json)"]
    assert qa["deny"] == [f"Read({root}/scenes/**)"]
    assert "Render limit reached" in (work / "render.mjs").read_text()
    jobs = [json.loads(p.read_text()) for p in sorted((work / "jobs").glob("*.json"))]
    assert [[s["id"] for s in j["scenes"]] for j in jobs] == [["s01"], ["s02"]]
    assert {"start", "end", "say", "show"} <= set(jobs[0]["scenes"][0]["beats"][0])
    assert not (work / "feedback.json").exists() and not list(work.glob("*.md"))[1:]  # only BRIEF.md
    assert "import" in (work / "render.mjs").read_text()


def test_diagram_mode_runs_only_the_script_step(fake, done_session):
    state, _ = run(done_session, "diagram")
    assert state["status"] == "done"
    assert list(state["stages"]) == ["script", "diagram", "package"]
    assert [c["stage"] for c in fake.calls] == ["script"]
    assert set(state["outputs"]) == {"script", "diagram_svg", "diagram_png"}
    d = done_session.dir / "model_answer"
    assert (d / "diagram.svg").read_text().startswith("<svg")
    assert "![Rate limiter](diagram.svg)" in (d / "script.md").read_text()
    assert not (d / "work" / "audio").exists()
    assert [c[0] for c in fake.node.calls] == ["svg"]


def test_video_after_diagram_reuses_the_script(fake, done_session):
    run(done_session, "diagram")
    state, _ = run(done_session, "video")
    assert state["status"] == "done"
    assert [c["stage"] for c in fake.calls].count("script") == 1
    assert "youtube" in state["outputs"]


def test_script_bounced_once_then_accepted(fake, done_session):
    fake.scripts = [make_script(say_extra="The candidate forgot this."), make_script()]
    state, _ = run(done_session, "diagram")
    assert state["status"] == "done"
    script_calls = [c for c in fake.calls if c["stage"] == "script"]
    assert len(script_calls) == 2
    assert script_calls[1]["resume"] == "sess-1"
    assert '"candidate" (refers to a candidate)' in script_calls[1]["prompt"]


def test_script_bounced_once_then_fails(fake, done_session):
    fake.scripts = [make_script(say_extra="You missed the cache.")]
    state, _ = run(done_session, "diagram")
    assert state["status"] == "failed"
    assert state["stages"]["script"]["status"] == "failed"
    assert "after one revision" in state["error"] and "You missed" in state["error"]
    assert len(fake.calls) == 2  # one write, one revision, no more
    assert not (done_session.dir / "model_answer" / "script.md").exists()


def test_company_name_is_rejected_unless_allowed(fake, done_session):
    fake.scripts = [make_script(say_extra="Acme does it this way.")]
    state, _ = run(done_session, "diagram")
    assert state["status"] == "failed" and "names the target company" in state["error"]
    fake.calls.clear()
    fake.settings["ma_name_company"] = True
    state, _ = run(done_session, "diagram", fresh=True)
    assert state["status"] == "done"
    assert "You may name Acme" in fake.calls[0]["system"]


def test_invalid_schema_is_bounced(fake, done_session):
    bad = make_script()
    del bad["diagram"]
    fake.scripts = [bad, make_script()]
    state, _ = run(done_session, "diagram")
    assert state["status"] == "done"
    assert "diagram must be an object" in fake.calls[1]["prompt"]


def test_budget_cap_aborts_cleanly(fake, done_session):
    fake.cost = 2.0
    fake.settings["ma_budget_usd"] = 3.0
    state, _ = run(done_session)
    assert state["status"] == "failed"
    assert state["stages"]["scenes"]["status"] == "failed"
    assert "budget cap of $3.00" in state["error"]
    # each scene worker was capped at half of what was left
    assert all(c["budget_usd"] == pytest.approx(0.5) for c in fake.calls if c["stage"] == "scenes")
    assert state["cost_usd"] in (pytest.approx(4.0), pytest.approx(6.0))  # the 2nd worker may not start at all


def test_no_worker_starts_without_budget(fake, done_session):
    fake.settings["ma_budget_usd"] = 0.0
    state, _ = run(done_session, "diagram")
    assert state["status"] == "failed" and "budget cap" in state["error"]
    assert fake.calls == []


def test_budget_subtype_from_claude_aborts(fake, done_session, monkeypatch):
    def over(*a, **k):
        return {"result": "", "cost_usd": 0.4, "session_id": "x", "is_error": True, "subtype": "error_max_budget_usd",
                "usage": {}}
    monkeypatch.setattr(ma, "run_claude", over)
    state, _ = run(done_session, "diagram")
    assert state["status"] == "failed" and "budget cap" in state["error"]


def wait_for(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_cancel_then_retry_from_the_stage(fake, done_session):
    fake.block = threading.Event()
    ma.start(done_session.dir, done_session.meta, lambda e: None, "video")
    assert wait_for(lambda: any(c["stage"] == "scenes" for c in fake.calls))
    with pytest.raises(ma.Busy):
        ma.start(done_session.dir, done_session.meta, lambda e: None, "video")
    assert ma.cancel(done_session.id)
    fake.block.set()
    assert wait_for(lambda: ma.current() is None)
    state = ma.status(done_session.dir)["state"]
    assert state["status"] == "cancelled"
    assert state["stages"]["scenes"]["status"] == "cancelled"
    assert state["stages"]["script"]["status"] == "done"
    # retry continues from the scenes: the script isn't written again
    fake.block = None
    fake.calls.clear()
    ma.start(done_session.dir, done_session.meta, lambda e: None, "video")
    assert wait_for(lambda: ma.current() is None)
    state = ma.status(done_session.dir)["state"]
    assert state["status"] == "done", state["error"]
    assert "script" not in [c["stage"] for c in fake.calls]


def test_retry_from_failed_render(fake, done_session):
    fake.node.fail_on = "video"
    state, _ = run(done_session)
    assert state["status"] == "failed" and state["stages"]["render"]["status"] == "failed"
    assert "boom" in state["error"]
    n_calls = len(fake.calls)
    state, _ = run(done_session)
    assert state["status"] == "done"
    assert [c["stage"] for c in fake.calls[n_calls:]] == ["qa"]  # script, voice, scenes not redone


def test_interrupted_generation_becomes_retryable(fake, done_session):
    d = done_session.dir / "model_answer"
    d.mkdir()
    (d / "state.json").write_text(json.dumps({"mode": "diagram", "status": "running", "cost_usd": 0, "runs": [],
                                              "stages": {"script": {"status": "running"}, "diagram": {"status": "pending"},
                                                         "package": {"status": "pending"}}}))
    state = ma.status(done_session.dir)["state"]
    assert state["status"] == "failed" and "Interrupted" in state["error"]
    assert state["stages"]["script"]["status"] == "failed"


def test_fixes_rerender_only_touched_scenes(fake, done_session):
    fake.qa_issues = [{"scene": "s02", "t": 2.0, "severity": "medium", "kind": "mismatch", "issue": "says 200", "fix": "100"},
                      {"scene": "s01", "t": 1.0, "severity": "low", "kind": "layout", "issue": "tight", "fix": "space"}]
    state, _ = run(done_session)
    assert state["status"] == "done"
    assert fake.node.video_calls() == [["s01", "s02"], ["s02"]]
    fix = [c for c in fake.calls if c["stage"] == "fixes"]
    owner = next(w for w in state["workers"].values() if "s02" in w["scenes"])
    assert len(fix) == 1 and fix[0]["resume"] == owner["session_id"]  # the worker that wrote s02
    assert json.loads((done_session.dir / "model_answer" / "work" / ".render_limits.json").read_text())["s02"] == 1
    assert "says 200" in fix[0]["prompt"] and "tight" not in fix[0]["prompt"]


def test_no_fix_round_when_qa_is_clean(fake, done_session):
    fake.qa_issues = [{"scene": "s01", "severity": "low", "kind": "layout", "issue": "tight", "fix": "space"}]
    state, _ = run(done_session)
    assert state["stages"]["fixes"]["detail"] == "nothing to fix"
    assert "fixes" not in [c["stage"] for c in fake.calls]


def test_on_screen_text_neutrality_is_enforced(fake, done_session):
    fake.scene_text = "What the candidate missed"
    fake.fix_edit = False
    state, _ = run(done_session)
    issues = state["qa"]["issues"]
    assert any(i["kind"] == "neutrality" and i["severity"] == "high" for i in issues)
    assert state["status"] == "failed" and "still breaks the publishing rules" in state["error"]
    assert any(c["stage"] == "fixes" for c in fake.calls)


def test_missing_scene_fails_and_retry_writes_only_it(fake, done_session, monkeypatch):
    real = fake.__call__

    def lazy(cwd, prompt, **kw):
        r = real(cwd, prompt, **kw)
        if prompt.startswith("Your scenes: s02"):
            (cwd / "scenes" / "s02.js").unlink()
        return r
    monkeypatch.setattr(ma, "run_claude", lazy)
    state, _ = run(done_session)
    assert state["status"] == "failed" and "didn't write s02" in state["error"]
    monkeypatch.setattr(ma, "run_claude", fake)
    fake.calls.clear()
    state, _ = run(done_session)
    assert state["status"] == "done"
    scene_calls = [c for c in fake.calls if c["stage"] == "scenes"]
    assert len(scene_calls) == 1 and scene_calls[0]["prompt"].startswith("Your scenes: s02.")


def test_voice_timings_follow_the_beats(fake, done_session):
    run(done_session)
    t = json.loads((done_session.dir / "model_answer" / "work" / "timings.json").read_text())
    b = t["s01"]["beats"]
    assert b[0]["start"] == pytest.approx(ma.LEAD)
    assert b[1]["start"] == pytest.approx(b[0]["end"] + ma.GAP, abs=1e-3)
    assert t["s01"]["duration"] == pytest.approx(b[1]["end"] + ma.TAIL, abs=1e-3)


# ------------------------------------------------------------------ claude flags

def test_run_claude_flags(monkeypatch, tmp_path):
    seen = {}

    class P:
        returncode = 0

        def __init__(self, args, **kw):
            seen["args"], seen["cwd"] = args, kw["cwd"]

        def communicate(self, timeout=None):
            return json.dumps({"result": "ok", "total_cost_usd": 0.02, "session_id": "abc", "usage": {}}), ""

    monkeypatch.setattr(ma.subprocess, "Popen", P)
    r = ma.run_claude(tmp_path, "go", system="sys", model="opus", tools=["Read", "Write"],
                      allow=["Read(//x/**)", "Write(//x/script.json)"], deny=["Write(//x/render.mjs)"],
                      budget_usd=1.234, resume="r1")
    a = seen["args"]
    assert a[:3] == ["claude", "-p", "go"]
    assert a[a.index("--setting-sources") + 1] == "" and "--strict-mcp-config" in a and "--bare" not in a
    assert a[a.index("--permission-mode") + 1] == "default"  # unlisted actions are refused, never auto-accepted
    i = a.index("--allowedTools")
    assert a[i + 1:i + 3] == ["Read(//x/**)", "Write(//x/script.json)"]
    assert a[a.index("--disallowedTools") + 1] == "Write(//x/render.mjs)"
    t = a.index("--tools")
    assert a[t + 1:t + 3] == ["Read", "Write"]
    assert a[a.index("--max-budget-usd") + 1] == "1.23"
    assert a[a.index("--resume") + 1] == "r1" and a[a.index("--model") + 1] == "opus"
    assert r["cost_usd"] == 0.02 and r["session_id"] == "abc" and not r["is_error"]
    assert ma.path_rule("Read", tmp_path, "**") == f"Read(/{tmp_path.resolve()}/**)"


def test_resumed_runs_count_only_their_own_cost(fake, done_session):
    fake.scripts = [make_script(say_extra="The candidate should ask."), make_script()]
    fake.cost = 0.25
    state, _ = run(done_session, "diagram")
    first, revision = [c for c in fake.calls if c["stage"] == "script"]
    # Claude Code reports 0.50 for the resumed session (cumulative); only 0.25 is new
    assert [r["cost_usd"] for r in state["runs"]] == [0.25, 0.25]
    assert state["cost_usd"] == pytest.approx(0.5)
    # and its --max-budget-usd is compared with the session's cumulative cost: prior + what's left
    assert revision["budget_usd"] == pytest.approx(0.25 + (5.0 - 0.25))


def test_fix_round_is_bounded_across_retries(fake, done_session):
    fake.qa_issues = [{"scene": "s02", "t": 1.0, "severity": "high", "kind": "layout", "issue": "overlap", "fix": "move"}]
    fake.node.fail_on = None
    real = fake.node.__call__
    calls = {"n": 0}

    def fail_after_fix(cwd, args, **kw):
        if args[0] == "video" and any(c["stage"] == "fixes" for c in fake.calls) and calls["n"] == 0:
            calls["n"] += 1
            raise ma.StageError("render.mjs video failed: boom")
        return real(cwd, args, **kw)
    import app.model_answer as mod
    mod.run_node = fail_after_fix
    state, _ = run(done_session)
    assert state["status"] == "failed" and state["stages"]["fixes"]["status"] == "failed"
    state, _ = run(done_session)
    assert state["status"] == "done"
    assert [c["stage"] for c in fake.calls].count("fixes") == 1  # the retry re-rendered, it didn't fix again
    assert "already used" in state["stages"]["fixes"]["detail"]


def test_qa_summary_counts_frames_in_code(fake, done_session, monkeypatch):
    real = fake.node.__call__

    def node(cwd, args, **kw):
        real(cwd, args, **kw)
        if args[0] == "qa":
            (cwd / "out" / "qa" / "index.json").write_text(json.dumps([{"file": f"f{i}"} for i in range(7)]))
    monkeypatch.setattr(ma, "run_node", node)
    state, _ = run(done_session)
    assert state["qa"]["frames"] == 7 and state["qa"]["summary"].startswith("7 frames reviewed")


def test_diagram_falls_back_to_svg_when_png_render_fails(fake, done_session):
    fake.node.fail_on = "svg"
    state, _ = run(done_session, "diagram")
    assert state["status"] == "done"
    assert set(state["outputs"]) == {"script", "diagram_svg"}
    assert "SVG only" in state["stages"]["diagram"]["detail"]


def test_png_needs_no_ffmpeg(fake, done_session, monkeypatch):
    monkeypatch.setattr(ma, "renderer_ready", lambda: None)
    monkeypatch.setattr(ma.shutil, "which", lambda name: None if name == "ffmpeg" else "/usr/bin/" + name)
    state, _ = run(done_session, "diagram")
    assert "diagram_png" in state["outputs"]


def test_pack_gating():
    assert ma.allowed({"whiteboard": True}) and not ma.allowed({"whiteboard": False})
    assert not ma.allowed({"whiteboard": True, "model_answer": False})
    assert ma.allowed({"whiteboard": False, "model_answer": True})
    beh = sessions.create_session("behavioural", "", "", "", "en", "haiku", "haiku", lambda e: None)
    assert beh.meta["model_answer"] is False
    sd = sessions.create_session("system-design", "", "", "", "en", "haiku", "haiku", lambda e: None)
    assert sd.meta["model_answer"] is True


# ------------------------------------------------------------------ script helpers

def test_validate_accepts_a_good_script():
    assert sc.validate(make_script(), 2) == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda s: s.pop("title"), "title must be"),
    (lambda s: s["scenes"].append(s["scenes"][0]), "scenes must be a list of 1-2"),
    (lambda s: s["scenes"][0].update(id="scene1"), 'like "s01"'),
    (lambda s: s["scenes"][0].update(phase="nope"), "phase must be one of"),
    (lambda s: s["scenes"][0]["beats"].pop(), "2-7 beats"),
    (lambda s: s["scenes"][0]["beats"][0].update(say=""), "say must be"),
    (lambda s: s["diagram"]["nodes"][1].update(col=0, row=0), "shares its cell"),
    (lambda s: s["diagram"]["edges"].append({"from": "c", "to": "zz"}), "connect two node ids"),
    (lambda s: s["scenes"][0]["beats"][0].update(say="word " * 400), "words"),
])
def test_validate_rejects(mutate, expected):
    s = make_script()
    mutate(s)
    assert any(expected in e for e in sc.validate(s, 2)), sc.validate(s, 2)


FLAGGED = [
    "The candidate should have asked about scale.", "In this session we design a cache.",
    "A common mistake is to skip estimates.", "You said Redis, but", "Your answer missed the queue.",
    "That would get a hire verdict.", "The feedback was clear.", "During the mock round",
    "the interviewer pushes on it", "A common pitfall in interviews", "In a system design interview, start here.",
    "You never mentioned the cache.", "Earlier you talked about sharding.", "Here you went straight to the database.",
    "Practice round recap", "That would get a strong yes.", "You scored a 3 here.",
    "No mencionaste la caché.", "Te faltó la cola.", "Un error común es olvidar la caché.", "En esta entrevista",
    "El candidato olvidó la caché.",
]

ALLOWED = [
    "You'd want a queue here.", "The user session token expires after an hour.", "Store session state in Redis.",
    "In Raft, a follower becomes a candidate and requests votes.", "El nodo pasa a candidato y pide votos.",
    "Candidate keys identify a row.", "The candidate generation step retrieves 500 items.",
    "Add a forgot password flow.", "A missed heartbeat triggers failover.", "The job missed its deadline.",
    "A relevance score ranks results.", "La puntuación de relevancia ordena los resultados.",
    "The session expires after 30 minutes.", "Mock the payment API in tests.", "A mock server helps testing.",
    "User feedback loops improve ranking.", "Customers hire workers by the hour.", "Hiring managers review applicants.",
    "If you said yes to every request, the queue would grow.", "Cache misses fall through to the database.",
]


@pytest.mark.parametrize("text", FLAGGED)
def test_neutrality_flags(text):
    assert sc.check_text(text), text


@pytest.mark.parametrize("text", ALLOWED)
def test_neutrality_allows_technical_language(text):
    assert sc.check_text(text) == [], sc.check_text(text)


@pytest.mark.parametrize("variant", ["Acme Corp", "acme-corp", "AcmeCorp", "ACME.corp"])
def test_company_variants(variant):
    assert any("names the target company" in v for v in sc.check_text(f"{variant} does this", "AcmeCorp"))


def test_scene_literals_keep_single_words():
    src = 's += text({ t: "Verdict: hire" }); s += chip({ t: "Mock" }); s += chip({ t: "#5b9cff" });'
    lits = sc.scene_literals(src)
    assert "Verdict: hire" in lits and "Mock" in lits and "#5b9cff" not in lits
    assert all(sc.check_text(x) for x in ("Verdict: hire", "Mock"))


def test_neutrality_covers_every_published_field():
    s = make_script()
    s["diagram"]["nodes"][0]["label"] = "Candidate"
    s["phases"][0]["label"] = "Mock round"
    s["assumptions"].append("Acme has 1M users")
    found = sc.neutrality(s, "Acme")
    assert any(f.startswith("diagram node c") for f in found)
    assert any(f.startswith("phases[0].label") for f in found)
    assert any("Acme" in f for f in found)


def test_scene_literals_pick_on_screen_words():
    src = 'const a = "#5b9cff"; s += text({ t: "Reads outnumber writes", size: 30 }); s += `<rect x="1"/>`;'
    assert "Reads outnumber writes" in sc.scene_literals(src) and "#5b9cff" not in sc.scene_literals(src)


def test_youtube_text_and_markdown():
    s = make_script()
    timings = {"s01": {"duration": 65.2}, "s02": {"duration": 30}}
    yt = sc.youtube(s, timings)
    assert "0:00 Part 1\n1:05 Part 2" in yt
    md = sc.markdown(s)
    assert md.startswith("# Designing a rate limiter") and "## 2. Part 2" in md and "## Assumed figures" in md


def test_diagram_svg_is_well_formed():
    import xml.etree.ElementTree as ET
    svg = sc.diagram_svg(make_script()["diagram"])
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg") and "API gateway" in svg and "INCR" in svg


def test_estimates():
    from app.config import DEFAULTS
    e_video = ma.estimate("video", SETTINGS | {"ma_scenes": 8})
    e_diagram = ma.estimate("diagram", SETTINGS | {"ma_scenes": 8})
    assert 0 < e_diagram["usd"][1] < e_video["usd"][0]
    # the default cap covers a pessimistic full video
    assert e_video["usd"][0] < e_video["typical_usd"] < e_video["usd"][1] <= DEFAULTS["model_answer"]["budget_usd"]
    assert 3 <= e_video["typical_usd"] <= 6 and 0.4 <= e_diagram["typical_usd"] <= 0.9
    assert set(e_diagram["tokens"]) == {"opus"}
    assert e_video["minutes"] == [17, 25]


# ------------------------------------------------------------------ API

def test_api_rules(fake, done_session, monkeypatch):
    from fastapi.testclient import TestClient

    from app import server
    monkeypatch.setattr(server, "sessions", {})  # interviews left live by other tests
    c = TestClient(server.app)
    r = c.get(f"/api/sessions/{done_session.id}/model-answer")
    assert r.status_code == 200 and r.json()["state"] is None and set(r.json()["estimates"]) == {"video", "diagram"}
    assert c.post(f"/api/sessions/{done_session.id}/model-answer", json={"mode": "nope"}).status_code == 422

    other = sessions.create_session("system-design", "", "", "", "en", "haiku", "haiku", lambda e: None)
    assert c.post(f"/api/sessions/{other.id}/model-answer", json={"mode": "video"}).status_code == 409  # not evaluated

    live = server.get(other.id)
    live.meta["status"] = "live"
    try:
        r = c.post(f"/api/sessions/{done_session.id}/model-answer", json={"mode": "diagram"})
        assert r.status_code == 409 and "interview is in progress" in r.json()["detail"]
    finally:
        live.meta["status"] = "ended"

    r = c.post(f"/api/sessions/{done_session.id}/model-answer", json={"mode": "diagram"})
    assert r.status_code == 200
    assert wait_for(lambda: ma.current() is None)
    state = c.get(f"/api/sessions/{done_session.id}/model-answer").json()["state"]
    assert state["status"] == "done"
    assert c.get(state["outputs"]["diagram_svg"]["url"]).text.startswith("<svg")
    # cancel after the end is a no-op: it never deletes the result
    r = c.post(f"/api/sessions/{done_session.id}/model-answer/cancel")
    assert r.status_code == 200 and r.json()["cancelled"] is False
    assert c.get(f"/api/sessions/{done_session.id}/model-answer").json()["state"]["status"] == "done"
    assert c.delete(f"/api/sessions/{done_session.id}/model-answer").status_code == 200
    assert c.get(f"/api/sessions/{done_session.id}/model-answer").json()["state"] is None

    beh = sessions.create_session("behavioural", "", "", "", "en", "haiku", "haiku", lambda e: None)
    (beh.dir / "feedback.json").write_text("{}")
    r = c.post(f"/api/sessions/{beh.id}/model-answer", json={"mode": "diagram"})
    assert r.status_code == 409 and "no model answer" in r.json()["detail"]
    assert c.get(f"/api/sessions/{beh.id}/model-answer").json()["allowed"] is False


def test_api_blocks_cpu_work_while_generating(fake, done_session, monkeypatch):
    from fastapi.testclient import TestClient

    from app import server
    monkeypatch.setattr(server, "sessions", {})
    busy = type("Busy", (), {"cancel": lambda self: None})()
    monkeypatch.setattr(ma, "current", lambda: busy)
    c = TestClient(server.app)
    stopped = sessions.create_session("system-design", "", "", "", "en", "haiku", "haiku", lambda e: None)
    stopped.meta["status"] = "ended"
    stopped.save()
    for path in ("resume", "end"):
        r = c.post(f"/api/sessions/{stopped.id}/{path}", json={})
        assert r.status_code == 409 and "model answer is being generated" in r.json()["detail"], path
    r = c.post("/api/sessions", json={"pack": "system-design"})
    assert r.status_code == 409
