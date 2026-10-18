#!/bin/sh
# Container start: fetch the Kokoro voice model into its volume on first run, then serve.
set -e
BASE=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  if [ ! -f "models/$f" ]; then
    echo "Downloading $f (first start only)..."
    curl -fsSL -o "models/$f.part" "$BASE/$f" && mv "models/$f.part" "models/$f"
  fi
done
if ! claude auth status >/dev/null 2>&1; then
  echo "Claude Code is not logged in. Run: docker compose exec mocksy claude auth login"
fi
exec "$@"
