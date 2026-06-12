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

from app.core.config import AGENT_LOG_COLOR_ENABLED
from app.core.runtime import LOG_DIR, LOG_LEVEL, LOG_FORMAT


class AgentColorFormatter(logging.Formatter):
    """Add ANSI colors to agent diagnostic markers for console output only."""

    _RESET = "\033[0m"
    _COLORS = {
        "[AgentLLM:": "\033[36m",
        "[AgentStep:": "\033[34m",
        "[AgentState:": "\033[90m",
        "[AgentGuard:": "\033[33m",
        "[AgentTool:": "\033[35m",
        "[AgentFinal:": "\033[32m",
    }

    def __init__(self, fmt: str, *, enable_color: bool = AGENT_LOG_COLOR_ENABLED):
        super().__init__(fmt)
        self.enable_color = enable_color

    def format(self, record):
        formatted = super().format(record)
        if not self.enable_color:
            return formatted
        for marker, color in self._COLORS.items():
            if marker in formatted:
                return f"{color}{formatted}{self._RESET}"
        return formatted


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
        console_formatter = AgentColorFormatter(LOG_FORMAT)

        # 控制台 handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
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


def get_vnc_logger() -> logging.Logger:
    """远程连接 VNC 代理专用 logger。

    远程登录的 WebSocket 代理（app/api/vnc_proxy.py）不属于任何 job_id，
    它的连接日志是 session 级别的。这个 logger 同时输出到：
    - 控制台（stdout）
    - 文件 logs/vnc_proxy.log

    目的：在生产 uvicorn 环境下也能稳定看到 VNC 连接的打开/断开/断开原因，
    用于排查远程连接 8-9 秒断开等问题。

    注意：默认的 app.vnc_proxy logger 没有挂 handler，靠 propagate 传到 root，
    而 uvicorn 不会把 root 配成 INFO+console+file，导致 INFO 日志被丢弃。
    这里显式挂 handler 并关闭 propagate，确保日志不丢。

    session_id 通过 LOG_FORMAT 的 request_id 字段透出，没有时填 "VNC"。
    """
    logger = logging.getLogger("app.vnc_proxy")
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    logger.propagate = False

    if logger.handlers:
        return logger

    class VncFormatter(logging.Formatter):
        def format(self, record):
            if not hasattr(record, "request_id"):
                record.request_id = "VNC"
            return super().format(record)

    formatter = VncFormatter(LOG_FORMAT)
    console_formatter = AgentColorFormatter(LOG_FORMAT)

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # 文件 handler：logs/vnc_proxy.log
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    vnc_file = LOG_DIR / "vnc_proxy.log"
    file_handler = logging.FileHandler(vnc_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

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
    console_handler.setFormatter(AgentColorFormatter(LOG_FORMAT))
    logger.addHandler(console_handler)

    return logger
