# Brief for scene authors

We're making a short narrated explainer video (1920x1080, 30 fps) that teaches how to solve a
technical design problem well. The narration is already recorded; you write the animation for the
scenes assigned to you. The video is published as a stand-alone tutorial.

## Read these, and nothing else
- `BRIEF.md` (this file).
- Your job file (named in your instructions): for each of your scenes, its title and beats.
  Each beat has `say` (the narration, fixed and already recorded), `show` (the visual direction you
  implement) and `start`/`end` (seconds within the scene when it's spoken), plus the scene `duration`.
- `lib.js`: the drawing toolkit (`L.box`, `L.text`, `L.chip`, `L.arrow`, `L.table`, `L.code`, `L.bar`,
  `L.icon`, `L.enter`, `L.p`, `L.frac`, `L.wrap`, ...). Read it fully; use it.
- `reference.js`: the reference scene. Match its style and conventions.
Don't open other files (script.json, other scenes, audio): everything you need is above.

## Rules
- One file per scene: `scenes/<id>.js`, calling `scene("<id>", (t, B) => svgString)`. Pure function of t:
  no state, no DOM access, no randomness, no timers.
- Time everything off beats: `B[i].start`, `B[i].end`, `L.frac(B, i, f)`. Never hard-code absolute seconds
  (narration lengths may change). Things appear when the narration mentions them, and stay (cumulative
  build), unless the direction says they change or fade.
- Layout: content inside x 80..1840, y 150..920. The player draws the title/phase header (y < 130) and
  subtitles (y > 940). Nothing may overlap, get clipped, or run off a box. Text >= 20 px; main labels 26-40 px.
  Leave breathing room: when a scene gets dense, shrink or fade earlier elements rather than cramming.
  Use `L.wrap(text, maxChars)` for long labels; `L.textW` estimates widths.
- Colours from `L.C` (blue = system/neutral, green = good/settled, amber = needs attention, red = failure,
  purple = data/tables, teal = caches/queues, muted/faint = secondary). Dark theme is fixed.
- On-screen text and numbers must match the narration exactly: never show a figure, name or claim the
  narration doesn't make. If `show` contradicts `say`, follow `say`.
- The video teaches a topic. Never put on screen anything about a person being evaluated, an interview
  session, a mock, mistakes someone made, scores or verdicts.
- Don't edit lib.js, player.html, render.mjs, script.json, timings.json, reference.js or other scenes.
  If you need a helper, define it inside your scene file.
- Animations: short entrances (0.4-0.8 s), no constant wobbling. Something should move or appear at least
  every few seconds so the video doesn't feel frozen, always tied to what's being said.

## Check your work (bounded)
- `node render.mjs beats <id>` renders one still at the end of each beat into `out/preview/<id>_<t>.png`;
  `node render.mjs preview <id> <t> [t ...]` renders chosen times. Look at the PNGs with the Read tool.
- At most 2 render rounds per scene: write the scene, render its beats, fix what's wrong, render once more
  if you changed layout. Then move on. Errors in your scene show up as red text on the frame and on stderr.
- Check each still: no overlaps, nothing clipped, legible, matches what the narration says at that moment,
  and the final state looks clean and balanced.

## Report
When done, reply in a few lines: the scenes you wrote, anything from `show` you changed or simplified and
why, and any known issue.
