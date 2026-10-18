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
echo "Setup done. Run ./run.sh"
