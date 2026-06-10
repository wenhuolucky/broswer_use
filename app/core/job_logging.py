from __future__ import annotations

import logging
import sys
from pathlib import Path

from app.core.config import LOG_DIR
from app.core.request_logging import _setup_publish_logger


# 兼容老调用方：保留 JobFormatter 暴露
class JobFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "job_id"):
            record.job_id = "-"
        return super().format(record)


def setup_job_logger(job_id: str, log_dir: Path = LOG_DIR) -> tuple[logging.LoggerAdapter, Path]:
    """
    为单个任务创建 logger，与 setup_request_logger 共享同一个底层 logger。

    返回 (adapter, log_path)：
    - adapter 与 setup_request_logger 返回的对象指向同一 logger，
      因此 agent 编排日志和 service 发布流程日志都会进入
      logs/jobs/{job_id}.log（API 返回的 log_file_path 指向这里），
      也会进入 logs/requests/{job_id}.log。
    - log_path 仍指向 logs/jobs/{job_id}.log。
    """
    jobs_dir = Path(log_dir) / "jobs"
    log_path = jobs_dir / f"{job_id}.log"
    adapter = _setup_publish_logger(job_id)
    return adapter, log_path
