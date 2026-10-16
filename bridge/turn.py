"""Claude's side of the conversation.

    .venv/bin/python bridge/turn.py "What the interviewer says"   # speak, then wait
    echo "text" | .venv/bin/python bridge/turn.py -              # same, text from stdin
    .venv/bin/python bridge/turn.py                              # just wait
    .venv/bin/python bridge/turn.py --note "text"                # show on screen, don't speak

Blocks until the candidate sends something, prints it, and exits.
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"
CURSOR = Path(__file__).with_name(".cursor.json")


def call(path, body=None, timeout=700):
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=timeout) as r:
        return json.loads(r.read())


def load_cursor(session):
    try:
        c = json.loads(CURSOR.read_text())
        if c.get("session") == session:
            return c["after"]
    except (OSError, ValueError, KeyError):
        pass
    return 0


def main():
    args = sys.argv[1:]
    kind = "speech"
    if args and args[0] == "--note":
        kind, args = "note", args[1:]
    text = " ".join(args)
    if text == "-":
        text = sys.stdin.read()

    state = call("/api/state")
    session = state["session"]
    after = load_cursor(session)
    if after == 0 and state["messages"]:
        after = max(m["id"] for m in state["messages"])

    if text.strip():
        msg = call("/api/interviewer", {"text": text.strip(), "kind": kind})
        after = max(after, msg["id"])
        CURSOR.write_text(json.dumps({"session": session, "after": after}))

    # phase clicks alone don't start a turn; they ride along with the next real message
    msgs = []
    while not any(m["kind"] != "phase" for m in msgs):
        cursor = msgs[-1]["id"] if msgs else after
        msgs += call(f"/api/next?after={cursor}&timeout=600")["messages"]
    # the candidate often sends a couple of fragments in a row; gather them
    time.sleep(1.5)
    more = call(f"/api/next?after={msgs[-1]['id']}&timeout=0.1")["messages"]
    msgs += more

    started = state["messages"][0]["ts"] if state["messages"] else msgs[0]["ts"]
    for m in msgs:
        mins = (m["ts"] - started) / 60
        line = f"[+{mins:4.1f}m #{m['id']} {m['kind']}] {m['text']}"
        if m.get("image"):
            line += f"\n  WIREFRAME IMAGE: {m['image']}"
        print(line)
    CURSOR.write_text(json.dumps({"session": session, "after": msgs[-1]["id"]}))


if __name__ == "__main__":
    main()
