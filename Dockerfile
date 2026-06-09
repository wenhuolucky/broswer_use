FROM mcr.microsoft.com/playwright/python:v1.50.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_DATA_DIR=/app/data \
    APP_LOG_DIR=/app/logs \
    APP_REMOTE_PROFILE_DIR=/app/data/remote_profiles \
    CLOUDFLARED_PATH=/usr/local/bin/cloudflared \
    KASMVNC_BIN=Xvnc \
    MAX_REMOTE_LOGIN_SESSIONS=4 \
    DISPLAY_BASE=100 \
    KASMVNC_PORT_BASE=6900 \
    REMOTE_VNC_SCREEN=1440x900x24 \
    REMOTE_BROWSER_WINDOW_WIDTH=1440 \
    REMOTE_BROWSER_WINDOW_HEIGHT=900 \
    REMOTE_VIEWER_MAX_WIDTH=1440 \
    REMOTE_VIEWER_MAX_HEIGHT=900 \
    REMOTE_VIEWER_JPEG_QUALITY=85

WORKDIR /app

ARG TARGETARCH
ARG KASMVNC_VERSION=1.4.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        xvfb \
        x11-utils \
        procps \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        libnss3 \
        libxss1 \
        libasound2 \
        libgbm1 \
        libxrandr2 \
        libxdamage1 \
        libxcomposite1 \
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

RUN curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    -o /usr/local/bin/cloudflared \
    && chmod +x /usr/local/bin/cloudflared

COPY requirements.txt .
RUN python -m pip install -U pip \
    && pip install -r requirements.txt \
    && python -m playwright install chromium

COPY . .

RUN mkdir -p /app/data /app/logs

EXPOSE 19000

CMD ["sh", "-c", "Xvfb ${DISPLAY:-:99} -screen 0 ${XVFB_SCREEN:-1920x1080x24} -nolisten tcp & export DISPLAY=${DISPLAY:-:99}; exec python -m uvicorn app.server:app --host 0.0.0.0 --port 19000 --workers 1"]
