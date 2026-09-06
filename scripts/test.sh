#!/usr/bin/env bash
# Run the test suite (idempotent).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# shellcheck disable=SC1091
source "$PROJECT_DIR/.venv/bin/activate"

echo "==> Running tests"
python -m pytest -v "$@"
