#!/usr/bin/env bash
# Send a REAL Telegram test message through GX10 → Telegram API → inferont.
# Usage: scripts/send_test.sh [message]
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# shellcheck disable=SC1091
source "$PROJECT_DIR/.venv/bin/activate"

if [ ! -f .env ]; then
    echo "ERROR: $PROJECT_DIR/.env not found. Run scripts/install.sh and edit .env first." >&2
    exit 1
fi

if [ $# -gt 0 ]; then
    # Raw message through the running gateway (or direct API fallback)
    exec telegram-gateway send "$*"
else
    exec telegram-gateway test
fi
