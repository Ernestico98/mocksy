"""The streaming parser that splits the interviewer's output into speech and actions."""

import pytest

from app.interviewer import TagStream, claude_args


def run(text: str, step: int = 5):
    out = []
    p = TagStream(lambda kind, value: out.append((kind, value)))
    for i in range(0, len(text), step):  # simulate token-sized deltas
        p.feed(text[i:i + step])
    p.close()
    return out


@pytest.mark.parametrize("step", [1, 3, 7, 50])
def test_speech_split_into_sentences_regardless_of_chunking(step):
    out = run("Thanks for joining today, it's great to meet you. Let's start with a question. "
              "How would you approach it?", step)
    assert out == [
        ("speech", "Thanks for joining today, it's great to meet you."),
        ("speech", "Let's start with a question."),
        ("speech", "How would you approach it?"),
    ]


def test_short_fragments_are_kept_with_the_next_sentence():
    assert run("Okay. Sounds good, tell me more about the users.") == [
        ("speech", "Okay. Sounds good, tell me more about the users."),
    ]


@pytest.mark.parametrize("step", [1, 4, 100])
def test_tags_become_actions_and_are_never_spoken(step):
    text = ("Nice, that makes sense to me overall. <note>- a\n- b</note> Let's move on to the wireframe now please. "
            "<phase>wireframe</phase>Draw the main screen. <silent/>")
    out = run(text, step)
    assert ("note", "- a\n- b") in out
    assert ("phase", "wireframe") in out
    assert ("silent", None) in out
    spoken = " ".join(v for k, v in out if k == "speech")
    assert "<" not in spoken and "wireframe</phase>" not in spoken
    assert "Draw the main screen." in spoken


def test_speech_before_a_tag_is_emitted_first():
    out = run("Thanks, that's all the time we have today. <end/>")
    assert out == [("speech", "Thanks, that's all the time we have today."), ("end", None)]


def test_literal_less_than_is_spoken():
    out = run("Latency should stay under 300 ms, so p99 < 300 is the goal here.")
    assert out == [("speech", "Latency should stay under 300 ms,"), ("speech", "so p99 < 300 is the goal here.")]


@pytest.mark.parametrize("step", [1, 4, 100])
def test_first_chunk_ends_at_a_clause_then_whole_sentences(step):
    out = run("That makes sense for the read path, but what happens when a cache node goes down, "
              "and the traffic shifts to the database? Walk me through it, step by step.", step)
    assert out == [
        ("speech", "That makes sense for the read path,"),
        ("speech", "but what happens when a cache node goes down, and the traffic shifts to the database?"),
        ("speech", "Walk me through it, step by step."),
    ]


def test_short_lead_in_is_not_cut_at_its_comma():
    assert run("Okay, sure. Let's talk about the storage layer then.") == [
        ("speech", "Okay, sure. Let's talk about the storage layer then."),
    ]


def test_clause_cut_is_only_for_the_first_chunk_after_a_tool_call_too():
    out = []
    p = TagStream(lambda kind, value: out.append((kind, value)))
    p.feed("Thanks, I've written that down for later.")
    p.close()  # a tool call happens here
    p.feed(" So for the write path, how would you keep the counters consistent across regions?")
    p.close()
    assert [v for _, v in out] == ["Thanks, I've written that down for later.",
                                   "So for the write path, how would you keep the counters consistent across regions?"]


@pytest.mark.parametrize("text,chunks", [
    ("You could use a log, e.g. Kafka or a similar queue, for the events here. Then what?",
     ["You could use a log, e.g. Kafka or a similar queue,", "for the events here. Then what?"]),
    ("I spoke with Dr. Smith about the U.S. traffic numbers yesterday. Fine.",
     ["I spoke with Dr. Smith about the U.S. traffic numbers yesterday.", "Fine."]),
    ("We expect about 3.5 million writes per day at peak load. What else?",
     ["We expect about 3.5 million writes per day at peak load.", "What else?"]),
    ("Peak traffic is around 1,000 requests per second at most. Go on.",
     ["Peak traffic is around 1,000 requests per second at most.", "Go on."]),
])
def test_abbreviations_and_numbers_are_not_cut(text, chunks):
    assert [v for _, v in run(text, 3)] == chunks


def test_spanish_questions_are_not_cut_inside_the_question_marks():
    out = run("¿Y qué pasaría con la consistencia de los datos, si un nodo de caché se cae? "
              "Piénsalo con calma, por ejemplo, con dos réplicas.", 2)
    assert [v for _, v in out] == [
        "¿Y qué pasaría con la consistencia de los datos, si un nodo de caché se cae?",
        "Piénsalo con calma, por ejemplo, con dos réplicas.",
    ]


def test_spanish_abbreviation_and_first_clause():
    out = run("Bien, vamos a pensar en el almacenamiento primero, p. ej. una base de datos clave valor. ¿Vale?", 3)
    assert [v for _, v in out] == [
        "Bien, vamos a pensar en el almacenamiento primero,",
        "p. ej. una base de datos clave valor.",
        "¿Vale?",
    ]


def test_unfinished_tag_at_the_end_is_dropped():
    out = run("Let me think about that for a second. <pha")
    assert out == [("speech", "Let me think about that for a second.")]


def test_silent_only_turn_emits_no_speech():
    assert run("<silent/>") == [("silent", None)]


def test_cli_args_isolate_the_interviewer():
    args = claude_args("sonnet", "PROMPT", resume="abc")
    assert args[:2] == ["claude", "-p"]
    assert "--strict-mcp-config" in args
    i = args.index("--setting-sources")
    assert args[i + 1] == ""                     # no CLAUDE.md, settings or memory
    assert "Bash" not in args                    # no shell for the interviewer
    assert "--bare" not in args                  # --bare would forbid subscription auth
    assert args[args.index("--resume") + 1] == "abc"
    assert args[args.index("--model") + 1] == "sonnet"


def test_failed_turn_is_reported_as_an_error():
    import io
    import json
    import threading
    import types

    from app.interviewer import Interviewer

    events = []
    it = Interviewer.__new__(Interviewer)  # no real process: feed a canned stdout
    it.on_event, it.lock, it.busy, it.queue = lambda k, v: events.append((k, v["value"])), threading.Lock(), True, []
    it.parser = TagStream(lambda kind, val: None)
    result = {"type": "result", "is_error": True, "result": "Not logged in · Please run /login", "total_cost_usd": 0}
    it.proc = types.SimpleNamespace(stdout=io.StringIO(json.dumps(result) + "\n"), returncode=None, wait=lambda: 0)
    it._read()
    assert ("error", "Claude Code: Not logged in · Please run /login") in events
    assert events.index(("turn_end", 0)) < events.index(("error", "Claude Code: Not logged in · Please run /login"))


def test_effort_is_passed_only_when_set():
    assert "--effort" not in claude_args("sonnet", "P")
    assert "--effort" not in claude_args("sonnet", "P", effort="default")
    args = claude_args("haiku", "P", effort="low")
    assert args[args.index("--effort") + 1] == "low"
    assert "--effort" not in claude_args("haiku", "P", effort="--dangerous")  # only known levels


class FakeProc:
    """A Claude Code process that prints canned stdout/stderr and exits."""

    def __init__(self, args, stdout="", stderr="", code=0):
        import io
        self.args = args
        self.stdout, self.stderr = io.StringIO(stdout), io.StringIO(stderr)
        self.stdin = io.StringIO()
        self.code = code

    def wait(self):
        return self.code

    def poll(self):
        return self.code


def spawn_with(monkeypatch, *procs):
    from app import interviewer
    made = []

    def popen(args, **kw):
        made.append(procs[len(made)](args))
        return made[-1]
    monkeypatch.setattr(interviewer.subprocess, "Popen", popen)
    return made


def collect(it_events, kind, timeout=3.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if any(k == kind for k, _ in it_events):
            return True
        time.sleep(0.01)
    return False


def test_cli_error_on_stderr_reaches_the_session(monkeypatch):
    from app.interviewer import Interviewer
    spawn_with(monkeypatch, lambda a: FakeProc(a, stderr="error: Not logged in\n", code=1))
    events = []
    Interviewer("/tmp", "sonnet", "P", None, lambda k, d: events.append((k, d)))
    assert collect(events, "exit")
    exit_data = next(d for k, d in events if k == "exit")
    assert exit_data["error"] == "Claude Code: error: Not logged in"


def test_older_cli_without_effort_is_retried_without_it(monkeypatch, capsys):
    import json

    from app.interviewer import Interviewer
    ok = json.dumps({"type": "result", "total_cost_usd": 0.001}) + "\n"
    made = spawn_with(monkeypatch,
                      lambda a: FakeProc(a, stderr="error: unknown option '--effort'\n", code=1),
                      lambda a: FakeProc(a, stdout=ok))
    events = []
    it = Interviewer("/tmp", "haiku", "P", None, lambda k, d: events.append((k, d)), effort="low")
    it.send("[system] start")    # before or after the first process died: either way it's (re)sent
    import time
    end = time.time() + 3
    while time.time() < end and not (len(made) == 2 and "[system] start" in made[1].stdin.getvalue()):
        time.sleep(0.01)
    assert "--effort" in made[0].args and "--effort" not in made[1].args
    assert "[system] start" in made[1].stdin.getvalue()
    assert "doesn't support --effort" in capsys.readouterr().out
    assert not any(k == "exit" and d.get("error") for k, d in events)


class Gated(FakeProc):
    """Exits (or not) as told: `poll` reports `dead_code` until the gate opens; `wait` blocks on it."""

    def __init__(self, args, gate, dead_code=None, **kw):
        super().__init__(args, **kw)
        self.gate, self.dead_code = gate, dead_code

    def poll(self):
        return self.code if self.gate.is_set() else self.dead_code

    def wait(self):
        self.gate.wait(3)
        return self.code


def wait_until(cond, timeout=3.0):
    import time
    end = time.time() + timeout
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_interviewer_counts_as_alive_while_a_retry_without_effort_is_pending(monkeypatch):
    import threading

    from app.interviewer import Interviewer
    first, second = threading.Event(), threading.Event()
    made = spawn_with(monkeypatch,
                      # died over --effort, while its reader is still busy (wait blocks)
                      lambda a: Gated(a, first, dead_code=1, stderr="error: unknown option '--effort'\n", code=1),
                      lambda a: Gated(a, second, dead_code=None))  # the respawned one keeps running
    events = []
    it = Interviewer("/tmp", "haiku", "P", None, lambda k, d: events.append((k, d)), effort="low")
    assert wait_until(lambda: it.alive)       # dead, but about to be respawned: don't replace it
    first.set()
    assert wait_until(lambda: len(made) == 2)
    assert it.alive                            # respawned in place: still one interviewer
    second.set()
    assert wait_until(lambda: any(k == "exit" for k, _ in events))
    assert not it.alive


@pytest.mark.parametrize("effort,stderr", [(None, "Segmentation fault\n"), ("low", "error: Not logged in\n")])
def test_crashed_interviewer_is_not_alive_while_its_reader_still_waits(monkeypatch, effort, stderr):
    import threading

    from app.interviewer import Interviewer
    gate = threading.Event()
    spawn_with(monkeypatch, lambda a: Gated(a, gate, dead_code=1, stderr=stderr, code=1))
    it = Interviewer("/tmp", "sonnet", "P", None, lambda k, d: None, effort=effort)
    # the session must be able to respawn right away, or the next message hits a broken pipe
    assert wait_until(lambda: not it.alive)
    gate.set()


def test_reader_crash_does_not_leave_the_interviewer_alive(monkeypatch):
    import threading
    import time

    from app.interviewer import Interviewer
    crashed = threading.Event()
    monkeypatch.setattr(threading, "excepthook", lambda args: crashed.set())  # expected: keep the log clean
    gate = threading.Event()
    gate.set()
    spawn_with(monkeypatch, lambda a: Gated(a, gate, stdout='{"type": "stream_event"}\n',  # no "event": KeyError
                                            stderr="error: unknown option '--effort'\n", code=1))
    it = Interviewer("/tmp", "haiku", "P", None, lambda k, d: None, effort="low")
    assert wait_until(lambda: it.exited)
    assert not it.alive
    assert crashed.wait(2)
    time.sleep(0.01)


def test_stopped_interviewer_is_not_alive(monkeypatch):
    from app.interviewer import Interviewer

    class Running(FakeProc):
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    made = spawn_with(monkeypatch, lambda a: Running(a))
    it = Interviewer("/tmp", "sonnet", "P", None, lambda k, d: None)
    it.stop()
    assert not it.alive and made[0].terminated
