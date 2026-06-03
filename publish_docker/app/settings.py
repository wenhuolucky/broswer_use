"""Runtime settings for publish_docker phase 1."""

from __future__ import annotations

import os


class Settings:
    service_host = os.getenv("SERVICE_HOST", "0.0.0.0")
    service_port = int(os.getenv("SERVICE_PORT", "8000"))
    queue_max_size = int(os.getenv("PUBLISH_QUEUE_MAX_SIZE", "20"))
    job_retention_hours = int(os.getenv("JOB_RETENTION_HOURS", "24"))


settings = Settings()
