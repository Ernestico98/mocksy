#!/bin/sh
# Start the app. Host/port come from config.toml.
cd "$(dirname "$0")"
PORT=$(.venv/bin/python -c "import tomllib;print(tomllib.load(open('config.toml','rb'))['app']['port'])")
HOST=$(.venv/bin/python -c "import tomllib;print(tomllib.load(open('config.toml','rb'))['app']['host'])")
echo "→ http://$HOST:$PORT"
exec .venv/bin/uvicorn app.server:app --host "$HOST" --port "$PORT"
