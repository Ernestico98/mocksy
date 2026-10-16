# Mocksy — developer notes

Voice mock-interview app. The candidate talks (local Whisper STT) and draws (Excalidraw);
the interviewer is a Claude Code CLI process the backend spawns per session; its replies
are spoken with local Kokoro TTS. At the end a second Claude call writes the evaluation.

## Layout
- `app/` FastAPI backend (Python 3.11, `.venv` local — never install packages globally)
  - `server.py` HTTP API + SSE (`/api/events`), serves `web/dist`
  - `sessions.py` session state on disk, phases/clock, canvas diffs, interrupts, TTS queue, evaluation
  - `interviewer.py` long-lived `claude -p --input-format stream-json` process + streaming tag parser
  - `speech.py` Kokoro TTS / mlx-whisper STT with hallucination filters
  - `config.py` `config.toml` defaults + `data/settings.json` overrides, interview packs
- `prompts/interviewer.md`, `prompts/feedback.md` system prompts (`{placeholders}` filled from session meta)
- `prompts/packs/*.md` interview types: TOML header between `+++` (title, phases, rubric) + interviewer brief
- `web/` React 19 + Vite + TypeScript, **pnpm** (not npm)
- `data/` sessions and settings (gitignored); `models/` Kokoro weights (gitignored)

## Run / develop
- First time: `scripts/setup.sh` (venv, Python deps, model downloads)
- `./run.sh` builds `web/` if needed and serves http://127.0.0.1:8765 (`./run.sh --build` to force a rebuild)
- Frontend dev with hot reload: `./run.sh` in one terminal, `cd web && pnpm dev` in another (proxies `/api` and `/files`)
- Checks: `cd web && pnpm typecheck`; backend has no test suite yet
- The built-in browser pane has no microphone: test voice in Chrome/Safari

## Interviewer protocol (see prompts/interviewer.md)
- Plain text is spoken; tags are actions: `<note>`, `<phase>key</phase>`, `<silent/>`, `<end/>`
- Messages to the model carry headers: `[candidate · clock]`, `[canvas]` diffs, `[drawing silently]`,
  `[interrupted]`, `[phase]`, `[system] start`
- The process runs in the session folder with `--setting-sources "" --strict-mcp-config` and only
  Read/Write/Edit/WebSearch/WebFetch, so it never sees this repo's CLAUDE.md or the user's MCP servers
- Don't use `--bare`: it forbids subscription (OAuth) auth

## Conventions
- UI copy is Spanish; interview language is per session (`en`/`es`); evaluation language from settings
- Model weights, `data/`, `web/dist`, `web/node_modules` never go into git
