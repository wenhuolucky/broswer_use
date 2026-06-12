"""
基于 loguru 的结构化日志配置

每个发布任务有独立的 job_id（与 request_id 同一值），日志前缀始终包含 job_id。
日志记录内容：收到请求详情、每一步动作、耗时、LLM 模型、token 消耗、返回结果等。

实现说明：
- 底层统一由 loguru 写出，负责所有 sink（控制台 + 文件）以及切割、保留、压缩。
- 业务代码仍然拿到标准库 ``logging.Logger`` / ``LoggerAdapter``，因此既有调用方
  （``%s`` 惰性格式化、``exc_info=True``、``logger.exception`` 等）无需改动。
  所有标准库日志通过 ``InterceptHandler`` 转发进 loguru。

日志同时输出到：
- 控制台（stderr）
- 按日期切割的统一文件：logs/service.log（每天 00:00 轮转，保留 14 天，
  旧文件 zip 压缩）——这是按时间排查的主日志，每行都带 job_id。
- 任务级别的文件：logs/jobs/{YYYY-MM-DD}/{job_id}.log，供运维在服务器侧按 job_id
  廉价排查单个任务（不对外暴露 HTTP 接口）。要点：
  * 每个 job 只有一个文件（不再额外写 logs/requests/，那是重复内容）；
  * per-job 的 loguru sink 用 LRU 上界（``_MAX_LIVE_JOB_SINKS``）管理，避免句柄/
    过滤开销随任务数无限增长——这是历史实现的泄漏点；
  * 按日期分目录，``cleanup_old_job_logs`` 直接删过期日期目录即可（保留
    ``JOB_LOG_RETENTION_DAYS`` 天，与主日志一致）。
"""
from __future__ import annotations

import inspect
import logging
import shutil
import sys
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger as _loguru

from app.core.runtime import LOG_DIR, LOG_LEVEL

# per-job 日志保留天数（与主日志 retention 一致）。
JOB_LOG_RETENTION_DAYS = 14
# 同时常驻的 per-job 文件 sink 上界。已结束的任务不再写日志，其 sink 会随新任务
# 进来被 LRU 淘汰（文件已落盘，淘汰只关闭句柄，不影响后续按文件读取日志）。
_MAX_LIVE_JOB_SINKS = 128


# loguru 输出格式，与旧版标准库格式保持一致（asctime | level | request_id | module:line | message）
_LOGURU_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{extra[request_id]: <36} | {extra[module]}:{extra[line]} | {message}"
)

# job_id -> loguru sink id，按最近使用排序的 LRU；超过上界时淘汰最久未用的 sink。
_job_sinks: "OrderedDict[str, int]" = OrderedDict()
# job_id -> 该任务日志文件的规范路径。仅在首次创建时按当日日期定一次，之后复用，
# 避免同一任务跨天时日志被拆到两个日期目录。路径缓存很轻量，不随 sink 淘汰。
_job_log_paths: dict[str, Path] = {}
_configured = False


class _ContextFilter(logging.Filter):
    """为每条记录补齐 job_id / request_id，使直接使用 raw Logger 的调用方也带上下文。"""

    def __init__(self, job_id: str, request_id: str | None = None) -> None:
        super().__init__()
        self._job_id = job_id
        self._request_id = request_id if request_id is not None else job_id

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "job_id"):
            record.job_id = self._job_id
        if not hasattr(record, "request_id"):
            record.request_id = self._request_id
        return True


class _InterceptHandler(logging.Handler):
    """把标准库 logging 的记录转发给 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        # 将标准库 level 映射为 loguru level 名称。
        try:
            level = _loguru.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 找到真正发起日志调用的调用栈深度，使 loguru 记录到正确的模块/行号。
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        request_id = getattr(record, "request_id", None) or getattr(record, "job_id", None) or "-"
        job_id = getattr(record, "job_id", None) or request_id

        _loguru.bind(
            request_id=request_id,
            job_id=job_id,
            module=record.module,
            line=record.lineno,
        ).opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _configure_loguru() -> None:
    """初始化全局 loguru sink，并把标准库 logging 接管到 loguru。仅执行一次。"""
    global _configured
    if _configured:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = LOG_LEVEL.upper()

    _loguru.remove()

    # 控制台
    _loguru.add(
        sys.stderr,
        level=level,
        format=_LOGURU_FORMAT,
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )

    # 按日期切割的主日志文件
    _loguru.add(
        LOG_DIR / "service.log",
        level=level,
        format=_LOGURU_FORMAT,
        rotation="00:00",
        retention="14 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )

    # 把标准库 root logger（含 uvicorn / browser-use 等第三方库）接管到 loguru
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        std = logging.getLogger(name)
        std.handlers = [_InterceptHandler()]
        std.propagate = False

    _configured = True


def job_log_path(job_id: str) -> Path:
    """返回该 job 的规范日志文件路径 logs/jobs/{YYYY-MM-DD}/{job_id}.log。

    首次调用时按当日日期固定，之后复用缓存，保证同一任务始终写同一个文件。
    """
    path = _job_log_paths.get(job_id)
    if path is None:
        date_dir = datetime.now().strftime("%Y-%m-%d")
        path = LOG_DIR / "jobs" / date_dir / f"{job_id}.log"
        _job_log_paths[job_id] = path
    return path


def _ensure_job_sink(job_id: str) -> None:
    """为指定 job_id 挂载唯一的任务级文件 sink（按 extra.job_id 过滤），幂等。

    使用 LRU 上界管理 sink：已结束的任务不会再写日志，其 sink 会随新任务进来被
    淘汰（``_loguru.remove``）。这样常驻文件句柄和「每条日志逐个匹配 filter」的开销
    都被限制在 ``_MAX_LIVE_JOB_SINKS`` 以内，不随历史任务数无限增长。
    """
    if job_id in _job_sinks:
        _job_sinks.move_to_end(job_id)
        return

    path = job_log_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 刻意使用 enqueue=False（同步写入），避免 loguru 为每个 sink 各起一个后台线程。
    # 同一任务的编排日志与内核日志 extra.job_id 都等于 job_id，单个 sink 即可覆盖
    # （历史实现额外写的 logs/requests/ 是同内容副本，已去除）。
    sink_id = _loguru.add(
        path,
        level=LOG_LEVEL.upper(),
        format=_LOGURU_FORMAT,
        filter=lambda rec, jid=job_id: rec["extra"].get("job_id") == jid,
        encoding="utf-8",
        enqueue=False,
        backtrace=False,
        diagnose=False,
    )
    _job_sinks[job_id] = sink_id

    while len(_job_sinks) > _MAX_LIVE_JOB_SINKS:
        _old_job, old_sink = _job_sinks.popitem(last=False)
        try:
            _loguru.remove(old_sink)
        except ValueError:
            pass


def cleanup_old_job_logs(days: int = JOB_LOG_RETENTION_DAYS) -> int:
    """删除 logs/jobs/ 下早于 ``days`` 天的日期目录，返回删除的目录数。

    按日期分目录使过期清理变成「删整个日期目录」，无需逐文件 stat mtime。
    """
    jobs_dir = LOG_DIR / "jobs"
    if not jobs_dir.is_dir():
        return 0
    cutoff = datetime.now().date() - timedelta(days=days)
    removed = 0
    for child in jobs_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            day = datetime.strptime(child.name, "%Y-%m-%d").date()
        except ValueError:
            continue  # 非日期目录，跳过
        if day < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


def _setup_publish_logger(job_id: str) -> logging.LoggerAdapter:
    """
    内部函数：创建或获取 job_id 对应的发布日志器。

    - 第一次调用时挂载该 job_id 的任务级文件 sink（控制台与主日志 sink 在全局
      初始化时已挂载）。
    - 同一 job_id 多次调用复用同一 sink，不会重复添加。
    - 返回 ``LoggerAdapter``，自动在每条日志上注入 job_id / request_id 上下文，
      底层经 ``InterceptHandler`` 流入 loguru。
    """
    _configure_loguru()
    _ensure_job_sink(job_id)

    logger = logging.getLogger(f"app.publish.{job_id}")
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    # propagate=True：交由 root 上的 InterceptHandler 处理，自身不再挂 handler。
    logger.handlers = []
    logger.propagate = True
    if not getattr(logger, "_publish_ctx_installed", False):
        logger.addFilter(_ContextFilter(job_id))
        logger._publish_ctx_installed = True

    return logging.LoggerAdapter(logger, {"job_id": job_id, "request_id": job_id})


def setup_request_logger(request_id: str) -> logging.Logger:
    """
    为单个请求创建独立 logger，所有日志自动带上 request_id。

    为保持向后兼容返回 raw ``Logger``（adapter 的 logger 字段），调用方继续按
    Logger 接口使用即可。
    """
    return _setup_publish_logger(request_id).logger


def setup_job_logger(job_id: str, log_dir=None) -> tuple[logging.LoggerAdapter, Path]:
    """为单个任务创建 logger，并返回 ``(adapter, log_path)``。

    - adapter 与 ``setup_request_logger`` 共享同一底层 logger，因此编排日志与发文
      内核日志都进入同一个 logs/jobs/{YYYY-MM-DD}/{job_id}.log。
    - log_path 指向该任务日志文件（编排层会把它存进 job store，供日志接口定位文件）。

    ``log_dir`` 参数仅为兼容旧签名而保留；任务日志统一落在配置的 ``LOG_DIR`` 下。
    """
    adapter = _setup_publish_logger(job_id)
    return adapter, job_log_path(job_id)


def get_service_logger() -> logging.LoggerAdapter:
    """获取服务级别的全局 logger（不带 request_id，标记为 SYSTEM）。"""
    _configure_loguru()
    logger = logging.getLogger("publish.service")
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    logger.handlers = []
    logger.propagate = True
    if not getattr(logger, "_publish_ctx_installed", False):
        logger.addFilter(_ContextFilter("SYSTEM"))
        logger._publish_ctx_installed = True
    return logging.LoggerAdapter(logger, {"job_id": "SYSTEM", "request_id": "SYSTEM"})
