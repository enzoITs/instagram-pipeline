#!/usr/bin/env bash
# Development helper: bring up PostgreSQL and Redis, migrate, then run the API,
# both workers and (optionally) the Vite dev server.
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE=${COMPOSE:-}
if [ -z "$COMPOSE" ]; then
  if command -v docker >/dev/null 2>&1; then COMPOSE="docker compose"
  elif command -v podman-compose >/dev/null 2>&1; then COMPOSE="podman-compose"
  else
    echo "Neither docker nor podman-compose found. Start PostgreSQL and Redis yourself,"
    echo "then run: .venv/bin/uvicorn app.main:app --reload"
    exit 1
  fi
fi

if [ ! -f .env ]; then
  echo "==> No .env found. Copy the template first:  cp .env.example .env"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "==> Creating the virtual environment"
  python3 -m venv .venv
fi

echo "==> Installing dependencies"
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements-dev.txt

echo "==> Starting PostgreSQL and Redis"
$COMPOSE up -d postgres redis

echo "==> Applying migrations"
./.venv/bin/alembic upgrade head

if [ ! -f frontend/dist/index.html ]; then
  echo "==> Building the dashboard (first run only)"
  npm --prefix frontend ci --no-audit --no-fund
  npm --prefix frontend run build
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "==> Starting the analysis worker"
./.venv/bin/celery -A app.celery_app worker -Q analysis --concurrency 1 --loglevel=INFO &
pids+=($!)

echo "==> Starting the collection worker and the schedule"
./.venv/bin/celery -A app.celery_app worker -Q collection -B --concurrency 1 --loglevel=INFO &
pids+=($!)

echo "==> Dashboard: http://${HOST}:${PORT}"
exec ./.venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
