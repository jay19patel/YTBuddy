#!/bin/sh
# Start everything: Chatterbox on the Mac (it needs the Mac's GPU, which Docker cannot use), then the Docker stack.
#   tools/up.sh            start (Chatterbox is left alone if it is already running)
#   tools/up.sh --build    rebuild the Docker images first (after code changes)
# Chatterbox's log: /tmp/chatterbox.log. Stop it: pkill -f chatterbox_server.py
set -e
cd "$(dirname "$0")/.."
PY="${CHATTERBOX_PYTHON:-$(grep -E '^CHATTERBOX_PYTHON=' .env | cut -d= -f2-)}"
REF="${CHATTERBOX_REF:-$(grep -E '^CHATTERBOX_REF=' .env | cut -d= -f2-)}"  # your voice; empty = Chatterbox's own

if curl -s -m 2 http://127.0.0.1:8765/ >/dev/null; then
  echo "Chatterbox is already running."
else
  [ -x "$PY" ] || { echo "Set CHATTERBOX_PYTHON in .env to the Chatterbox virtualenv's python"; exit 1; }
  echo "Starting Chatterbox (log: /tmp/chatterbox.log)..."
  nohup "$PY" tools/chatterbox_server.py ${REF:+--ref "$REF"} >/tmp/chatterbox.log 2>&1 &
  for _ in $(seq 1 180); do  # the model takes up to a minute or two to load
    curl -s -m 2 http://127.0.0.1:8765/ >/dev/null && break
    sleep 1
  done
  curl -s -m 2 http://127.0.0.1:8765/ >/dev/null || { echo "Chatterbox did not start; see /tmp/chatterbox.log"; exit 1; }
  echo "Chatterbox is ready."
fi

docker compose up -d "$@"
