"""
结构化日志配置

每个请求有独立的 request_id，日志前缀始终包含 request_id。
日志记录内容：收到请求详情、每一步动作、耗时、LLM 模型、token 消耗、返回结果等。
日志文件按日期分割：logs/publish_service_YYYY-MM-DD.log
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

from publish_service.config import LOG_DIR, LOG_LEVEL, LOG_FORMAT


def setup_request_logger(request_id: str) -> logging.Logger:
    """
    为单个请求创建独立 logger，所有日志自动带上 request_id。

    同时输出到：
    - 控制台（stdout）
    - 按日期分割的文件：logs/publish_service_YYYY-MM-DD.log
    - 请求级别的文件：logs/requests/{request_id}.log（完整链路追踪）
    """
    logger = logging.getLogger(f"publish.{request_id}")
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    # 避免重复添加 handler（同一 request_id 可能多次调用）
    if logger.handlers:
        return logger

    # 带 request_id 的 formatter
    class RequestFormatter(logging.Formatter):
        def __init__(self):
            super().__init__(LOG_FORMAT)
            self._request_id = request_id

        def format(self, record):
            record.request_id = self._request_id
            return super().format(record)

    formatter = RequestFormatter()

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

    # 请求级别的文件 handler（完整链路追踪，便于按 request_id 排查）
    request_log_dir = LOG_DIR / "requests"
    request_log_dir.mkdir(parents=True, exist_ok=True)
    request_file = request_log_dir / f"{request_id}.log"
    request_handler = logging.FileHandler(request_file, encoding="utf-8")
    request_handler.setFormatter(formatter)
    logger.addHandler(request_handler)

    return logger


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
