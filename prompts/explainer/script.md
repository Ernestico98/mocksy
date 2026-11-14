You write the script of a short narrated explainer video: a model answer to a technical design problem,
shown the way a strong engineer would solve it. The video is published on its own (for example on YouTube),
for viewers who know nothing about how it was made.

Your working directory contains exactly these files:
{inputs}
Read those files, nothing else. Then write the script with the Write tool to exactly this path:
`{output}`

# What to write
- {scenes_min}-{scenes} scenes, about {words} words of narration in total (about {minutes} minutes spoken).
  Each scene has 2-6 beats; a beat is 1-3 spoken sentences plus a visual direction.
- Solve the problem end to end, in the order of the round's phases: clarify scope and numbers, then the design,
  then the hardest parts in depth, then trade-offs. Spend most time on the concepts `emphasis.md` points to.
- Be concrete: real numbers, real components, real data models and APIs. When a figure isn't given in `scenario.md`,
  pick a reasonable one, say it as an assumption in the narration ("say, ten million links a day") and list it
  in `assumptions`.
- Show before you tell: never quote an example's results (sample answers, outputs, measurements) without first
  showing the input that produces them, and walk through at least one example step by step on screen.
- Define every technical term the first time it's used (in a sentence, before relying on it). For example, say
  what a tree's centroid is before arguing about splitting at it; don't just name it.
- If the video ends with an extension or harder variant, pose it to the viewer as an open challenge (for example
  "Can you solve this version?") with at most a hint, not as a solved aside.
- Narration in {language_name}, spoken style, short sentences, no lists read aloud, no markdown, no symbols the
  voice would read literally (write "per second", not "/s"). Scene titles: short (max ~40 characters).
- `show` directions are for an animator: what appears, where, and when within the beat, using only things the
  narration says. Every number or label on screen must appear in that beat's narration.
- The `diagram` is the final solution as one picture (architecture, or the wireframe's main regions):
  4-14 nodes on a grid, labelled arrows for the main flows.

# Publishing rules (hard)
The script must be a neutral, self-contained explainer of how to solve the problem well.
- Never mention a candidate, an interviewee, an interviewer, an interview session, a mock or practice round,
  mistakes, things someone missed or forgot, a score, feedback, a verdict or hiring.
- No second person aimed at someone who attempted it ("you said", "you missed", "your answer").
  A generic teaching "you" is fine ("you'd want a queue here").
- {company_rule}
- No personal information of any kind.
A script that breaks these rules is rejected automatically.

When done, reply with one line: the number of scenes and words.
