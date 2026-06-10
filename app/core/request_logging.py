"""
结构化日志配置

每个发布任务有独立的 job_id（与 request_id 同一值），日志前缀始终包含 job_id。
日志记录内容：收到请求详情、每一步动作、耗时、LLM 模型、token 消耗、返回结果等。
日志同时输出到：
- 控制台（stdout）
- 按日期分割的文件：logs/publish_service_YYYY-MM-DD.log
- 请求级别的文件：logs/requests/{job_id}.log
- 任务级别的文件：logs/jobs/{job_id}.log（与 setup_job_logger 共享 logger）
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

from app.core.runtime import LOG_DIR, LOG_LEVEL, LOG_FORMAT


def _setup_publish_logger(job_id: str) -> logging.LoggerAdapter:
    """
    内部函数：创建或获取 job_id 对应的发布日志器。

    - 第一次调用时同时挂载 console / daily / request / job 四份 handler。
    - 同一 job_id 多次调用会复用同一个 logger，不会重复添加 handler。
    - 返回 LoggerAdapter，自动在每条日志上注入 job_id / request_id 上下文。
    """
    logger = logging.getLogger(f"app.publish.{job_id}")
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    logger.propagate = False

    if not logger.handlers:
        class PublishFormatter(logging.Formatter):
            def format(self, record):
                if not hasattr(record, "job_id"):
                    record.job_id = job_id
                if not hasattr(record, "request_id"):
                    record.request_id = job_id
                return super().format(record)

        formatter = PublishFormatter(LOG_FORMAT)

        # 控制台 handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # 按日期分割的文件 handler
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        daily_file = LOG_DIR / f"publish_service_{date_str}.log"
        daily_handler = logging.FileHandler(daily_file, encoding="utf-8")
        daily_handler.setFormatter(formatter)
        logger.addHandler(daily_handler)

        # 请求级别的文件 handler（按 request_id 排查完整链路）
        request_log_dir = LOG_DIR / "requests"
        request_log_dir.mkdir(parents=True, exist_ok=True)
        request_file = request_log_dir / f"{job_id}.log"
        request_handler = logging.FileHandler(request_file, encoding="utf-8")
        request_handler.setFormatter(formatter)
        logger.addHandler(request_handler)

        # 任务级别的文件 handler（API 返回的 log_file_path 指向这里）
        jobs_dir = LOG_DIR / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        job_file = jobs_dir / f"{job_id}.log"
        job_handler = logging.FileHandler(job_file, encoding="utf-8")
        job_handler.setFormatter(formatter)
        logger.addHandler(job_handler)

    return logging.LoggerAdapter(logger, {"job_id": job_id, "request_id": job_id})


def setup_request_logger(request_id: str) -> logging.Logger:
    """
    为单个请求创建独立 logger，所有日志自动带上 request_id。

    实际委托给 _setup_publish_logger，输出到 console、daily、request、job
    四份目标。为了保持向后兼容，这里返回 raw Logger（adapter 的 logger
    字段），调用方继续按 Logger 接口使用即可。
    """
    return _setup_publish_logger(request_id).logger


def get_service_logger() -> logging.Logger:
    """获取服务级别的全局 logger（不带 request_id）"""
    logger = logging.getLogger("publish.service")
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    if logger.handlers:
        return logger

    class ServiceFormatter(logging.Formatter):
        def format(self, record):
            if not hasattr(record, "request_id"):
                record.request_id = "SYSTEM"
            return super().format(record)

    formatter = ServiceFormatter(LOG_FORMAT)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
