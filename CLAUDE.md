# Mocksy — developer notes

Voice mock-interview app. The candidate talks (local Whisper STT) and draws (Excalidraw);
the interviewer is a Claude Code CLI process the backend spawns per session; its replies
are spoken with local Kokoro TTS. At the end a second Claude call writes the evaluation.

## Layout
- `app/` FastAPI backend (Python 3.11, `.venv` local — never install packages globally)
  - `server.py` HTTP API + SSE (`/api/events`), serves `web/dist`
  - `sessions.py` session state on disk, phases/clock, canvas diffs, interrupts, TTS queue, evaluation
  - `interviewer.py` long-lived `claude -p --input-format stream-json` process + streaming tag parser
  - `speech.py` Kokoro TTS / Whisper STT with hallucination filters; STT engine is mlx-whisper on Apple Silicon,
    faster-whisper elsewhere (`stt_engine` in `config.toml`; per-platform markers in `requirements.txt`)
  - `config.py` `config.toml` defaults + `data/settings.json` overrides
  - `latency.py` per-turn timing (VAD → upload → STT → sent → first text → first chunk → TTS → playback),
    stored as `latency` on the candidate message and logged as `[latency] {json}`; the browser sends
    `vad_age_ms`/`silence_ms` with `/transcribe` and reports playback via `/played` (`wait_ms`): durations on
    the browser's own clock, never timestamps, so remote browsers work; values outside 0–60 s are dropped
  - `packs.py` interview packs: load, parse/serialize, validate, personal-pack CRUD
- `prompts/interviewer.md`, `prompts/feedback.md` system prompts (`{placeholders}` filled from session meta)
- `prompts/explainer/{script,format,scene,qa}.md` model-answer worker prompts (generic: no company names)
- `prompts/packs/*.md` interview types: TOML header between `+++` (title, phases, rubric) + interviewer brief;
  personal packs (e.g. company-specific) go in `data/packs/*.md` and stay out of git. `app/packs.py` parses,
  validates and writes them back (round-trip safe); the in-app editor (`#/packs`, `/api/packs`) only writes
  `data/packs/`. `whiteboard = false` (default true) hides the board and drops the `<!-- if whiteboard -->`
  blocks from the prompts; sessions snapshot the brief (`pack_body`) so edits don't affect running interviews
- `web/` React 19 + Vite + TypeScript, **pnpm** (not npm)
- `data/` sessions and settings (gitignored); `models/` Kokoro weights (gitignored)

## Run / develop
- First time: `scripts/setup.sh` (venv, Python deps, model downloads)
- `./run.sh` builds `web/` if needed and serves http://127.0.0.1:8765 (`./run.sh --build` to force a rebuild)
- Docker: `docker compose up -d --build` (Linux image: faster-whisper on CPU, Claude Code from the `stable`
  channel; pin with the `CLAUDE_CODE_VERSION` build arg, and rebuild with `--no-cache` to update it). The macOS Keychain login isn't reachable from the container: log in with
  `docker compose exec mocksy claude auth login` (kept in the `claude` volume via `CLAUDE_CONFIG_DIR`) or export
  `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY`. Kokoro is fetched on first start by `scripts/docker-entrypoint.sh`
  into the `models` volume, Whisper into `hf-cache`; `./data` is bind-mounted, shared with native runs
- Frontend dev with hot reload: `./run.sh` in one terminal, `cd web && pnpm dev` in another (proxies `/api` and `/files`)
- Tests: `scripts/test.sh` (pytest + `pnpm typecheck` + vitest). Backend tests use a fake interviewer
  (`tests/conftest.py`) and an isolated `MOCKSY_DATA_DIR`; `MOCKSY_LIVE=1` adds one real Claude Code
  round trip with haiku (`tests/test_live.py`) — run it after touching `app/interviewer.py`
- Frontend tests live next to the code (`web/src/lib/*.test.ts`): whiteboard description, VAD (incl. the
  adaptive end-of-turn pause) with synthetic audio
- Latency: `.venv/bin/python scripts/bench_latency.py` (real Whisper + Claude haiku + Kokoro, no mic, a throwaway
  data dir); in the UI, ⚙ → "Show response times" or `?debug=1`. The server warms up Kokoro + Whisper at
  startup (`MOCKSY_WARMUP=0` disables it; tests do)
- The built-in browser pane has no microphone: test voice in Chrome/Safari

## Interviewer protocol (see prompts/interviewer.md)
- Plain text is spoken; tags are actions: `<note>`, `<phase>key</phase>`, `<silent/>`, `<end/>`
- Replies are spoken chunk by chunk (`TagStream`): the first chunk may end at a clause (≥ 6 words before a
  comma/colon/dash), later ones at sentence ends; no cuts after abbreviations or inside `¿…?`/`¡…!`
- Messages to the model carry headers: `[candidate · clock]`, `[canvas]` diffs, `[drawing silently]`,
  `[interrupted]`, `[phase]`, `[system] start`
- The process runs in the session folder with `--setting-sources "" --strict-mcp-config` and only
  Read/Write/Edit/WebSearch/WebFetch, so it never sees this repo's CLAUDE.md or the user's MCP servers
- Don't use `--bare`: it forbids subscription (OAuth) auth

## Model answer (see README for the user view)
- Modes: `video` (script → diagram → voice → scenes → render → qa → fixes → package) and `diagram` (script →
  diagram → package). Retry continues from the first stage not done; a video run reuses a diagram run's script.
- Cost bounds (required): one script worker (opus) reading only `brief.md`, `emphasis.md` (rubric dimensions
  ordered by score: never free text from the evaluation), `scenario.md` (opening prompt + the `## Scenario` part of
  notes.md, remarks about the person dropped), `format.md` (never the transcript); two scene workers (sonnet) reading only BRIEF.md, their job file,
  lib.js, reference.js, ≤2 still-render rounds per scene, Bash limited to `node render.mjs beats|preview`; one QA
  pass (sonnet) on 960 px end-of-beat frames; one fix round (`--resume` of the scene worker); no second QA.
  The backend never reads images. Every worker gets `--max-budget-usd` = what's left of the cap (plus, on
  `--resume`, what the session already cost: Claude Code reports and caps resumed sessions cumulatively; only the
  delta is recorded). One fix round in total (`fix_rounds_used` survives retries); the work folder's `render.mjs`
  shim counts render rounds per scene (`.render_limits.json`: 2, +1 per fix).
- Isolation: `--permission-mode default` + path rules (`Read(//abs/**)`, writes via `Edit(//abs/file)`: `Write(...)`
  path rules grant nothing) + deny rules for the toolkit files, which are also read-only on disk.
- Packs: `model_answer = false` hides the tab and the API refuses (default: follows `whiteboard`).
- Publishing rules: the script is a neutral explainer (no candidate, interview session, mock, mistakes, score,
  feedback, verdict, company name unless `ma_name_company`). `explainer/script.py:neutrality` runs on script.json
  (one bounce, then fail) and on scene string literals (fed to QA/fixes, fail if they remain); QA checks frames.
- One generation at a time; blocked while an interview is live, and new/resumed interviews and re-evaluations are
  blocked while it runs.
- Tests: `tests/test_model_answer.py` (fake Claude + fake renderer), `tests/test_explainer_render.py` (real
  Chrome/ffmpeg, skipped without them), `MOCKSY_LIVE=1 tests/test_live_model_answer.py` (2 scenes, haiku, ~$0.1).

## Conventions
- UI copy is English; interview language is per session (`en`/`es`); evaluation language from settings (default `en`)
- Model weights, `data/`, `web/dist`, `web/node_modules` never go into git
