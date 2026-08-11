#!/bin/zsh
set -e
cd "$(dirname "$0")"

PY=".venv/bin/python"
PORT="${APANEL_PORT:-5050}"

if [ ! -x "$PY" ]; then
  echo "Creating Python virtual environment..."
  /opt/homebrew/opt/python@3.11/bin/python3.11 -m venv .venv
fi

if ! "$PY" -c "import flask, requests, easy_tdx, pandas" >/dev/null 2>&1; then
  echo "Installing dependencies (first run only)..."
  "$PY" -m pip install -r requirements.txt
fi

export APANEL_PORT="$PORT"
export APANEL_HOST="127.0.0.1"
echo "Starting A股高开雷达 at http://127.0.0.1:$PORT/"
exec "$PY" server.py
