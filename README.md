# Mocksy

Rehearse technical interviews out loud. Mocksy gives you a voice interviewer that asks
questions, follows your whiteboard as you draw, pushes back like a real interviewer, and
writes a structured evaluation when you're done.

- **Talk naturally**: hands-free voice with automatic turn-taking; interrupt the interviewer any time.
- **Draw**: an embedded Excalidraw whiteboard. The interviewer gets a text description of
  every change and looks at the full image when it needs to.
- **Interview types**: product decomposition (incl. an product decomposition round), classic
  system design, and easy to add your own (`prompts/packs/*.md`). Optionally tailored to a
  target company with quick web research.
- **Evaluation**: verdict, rubric scores, top 3 things to practice, and links to the exact
  moments to review. Every session is saved and can be replayed.
- **Local voice**: speech-to-text with Whisper (MLX) and text-to-speech with Kokoro run on
  your machine. The interviewer runs on [Claude Code](https://claude.com/claude-code).

## Requirements
- macOS on Apple Silicon (Whisper runs on MLX)
- Python 3.11, Node 20+ and pnpm
- [Claude Code](https://claude.com/claude-code) installed and logged in (`claude` on your PATH)

## Setup
```sh
scripts/setup.sh   # local venv, Python deps, voice models (~1.9 GB)
./run.sh           # builds the web UI and serves http://127.0.0.1:8765
```
Open the URL in Chrome or Safari and allow the microphone.

## Configuration
`config.toml` holds defaults: which model runs the interview (`sonnet` by default) and which
writes the evaluation (`opus`), the evaluation language, voices and port. Models can also be
picked per session in the app.

## Development
```sh
./run.sh                 # backend on :8765
cd web && pnpm dev       # UI with hot reload on :5173
```
```sh
scripts/test.sh         # backend + frontend tests
```
See `CLAUDE.md` for the architecture.
