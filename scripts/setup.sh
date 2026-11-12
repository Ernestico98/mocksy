#!/bin/sh
# One-time setup: local venv + model downloads (models are not tracked in git).
set -e
cd "$(dirname "$0")/.."
. scripts/node.sh
PY=${PYTHON:-python3.11}
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt -r requirements-dev.txt
mkdir -p models
BASE=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
[ -f models/kokoro-v1.0.onnx ] || curl -fL -o models/kokoro-v1.0.onnx "$BASE/kokoro-v1.0.onnx"
[ -f models/voices-v1.0.bin ] || curl -fL -o models/voices-v1.0.bin "$BASE/voices-v1.0.bin"
# Whisper weights download from Hugging Face (cached in ~/.cache/huggingface).
.venv/bin/python -c "from app import speech; speech.preload_stt()" >/dev/null
(cd web && pnpm install --frozen-lockfile && pnpm build)
# "Model answer" video renderer: playwright-core drives Chrome; without a system Chrome/Chromium,
# download Playwright's headless Chromium (~100 MB). It also needs ffmpeg on the PATH.
(cd app/explainer && pnpm install --frozen-lockfile)
HAS_CHROME=
for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" /Applications/Chromium.app/Contents/MacOS/Chromium \
    /usr/bin/google-chrome /usr/bin/chromium /usr/bin/chromium-browser; do [ -x "$c" ] && HAS_CHROME=1; done
if [ -z "$HAS_CHROME" ]; then  # same places app/explainer/render.mjs looks
  (cd app/explainer && pnpm exec playwright-core install --only-shell chromium)
fi
command -v ffmpeg >/dev/null || echo "Note: install ffmpeg to render model-answer videos (brew install ffmpeg / apt install ffmpeg)."
echo "Setup done. Run ./run.sh"
