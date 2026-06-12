from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI

from app.api import vnc_proxy
from app.api.routes import agent, public_router, router
from app.api.schemas import HealthResponse
from app.core.auth import require_api_token
from app.core.request_logging import (
    JOB_LOG_RETENTION_DAYS,
    cleanup_old_job_logs,
    get_service_logger,
)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

_log = get_service_logger()


async def _job_log_cleanup_loop():
    """每天清理一次过期的 per-job 日志目录（保留 JOB_LOG_RETENTION_DAYS 天）。

    首轮在 sleep 之前执行，因此启动时即清理一次，长期运行也不会无限堆积。
    """
    while True:
        try:
            removed = cleanup_old_job_logs(JOB_LOG_RETENTION_DAYS)
            if removed:
                _log.info("清理过期任务日志目录 %d 个（保留 %d 天）", removed, JOB_LOG_RETENTION_DAYS)
        except Exception as exc:
            _log.warning("清理任务日志目录失败: %s", exc)
        await asyncio.sleep(24 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Surface a misconfigured / unreachable store at boot rather than on the
    # first request.
    try:
        agent.job_store.ready()
        agent.channel_store.ready()
    except Exception as exc:
        _log.error("store is not ready: %s", exc, exc_info=True)
        raise

    closed_count = agent.close_stale_running_jobs_after_restart()
    if closed_count:
        _log.info("Closed %d stale running job(s) after service restart.", closed_count)

    # 重启后清理遗留的 pending 渠道：它们只在一次登录会话进行中存在，
    # 会话已随重启失效（对应的 login job 上一步已被置为 failed）。
    purged = agent.channel_store.purge_pending_channels()
    if purged:
        _log.info("Purged %d stale pending channel(s) after service restart.", purged)

    # 预热登录会话池（REMOTE_LOGIN_WARM_PER_PLATFORM<=0 时为空操作）。
    try:
        await agent.remote_runner.start_warm_pool()
    except Exception as exc:
        _log.warning("预热登录会话池启动失败: %s", exc)

    cleanup_task = asyncio.create_task(_job_log_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await agent.remote_runner.shutdown()
        except Exception as exc:
            _log.warning("关闭远程登录运行器失败: %s", exc)


app = FastAPI(
    title="Browser Publish Service",
    description="One-call publish service with automatic per-user cookie acquisition.",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)


@app.get("/health", response_model=HealthResponse, tags=["service"])
async def health():
    return HealthResponse()


# Public, unauthenticated routes (readiness).
app.include_router(public_router, prefix="/api/v1")
# Protected business routes — Bearer token enforced at the router level.
app.include_router(router, prefix="/api/v1", dependencies=[Depends(require_api_token)])
app.state.remote_login_runner = agent.remote_runner
# Internal VNC proxy (already include_in_schema=False).
app.include_router(vnc_proxy.router)


@app.get("/scalar", include_in_schema=False)
async def scalar_html():
    from scalar_fastapi import get_scalar_api_reference

    # persist_auth：在 Scalar 里输入一次 Bearer token 即存入浏览器 localStorage，
    # 服务端重启也不丢，省去每次重填。token 不写进页面源码。
    return get_scalar_api_reference(
        openapi_url=app.openapi_url,
        title=app.title,
        persist_auth=True,
    )


if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8833, workers=1, reload=False)
