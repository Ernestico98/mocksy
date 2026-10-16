#!/bin/sh
# Start the mock interview server at http://127.0.0.1:8765
cd "$(dirname "$0")" && exec .venv/bin/uvicorn server:app --host 127.0.0.1 --port 8765
