from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI

from app.api import publish_viewer_proxy, vnc_proxy
from app.api.deps import agent
from app.api.v1.router import api_router
from app.core.config import (
    PENDING_CHANNEL_SWEEP_INTERVAL_SECONDS,
    PENDING_CHANNEL_TTL_SECONDS,
    PROXY_ASSIGNMENTS_PATH,
    PROXY_CONFIG_PATH,
    PROXY_ENABLED,
    PROXY_FAILURE_THRESHOLD,
    PROXY_HEALTH_CHECK_ENABLED,
    PROXY_HEALTH_CHECK_INTERVAL,
)
from app.schemas.common import HealthResponse
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


async def _pending_channel_sweep_loop():
    """周期清扫僵尸 pending 渠道（创建超过 PENDING_CHANNEL_TTL_SECONDS 仍未绑定）。

    启动时的 purge_pending_channels 只清掉重启前的遗留；本任务覆盖运行期用户放弃
    登录积累的空渠道。TTL<=0 时不删，仅按间隔空转。
    """
    while True:
        await asyncio.sleep(PENDING_CHANNEL_SWEEP_INTERVAL_SECONDS)
        try:
            purged = agent.channel_store.purge_stale_pending_channels(PENDING_CHANNEL_TTL_SECONDS)
            if purged:
                _log.info(
                    "清扫僵尸 pending 渠道 %d 个（创建超过 %.0f 秒未绑定）",
                    purged,
                    PENDING_CHANNEL_TTL_SECONDS,
                )
        except Exception as exc:
            _log.warning("清扫僵尸 pending 渠道失败: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Surface a misconfigured / unreachable store at boot rather than on the
    # first request.
    try:
        agent.job_store.ready()
        agent.channel_store.ready()
        from app.api.v1.accounts import _default_account_service

        _default_account_service().store.ready()
    except Exception as exc:
        _log.error("store is not ready: %s", exc, exc_info=True)
        raise

    closed_count = agent.close_stale_running_jobs_after_restart()
    if closed_count:
        _log.info("Closed %d stale running job(s) after service restart.", closed_count)
    resumed_count = await agent.resume_queued_publish_jobs_after_restart()
    if resumed_count:
        _log.info("Resumed %d queued publish job(s) after service restart.", resumed_count)

    # 多 IP 代理：PROXY_ENABLED=true 时初始化分配管理器。proxies.yaml 缺失/格式错误
    # 直接抛异常阻止启动——严格模式，避免带着无效代理配置上线后任务全失败。
    if PROXY_ENABLED:
        from app.proxy.assignment import init_assignment_manager

        try:
            mgr = init_assignment_manager(
                PROXY_CONFIG_PATH, PROXY_ASSIGNMENTS_PATH, PROXY_FAILURE_THRESHOLD
            )
            _log.info(
                "多 IP 代理已启用：ip_pool=%d, 已绑定渠道=%d, failure_threshold=%d",
                len(mgr.config.ip_pool),
                len(mgr.assignments),
                PROXY_FAILURE_THRESHOLD,
            )

            # 启动 IP 健康检查循环（可选）
            proxy_health_check_task = None
            if PROXY_HEALTH_CHECK_ENABLED:
                proxy_health_check_task = asyncio.create_task(
                    mgr.start_health_check_loop(PROXY_HEALTH_CHECK_INTERVAL)
                )
                _log.info(
                    "IP 健康检查循环已启动：interval=%d秒",
                    PROXY_HEALTH_CHECK_INTERVAL,
                )
        except Exception as exc:
            _log.error("代理模块初始化失败（PROXY_ENABLED=true）: %s", exc, exc_info=True)
            raise
    else:
        _log.info("多 IP 代理未启用（PROXY_ENABLED=false），所有渠道直连。")
        proxy_health_check_task = None

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

    # 宽限会话回收：登录成功后保留一段时间供用户查看/自行关闭，按空闲/TTL 拆除。
    try:
        await agent.remote_runner.start_session_reaper()
    except Exception as exc:
        _log.warning("启动登录会话回收巡检失败: %s", exc)

    cleanup_task = asyncio.create_task(_job_log_cleanup_loop())
    pending_sweep_task = asyncio.create_task(_pending_channel_sweep_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        pending_sweep_task.cancel()
        if proxy_health_check_task is not None:
            proxy_health_check_task.cancel()
            try:
                await proxy_health_check_task
            except asyncio.CancelledError:
                pass
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


# Protected business routes — Bearer token enforced at the router level.
app.include_router(api_router, prefix="/api/v1", dependencies=[Depends(require_api_token)])
app.state.remote_login_runner = agent.remote_runner
# Internal VNC proxy (already include_in_schema=False).
app.include_router(vnc_proxy.router)
# 发文实时查看反代：把本机 viewer 端口经主服务暴露，鉴权依赖不可猜的 job_id（同 vnc_proxy）。
app.include_router(publish_viewer_proxy.router)


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
