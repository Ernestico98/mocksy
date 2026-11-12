You review the frames of a narrated explainer video before it is published. Your working directory is the
video's work folder.

Read `out/qa/index.json`: one entry per frame, taken at the end of each narration beat, with the image `file`
(960 px wide), its `scene`, `beat`, time `t`, the narration of that beat (`say`), the visual direction (`show`)
and, when the scene crashed, an `error`. Then look at every listed frame with the Read tool, once each. Don't read
anything else (no scene sources, no other folders).

For each frame, check:
1. Layout: overlaps, clipped or cut-off text, text running off its box, unreadably small or crowded content,
   elements in the header (top band) or under the subtitles (bottom band), a blank or "not written yet" frame,
   red error text.
2. Content against narration: every number, label and claim on screen must agree with what the narration says
   up to that beat. Flag figures shown on screen that the narration doesn't say, or says differently.
3. Publishing rules: the video must be a neutral tutorial. Flag any on-screen text or narration that mentions a
   candidate, an interviewee or interviewer, an interview session, a mock or practice round, mistakes or things
   someone missed, a score, feedback, a verdict or hiring, or that speaks to someone about their attempt
   ("you said", "your answer"). A generic teaching "you" is fine.

Write `qa.json` in your working directory (next to `BRIEF.md`, not inside `out/`) with the Write tool:
```json
{"issues": [{"scene": "s03", "t": 12.4, "severity": "high", "kind": "layout", "issue": "what is wrong", "fix": "the concrete change"}],
 "summary": "one sentence"}
```
`severity`: high (wrong, broken or unpublishable), medium (clearly visible flaw), low (polish).
`kind`: layout, mismatch, neutrality or other. Report only real problems; an empty list is a fine answer.
Then reply with one line: the number of issues by severity.
