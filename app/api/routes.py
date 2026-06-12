from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import (
    ChannelDeleteResponse,
    ChannelListResponse,
    ChannelResponse,
    CreateLoginJobRequest,
    CreatePublishJobRequest,
    JobCreatedResponse,
    JobListResponse,
    JobResponse,
    LoginSessionCreatedResponse,
    LoginSessionListResponse,
    LoginSessionResponse,
    PlatformInfo,
    PlatformListResponse,
    ReadinessResponse,
    channel_to_list_item,
    channel_to_response,
    job_created_from,
    job_to_list_item,
    job_to_response,
    login_session_created_from,
    login_session_to_list_item,
    login_session_to_response,
)
from app.domain.channel import validate_channel_id
from app.domain.job import AutoPublishResponse, Job
from app.platforms import registry
from app.publishing.orchestrator import PublishAgent
from app.remote.login import _login_url_for

agent = PublishAgent()

# Protected resource router — mounted under /api/v1 with the Bearer dependency
# (see app/server.py). Public (unauthenticated) routes live on public_router.
router = APIRouter()
public_router = APIRouter()


def _raise_for_action_code(resp: AutoPublishResponse) -> None:
    """Translate an agent action result code into a real HTTP error, or pass on success."""
    code = resp.code
    if code in (200, 202):
        return
    if code == 404:
        raise HTTPException(status_code=404, detail=resp.message or "not found")
    if code == 409:
        raise HTTPException(status_code=409, detail=resp.message or "conflict")
    raise HTTPException(status_code=code if code >= 400 else 500, detail=resp.message or "error")


# ---------------------------------------------------------------------------
# Public (unauthenticated)
# ---------------------------------------------------------------------------
@public_router.get("/ready", response_model=ReadinessResponse, tags=["service"])
async def ready():
    try:
        agent.job_store.ready()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database not ready") from exc
    database = "postgresql" if agent.job_store._pool is not None else "memory"
    return ReadinessResponse(status="ok", database=database)


# ---------------------------------------------------------------------------
# Platforms
# ---------------------------------------------------------------------------
@router.get("/platforms", response_model=PlatformListResponse, tags=["platforms"])
async def list_platforms():
    platforms = [
        PlatformInfo(id=pid, name=cfg.name, home_url=cfg.home_url, login_url=_login_url_for(pid))
        for pid, cfg in registry.configs().items()
    ]
    return PlatformListResponse(platforms=platforms)


# ---------------------------------------------------------------------------
# Jobs（发文任务：由 LLM 驱动浏览器，长时间执行，故需轮询状态、取消）
# ---------------------------------------------------------------------------
# 发文与登录刻意拆成两套资源：发文是 LLM 自动操作（/jobs），登录是真人手动操作
# （/login-sessions）。两者底层共用同一个 Job 存储（靠 job.type 区分），这里在
# 路由层用 _require_job 强隔离——拿发文 job_id 去敲 /login-sessions/* 会得到 404，
# 反之亦然，让两个资源在对外契约上互不可见。
def _require_job(job_id: str, expected_type: str, not_found_detail: str) -> Job:
    try:
        job = agent.job_store.get(job_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="查询任务状态失败") from exc
    if job is None or job.type != expected_type:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return job


@router.post("/jobs", response_model=JobCreatedResponse, status_code=202, tags=["jobs"])
async def create_publish_job(request: CreatePublishJobRequest):
    resp = await agent.submit(request)
    if resp.code == 404:
        detail = str((resp.data or {}).get("error_detail") or "") or "渠道不存在，请先登录"
        raise HTTPException(status_code=404, detail=detail)
    return job_created_from(resp, job_type="publish")


@router.get("/jobs", response_model=JobListResponse, tags=["jobs"])
async def list_jobs(
    channel_id: str | None = Query(default=None),
    status: str | None = Query(default=None, description="逗号分隔的状态过滤"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    statuses = {s.strip() for s in status.split(",") if s.strip()} if status else None
    jobs = agent.job_store.list_jobs(
        channel_id=channel_id, statuses=statuses, job_type="publish", limit=limit, offset=offset
    )
    items = [job_to_list_item(job) for job in jobs]
    return JobListResponse(jobs=items, count=len(items), limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=JobResponse, tags=["jobs"])
async def get_job(job_id: str):
    return job_to_response(_require_job(job_id, "publish", "任务不存在"))


@router.post("/jobs/{job_id}/save-cookie", response_model=JobResponse, tags=["jobs"])
async def save_cookie(job_id: str):
    # 发文任务在缺 Cookie 时也会走远程登录，故发文资源同样保留 save-cookie。
    _require_job(job_id, "publish", "任务不存在")
    resp = await agent.save_remote_cookie(job_id)
    _raise_for_action_code(resp)
    return job_to_response(_require_job(job_id, "publish", "任务不存在"))


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse, tags=["jobs"])
async def cancel_job(job_id: str):
    _require_job(job_id, "publish", "任务不存在")
    resp = await agent.cancel_job(job_id)
    _raise_for_action_code(resp)
    return job_to_response(_require_job(job_id, "publish", "任务不存在"))


# 注意：日志是运维/调试关注点，且原始日志会泄漏内部细节（LLM prompt、页面 URL、
# 账号信息等），不属于第三方集成契约。故不对外提供 /jobs/{id}/logs 接口——运维可
# 在服务器侧直接看 logs/jobs/{date}/{job_id}.log 或 grep 统一日志 logs/service.log。


# ---------------------------------------------------------------------------
# Login sessions（登录会话：真人在流式浏览器里手动登录，与发文任务隔离。
# 仍是有状态的异步会话——占用稀缺的 Xvnc 显示槽，故同样需要查询状态、取消。）
# ---------------------------------------------------------------------------
@router.post("/login-sessions", response_model=LoginSessionCreatedResponse, status_code=202, tags=["login-sessions"])
async def create_login_session(request: CreateLoginJobRequest):
    resp = await agent.start_login_only(request)
    return login_session_created_from(resp)


@router.get("/login-sessions", response_model=LoginSessionListResponse, tags=["login-sessions"])
async def list_login_sessions(
    channel_id: str | None = Query(default=None),
    status: str | None = Query(default=None, description="逗号分隔的状态过滤"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    statuses = {s.strip() for s in status.split(",") if s.strip()} if status else None
    jobs = agent.job_store.list_jobs(
        channel_id=channel_id, statuses=statuses, job_type="login", limit=limit, offset=offset
    )
    items = [login_session_to_list_item(job) for job in jobs]
    return LoginSessionListResponse(sessions=items, count=len(items), limit=limit, offset=offset)


@router.get("/login-sessions/{session_id}", response_model=LoginSessionResponse, tags=["login-sessions"])
async def get_login_session(session_id: str):
    return login_session_to_response(_require_job(session_id, "login", "登录会话不存在"))


@router.post("/login-sessions/{session_id}/save-cookie", response_model=LoginSessionResponse, tags=["login-sessions"])
async def save_login_cookie(session_id: str):
    _require_job(session_id, "login", "登录会话不存在")
    resp = await agent.save_remote_cookie(session_id)
    _raise_for_action_code(resp)
    return login_session_to_response(_require_job(session_id, "login", "登录会话不存在"))


@router.delete("/login-sessions/{session_id}", response_model=LoginSessionResponse, tags=["login-sessions"])
async def cancel_login_session(session_id: str):
    _require_job(session_id, "login", "登录会话不存在")
    resp = await agent.cancel_job(session_id)
    _raise_for_action_code(resp)
    return login_session_to_response(_require_job(session_id, "login", "登录会话不存在"))


# ---------------------------------------------------------------------------
# Channels（已连接账号：channel_id ↔ 平台 ↔ 平台账号 ↔ cookie）
# ---------------------------------------------------------------------------
def _require_valid_channel_id(channel_id: str) -> None:
    """按路径取 channel_id 的接口不经过请求体模型校验，这里挡一次非法值，返回干净的
    422 而不是让下游抛 500。"""
    try:
        validate_channel_id(channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/channels", response_model=ChannelListResponse, tags=["channels"])
async def list_channels(
    platform: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    channels = agent.list_channels(platform=platform, limit=limit, offset=offset)
    items = [channel_to_list_item(c) for c in channels]
    return ChannelListResponse(channels=items, count=len(items), limit=limit, offset=offset)


@router.get("/channels/{channel_id}", response_model=ChannelResponse, tags=["channels"])
async def get_channel(channel_id: str):
    _require_valid_channel_id(channel_id)
    channel = agent.get_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    return channel_to_response(channel)


@router.post("/channels/{channel_id}/relogin", response_model=LoginSessionCreatedResponse, status_code=202, tags=["channels"])
async def relogin_channel(channel_id: str):
    _require_valid_channel_id(channel_id)
    if agent.get_channel(channel_id) is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    resp = await agent.start_relogin(channel_id)
    return login_session_created_from(resp)


@router.delete("/channels/{channel_id}", response_model=ChannelDeleteResponse, tags=["channels"])
async def delete_channel(channel_id: str):
    _require_valid_channel_id(channel_id)
    deleted = agent.delete_channel(channel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="渠道不存在")
    return ChannelDeleteResponse(channel_id=channel_id, deleted=True)
