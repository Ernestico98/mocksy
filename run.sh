#!/bin/sh
# Start Mocksy. Builds the web UI if needed; host/port come from config.toml.
#   ./run.sh           build (if missing) + serve
#   ./run.sh --build   force a fresh frontend build first
cd "$(dirname "$0")"
. scripts/node.sh
if [ "$1" = "--build" ] || [ ! -d web/dist ]; then
  (cd web && pnpm install --frozen-lockfile && pnpm build) || exit 1
fi
PORT=$(.venv/bin/python -c "import tomllib;print(tomllib.load(open('config.toml','rb'))['app']['port'])")
HOST=$(.venv/bin/python -c "import tomllib;print(tomllib.load(open('config.toml','rb'))['app']['host'])")
echo "→ http://$HOST:$PORT"
exec .venv/bin/uvicorn app.server:app --host "$HOST" --port "$PORT"
