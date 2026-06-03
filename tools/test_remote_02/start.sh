#!/bin/bash
set -e

VNC_PORT=5900
NOVNC_PORT=6080
CDP_PORT=9222
RESOLUTION=${RESOLUTION:-1280x800x24}
START_URL=${START_URL:-https://mp.toutiao.com/auth/page/login}

echo "============================================="
echo "  noVNC Remote Browser"
echo "============================================="
echo "  Resolution : $RESOLUTION"
echo "  VNC Port   : $VNC_PORT"
echo "  noVNC Port : $NOVNC_PORT"
echo "  CDP Port   : $CDP_PORT"
echo "  Start URL  : $START_URL"
echo "============================================="

# 1. 启动 Xvfb 虚拟显示器
echo "[1/6] Starting Xvfb..."
Xvfb :99 -screen 0 "$RESOLUTION" -ac &
export DISPLAY=:99
sleep 1

# 2. 启动 autocutsel — 同步 X11 PRIMARY 和 CLIPBOARD 剪贴板
echo "[2/6] Starting autocutsel (clipboard sync)..."
autocutsel -fork
autocutsel -s PRIMARY -fork
sleep 0.5

# 3. 启动 D-Bus + fcitx 输入法
echo "[3/6] Starting fcitx input method..."
eval "$(dbus-launch --sh-syntax)"
export DBUS_SESSION_BUS_ADDRESS
fcitx -d --replace 2>/dev/null || true
sleep 1

# 4. 启动 Chrome
echo "[4/6] Starting Chrome..."
google-chrome-stable \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-gpu \
    --disable-blink-features=AutomationControlled \
    --remote-debugging-port=$CDP_PORT \
    --remote-debugging-address=0.0.0.0 \
    --user-data-dir=/tmp/chrome-profile \
    --window-size=1280,800 \
    --window-position=0,0 \
    --no-first-run \
    --no-default-browser-check \
    "$START_URL" &
sleep 2

# 5. 启动 x11vnc（-xkb 启用 XKB，改善中文输入/剪贴板兼容性）
echo "[5/6] Starting x11vnc..."
x11vnc -display :99 -forever -nopw -listen 0.0.0.0 -rfbport $VNC_PORT -shared -xkb -clipboard &
sleep 1

# 6. 启动 noVNC + websockify
echo "[6/6] Starting noVNC on port $NOVNC_PORT..."
cd /opt/novnc
./utils/novnc_proxy --vnc localhost:$VNC_PORT --listen $NOVNC_PORT &

echo ""
echo "============================================="
echo "  Ready!"
echo "  noVNC UI: http://localhost:$NOVNC_PORT/vnc.html"
echo "  CDP API:  http://localhost:$CDP_PORT"
echo "  中文输入: Ctrl+Space 切换输入法"
echo "============================================="

# 等待所有后台进程
wait
