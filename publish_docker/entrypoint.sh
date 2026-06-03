#!/usr/bin/env sh
set -eu

mkdir -p /app/logs

Xvfb :99 -screen 0 1440x1000x24 >/tmp/xvfb.log 2>&1 &
fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc -display :99 -forever -shared -nopw -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
/usr/share/novnc/utils/novnc_proxy --vnc localhost:5900 --listen 6080 >/tmp/novnc.log 2>&1 &

exec python -m uvicorn publish_docker.app.server:app \
  --host "${SERVICE_HOST:-0.0.0.0}" \
  --port "${SERVICE_PORT:-8000}"
