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
    it.proc = types.SimpleNamespace(stdout=io.StringIO(json.dumps(result) + "\n"), returncode=None)
    it._read()
    assert ("error", "Claude Code: Not logged in · Please run /login") in events
    assert events.index(("turn_end", 0)) < events.index(("error", "Claude Code: Not logged in · Please run /login"))


def test_effort_is_passed_only_when_set():
    assert "--effort" not in claude_args("sonnet", "P")
    assert "--effort" not in claude_args("sonnet", "P", effort="default")
    args = claude_args("haiku", "P", effort="low")
    assert args[args.index("--effort") + 1] == "low"
    assert "--effort" not in claude_args("haiku", "P", effort="--dangerous")  # only known levels
