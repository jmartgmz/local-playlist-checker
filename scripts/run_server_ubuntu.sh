#!/usr/bin/env bash
# Production-style launcher for Ubuntu server use.
# - Forces DEBUG off
# - Uses HOST/PORT from env if set, otherwise defaults to localhost:5301

set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "Error: Virtual environment not found at .venv/bin/activate"
  echo "Create it with: python3 -m venv .venv"
  exit 1
fi

export DEBUG=false
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-5301}"

echo "Starting Local Playlist Checker on ${HOST}:${PORT} (DEBUG=${DEBUG})"
python run.py
