#!/usr/bin/env bash
set -euo pipefail

mkdir -p "${AUTO_DATA_DIR:-/app/auto/data}"
mkdir -p "${AUTO_LOG_DIR:-/app/auto/logs}"
mkdir -p /app/api/logs

export DISPLAY="${DISPLAY:-:99}"
Xvfb ${DISPLAY} -screen 0 1280x1024x24 -nolisten tcp &

exec python -m uvicorn auto.server:app --host 0.0.0.0 --port 19000 --workers 1
