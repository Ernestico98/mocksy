#!/bin/sh
# All checks: backend tests, frontend typecheck + tests.
#   scripts/test.sh          fast, offline (fake interviewer)
#   MOCKSY_LIVE=1 scripts/test.sh   also runs one real Claude Code round trip (haiku)
set -e
cd "$(dirname "$0")/.."
. scripts/node.sh
.venv/bin/pytest
(cd web && pnpm typecheck && pnpm test)
