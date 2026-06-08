from __future__ import annotations

import logging
import sys
from pathlib import Path

from app.core.config import LOG_DIR


LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(job_id)s | %(module)s:%(lineno)d | %(message)s"


class JobFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "job_id"):
            record.job_id = "-"
        return super().format(record)


def setup_job_logger(job_id: str, log_dir: Path = LOG_DIR) -> tuple[logging.LoggerAdapter, Path]:
    jobs_dir = Path(log_dir) / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    log_path = jobs_dir / f"{job_id}.log"

    logger = logging.getLogger(f"app.job.{job_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()

    formatter = JobFormatter(LOG_FORMAT)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logging.LoggerAdapter(logger, {"job_id": job_id}), log_path
