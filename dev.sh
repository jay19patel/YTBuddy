#!/usr/bin/env bash
# One command for local use: back up the DB, migrate, then run web + worker + beat together.
# Ctrl+C stops everything. Needs Redis (brew services start redis) and ffmpeg for Shorts.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8000}"

redis-cli ping >/dev/null 2>&1 || { echo "Redis is not running. Start it with: brew services start redis"; exit 1; }
command -v ffmpeg >/dev/null || echo "warning: ffmpeg not found, Shorts rendering will fail (brew install ffmpeg)"

# Daily safety copy of the SQLite database before migrating (sqlite3 .backup is safe while it is in use).
if [ -z "${DATABASE_URL:-}" ] && [ -f ytstudio.db ]; then
  mkdir -p backups
  backup="backups/ytstudio-$(date +%Y%m%d).db"
  [ -f "$backup" ] || sqlite3 ytstudio.db ".backup '$backup'"
fi

uv sync --frozen --inexact -q   # --inexact keeps the optional Whisper extra if you installed it
uv run python -m ytstudio.cli migrate

trap 'kill 0' EXIT   # stop all three when this script exits
uv run celery -A ytstudio.interfaces.worker worker -Q io,llm,media -c 4 -n dev@%h --loglevel=INFO &
uv run celery -A ytstudio.interfaces.worker beat --loglevel=INFO -s .celerybeat-schedule &
uv run uvicorn main:app --host 127.0.0.1 --port "$PORT" --reload &
echo "YTStudio: http://localhost:$PORT  (Ctrl+C to stop)"
wait
