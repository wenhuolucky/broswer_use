FROM mcr.microsoft.com/playwright/python:v1.50.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_DATA_DIR=/app/data \
    APP_LOG_DIR=/app/logs \
    APP_REMOTE_PROFILE_DIR=/app/data/remote_profiles \
    CLOUDFLARED_PATH=/usr/local/bin/cloudflared \
    REMOTE_LOGIN_TIMEOUT_SECONDS=600

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates xvfb \
    && rm -rf /var/lib/apt/lists/*

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

CMD ["sh", "-c", "Xvfb ${DISPLAY:-:99} -screen 0 1280x1024x24 -nolisten tcp & export DISPLAY=${DISPLAY:-:99}; exec python -m uvicorn app.server:app --host 0.0.0.0 --port 19000 --workers 1"]
