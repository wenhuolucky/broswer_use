FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

ARG TARGETARCH
ARG KASMVNC_VERSION=1.4.0
ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn

# base 是 Ubuntu noble；把官方 apt 源(archive/security/ports.ubuntu.com)换成国内镜像加速。
# 仅换主机名、保留 /ubuntu 与 /ubuntu-ports 路径，TUNA 两者都有；老/新(deb822)源文件都覆盖。
RUN sed -i \
        -e "s|archive.ubuntu.com|${APT_MIRROR}|g" \
        -e "s|security.ubuntu.com|${APT_MIRROR}|g" \
        -e "s|ports.ubuntu.com|${APT_MIRROR}|g" \
        /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null || true

# playwright base 已含 Chromium 运行库与 xvfb，这里仅补本服务额外需要的：
#   curl/ca-certificates —— KasmVNC 下载 + HEALTHCHECK
#   xvfb/x11-utils       —— entrypoint 起虚拟显示并用 xdpyinfo 等就绪(幂等，已装则跳过)
#   fonts-noto-cjk/emoji —— 中文/表情渲染，否则登录页是豆腐块
#   procps               —— 进程工具
#   openbox              —— 远程登录会话的轻量窗口管理器：让 --kiosk 的 Chromium
#                           随 KasmVNC 桌面 resize 自适应铺满(否则窗口跟不上、只剩左上角)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        xvfb \
        x11-utils \
        procps \
        openbox \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    url="https://github.com/kasmtech/KasmVNC/releases/download/v${KASMVNC_VERSION}/kasmvncserver_bookworm_${KASMVNC_VERSION}_${TARGETARCH:-amd64}.deb"; \
    if curl -fsSL -o /tmp/kasmvnc.deb "$url"; then \
        apt-get update && apt-get install -y --no-install-recommends /tmp/kasmvnc.deb; \
        rm -rf /var/lib/apt/lists/*; \
    else \
        echo "WARN: KasmVNC download failed ($url); remote login streaming will be unavailable"; \
    fi; \
    rm -f /tmp/kasmvnc.deb

RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY entrypoint.sh ./
RUN chmod +x /app/entrypoint.sh

EXPOSE 8833

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${SERVICE_PORT:-8833}/health" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
