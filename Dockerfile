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
ARG PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
ENV UV_DEFAULT_INDEX=${PIP_MIRROR}

RUN sed -i \
        -e "s|archive.ubuntu.com|${APT_MIRROR}|g" \
        -e "s|security.ubuntu.com|${APT_MIRROR}|g" \
        -e "s|ports.ubuntu.com|${APT_MIRROR}|g" \
        /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null || true

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
    url="https://github.com/kasmtech/KasmVNC/releases/download/v${KASMVNC_VERSION}/kasmvncserver_noble_${KASMVNC_VERSION}_${TARGETARCH:-amd64}.deb"; \
    if curl -fsSL -o /tmp/kasmvnc.deb "$url"; then \
        # 重试 apt-get update,避免镜像源个别 component(如 universe)瞬时拉取失败导致依赖缺失;
        # KasmVNC 仅用于远程登录串流,属可选组件,装不上时降级告警而非中断构建。
        ( apt-get update || apt-get update || apt-get update ) \
            && apt-get install -y --no-install-recommends /tmp/kasmvnc.deb \
            || echo "WARN: KasmVNC install failed; remote login streaming will be unavailable"; \
        rm -rf /var/lib/apt/lists/*; \
    else \
        echo "WARN: KasmVNC download failed ($url); remote login streaming will be unavailable"; \
    fi; \
    rm -f /tmp/kasmvnc.deb

RUN pip install --no-cache-dir -i ${PIP_MIRROR} uv

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
