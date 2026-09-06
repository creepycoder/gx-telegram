#!/usr/bin/env bash
# GX Telegram Gateway installer (idempotent).
# Creates a venv, installs the package, prepares .env and data dir.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

VENV="$PROJECT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "==> Project: $PROJECT_DIR"

# 1. Python version check
"$PYTHON_BIN" - <<'EOF'
import sys
if sys.version_info < (3, 11):
    sys.exit(f"Python >= 3.11 required, found {sys.version}")
print(f"==> Python {sys.version.split()[0]} OK")
EOF

# 2. Virtualenv (recreated only if missing/broken)
if [ ! -x "$VENV/bin/python" ]; then
    echo "==> Creating virtualenv"
    "$PYTHON_BIN" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --quiet --upgrade pip

# 3. Install package
echo "==> Installing telegram-gateway"
pip install --quiet -e ".[dev]"

# 4. .env
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo "==> Created .env from template (chmod 600) — EDIT IT and fill secrets"
else
    echo "==> .env already exists, leaving untouched"
fi

# 5. Data dir
mkdir -p data
chmod 700 data

echo
echo "Installation complete."
echo "  1. Edit $PROJECT_DIR/.env  (bot token, allowlists, tokens)"
echo "  2. Run: .venv/bin/telegram-gateway test    # real Telegram test message"
echo "  3. Run: .venv/bin/telegram-gateway run     # foreground"
echo "  4. systemd: see systemd/telegram-gateway.service + README"
