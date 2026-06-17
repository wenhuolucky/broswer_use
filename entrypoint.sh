#!/usr/bin/env bash
#
# 容器入口脚本：
#   1. 准备运行所需的 data / logs 目录
#   2. 启动 Xvfb 虚拟显示（供发布浏览器使用；远程登录会自行拉起独立 Xvnc 显示）
#   3. 以 exec 方式启动 uvicorn，确保信号（SIGTERM/SIGINT）正确传递给主进程
#
set -euo pipefail

# ---- 可配置项（均可通过环境变量覆盖）----
SERVICE_WORKERS="${SERVICE_WORKERS:-1}"
# 发布浏览器使用的 Xvfb 显示号；未显式配置时默认 :99，避免 set -u 下因未绑定而崩溃。
export DISPLAY="${DISPLAY:-:99}"

log() { echo "[entrypoint] $*"; }

# 本服务为单进程架构（模块级 PublishAgent 单例、内存兜底存储、后台任务追踪、
# 共享同一块 Xvfb 显示），多 worker 会导致状态分裂/取消失效/显示争用。
# 强制回退到单 worker，避免误配置悄悄搞坏运行时。
if [ "${SERVICE_WORKERS}" != "1" ]; then
    log "WARN: 本服务仅支持单 worker，已将 SERVICE_WORKERS=${SERVICE_WORKERS} 强制回退为 1"
    SERVICE_WORKERS=1
fi

mkdir -p \
    "${APP_DATA_DIR}" \
    "${APP_LOG_DIR}"

# 清理可能残留的 X 锁文件，避免 "Server is already active" 冲突。
rm -f "/tmp/.X${DISPLAY#:}-lock" "/tmp/.X11-unix/X${DISPLAY#:}" 2>/dev/null || true

log "starting Xvfb on ${DISPLAY} (${XVFB_SCREEN})"
Xvfb "${DISPLAY}" -screen 0 "${XVFB_SCREEN}" -nolisten tcp &
XVFB_PID=$!

# 等待 Xvfb 就绪（最多 ~10s）。就绪/超时/进程已死都打日志，失败也继续启动服务。
xvfb_ready=0
for _ in $(seq 1 50); do
    if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
        log "WARN: Xvfb 进程 (pid=${XVFB_PID}) 已退出，发布浏览器将无法使用"
        break
    fi
    if xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then
        log "Xvfb is ready (pid=${XVFB_PID})"
        xvfb_ready=1
        break
    fi
    sleep 0.2
done
[ "${xvfb_ready}" = "1" ] || log "WARN: Xvfb 在 ~10s 内未就绪，仍继续启动服务（发布可能失败）"

log "starting uvicorn on ${SERVICE_HOST}:${SERVICE_PORT} (workers=${SERVICE_WORKERS})"
exec python -m uvicorn app.server:app \
    --host "${SERVICE_HOST}" \
    --port "${SERVICE_PORT}" \
    --workers "${SERVICE_WORKERS}"
