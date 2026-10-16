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
    assert out == [("speech", "Latency should stay under 300 ms, so p99 < 300 is the goal here.")]


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
