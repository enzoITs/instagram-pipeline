#!/usr/bin/env bash
# Development helper: create the virtualenv if needed, then start the server.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "==> Creating the virtual environment"
  python3 -m venv .venv
fi

echo "==> Installing dependencies"
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  echo "==> No .env found; copying .env.example. Fill in your Meta credentials."
  cp .env.example .env
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

echo "==> Dashboard: http://${HOST}:${PORT}"
exec ./.venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
