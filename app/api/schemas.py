"""Public HTTP request/response models for the publish API + mappers.

Request models reuse the validated dataclass-construction models from
app.domain.job (so PublishAgent keeps using the same classes); they are
re-exported here under resource-oriented names for the API layer.

The caller-facing identity is ``channel_id`` — a service-issued handle bound 1:1
to a platform account. There is no ``user_id`` / ``platform`` on publish anymore.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.channel import Channel
from app.domain.job import (
    AutoPublishRequest as CreatePublishJobRequest,
    AutoTaskCreateResponse,
    LoginRequest as CreateLoginJobRequest,
    STATUS_CANCELLED,
    STATUS_CHECKING_COOKIE,
    STATUS_COOKIE_READY,
    STATUS_PUBLISHING,
    STATUS_QUEUED,
    STATUS_STARTING_REMOTE_LOGIN,
    STATUS_SUCCEEDED,
    STATUS_WAITING_COOKIE,
    Job,
)
from app.platforms import registry

__all__ = [
    "CreatePublishJobRequest",
    "CreateLoginJobRequest",
    "HealthResponse",
    "ReadinessResponse",
    "PlatformInfo",
    "PlatformListResponse",
    "ErrorInfo",
    "JobCreatedResponse",
    "PublishResult",
    "JobResponse",
    "JobListItem",
    "JobListResponse",
    "LoginSessionCreatedResponse",
    "LoginSessionResponse",
    "LoginSessionListItem",
    "LoginSessionListResponse",
    "ChannelResponse",
    "ChannelListItem",
    "ChannelListResponse",
    "ChannelDeleteResponse",
    "job_created_from",
    "job_to_response",
    "job_to_list_item",
    "login_session_created_from",
    "login_session_to_response",
    "login_session_to_list_item",
    "channel_to_response",
    "channel_to_list_item",
]

# Internal statuses that map to the external "running" (in-flight) state.
_RUNNING_STATUSES = {
    STATUS_QUEUED,
    STATUS_CHECKING_COOKIE,
    STATUS_COOKIE_READY,
    STATUS_STARTING_REMOTE_LOGIN,
    STATUS_PUBLISHING,
}


# ---------------------------------------------------------------------------
# Service-level
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "publish"


class ReadinessResponse(BaseModel):
    status: str = Field(description='"ok" or "down"')
    database: str = Field(description='"postgresql" or "memory" or "down"')


class PlatformInfo(BaseModel):
    id: str = Field(examples=["toutiao"])
    name: str = Field(examples=["toutiao"])
    home_url: str = Field(examples=["https://mp.toutiao.com"])
    login_url: str = Field(examples=["https://mp.toutiao.com/auth/page/login"])


class PlatformListResponse(BaseModel):
    platforms: list[PlatformInfo]


# ---------------------------------------------------------------------------
# Shared error shape
# ---------------------------------------------------------------------------
class ErrorInfo(BaseModel):
    """统一的失败信息块：``code`` 供程序分支，``detail`` 供人排错。仅在任务失败时出现。"""

    code: str = Field(description="机器可判断的错误码，如 remote_login_failed | cookie_invalid | job_failed")
    detail: str = Field(default="", description="人类可读的失败原因")


# ---------------------------------------------------------------------------
# Job creation
# ---------------------------------------------------------------------------
class JobCreatedResponse(BaseModel):
    job_id: str
    type: str = "publish"
    channel_id: str = ""
    task_status: str = Field(description="running | login_required | failed")
    message: str = ""
    # 仅 login_required 时有意义：让用户去这个流式浏览器地址完成登录。
    live_url: str = ""
    # 仅 failed 时出现。
    error: ErrorInfo | None = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "job_id": "9c833a78-4f42-4c98-aef6-cc7d0d06a7f8",
                "type": "publish",
                "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
                "task_status": "login_required",
                "message": "任务创建成功，需要用户登录",
                "live_url": "https://example.com/vnc/8c3d.../?token=...",
            }
        }
    }


# ---------------------------------------------------------------------------
# Job query
# ---------------------------------------------------------------------------
class PublishResult(BaseModel):
    success: bool = False
    account_name: str = ""
    platform_user_id: str = ""
    article_title: str = ""
    publish_signal: str = ""
    operation_time: str = ""


class JobResponse(BaseModel):
    job_id: str
    type: str = "publish"
    status: str = Field(description="raw internal status")
    task_status: str = Field(description="running | login_required | published | login_succeeded | failed | expired | closed | cancelled")
    message: str = ""
    channel_id: str = ""
    platform: str = ""
    title: str = ""
    cover_image_url: str = ""
    live_url: str = ""
    login_url: str = ""
    remote_session_id: str = ""
    article_url: str = ""
    publish_result: PublishResult | None = None
    # 仅 failed 时出现；统一失败建模，不再有 reason/error 两个并存的字符串字段。
    error: ErrorInfo | None = None
    created_at: str = ""
    updated_at: str = ""

    model_config = {
        "json_schema_extra": {
            "example": {
                "job_id": "9c833a78-4f42-4c98-aef6-cc7d0d06a7f8",
                "type": "publish",
                "status": "succeeded",
                "task_status": "published",
                "message": "文章发布成功",
                "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
                "platform": "toutiao",
                "title": "测试标题",
                "article_url": "https://www.toutiao.com/article/123456/",
                "publish_result": {"success": True, "account_name": "账号名称"},
            }
        }
    }


class JobListItem(BaseModel):
    job_id: str
    type: str = "publish"
    status: str
    channel_id: str = ""
    platform: str = ""
    title: str = ""
    created_at: str = ""
    updated_at: str = ""


class JobListResponse(BaseModel):
    jobs: list[JobListItem]
    count: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Login sessions
# ---------------------------------------------------------------------------
# 登录会话与发文任务（jobs）刻意分开建模：发文由 LLM 驱动浏览器，登录由真人在流式
# 浏览器里手动操作。它仍是有状态的异步会话（占用稀缺的 Xvnc 显示槽），故同样需要
# 查询状态与取消，但响应里不带任何发文相关字段（标题/正文/文章链接/发布结果）。
class LoginSessionCreatedResponse(BaseModel):
    session_id: str
    # 本次登录绑定/将绑定到的渠道句柄；登录成功后用它发文。
    channel_id: str = ""
    task_status: str = Field(description="login_required | failed")
    message: str = ""
    # 让用户去这个流式浏览器地址完成登录。
    live_url: str = ""
    # 仅 failed 时出现。
    error: ErrorInfo | None = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "session_id": "9c833a78-4f42-4c98-aef6-cc7d0d06a7f8",
                "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
                "task_status": "login_required",
                "message": "登录会话创建成功，需要用户登录",
                "live_url": "https://example.com/vnc/8c3d.../?token=...",
            }
        }
    }


class LoginSessionResponse(BaseModel):
    session_id: str
    channel_id: str = ""
    status: str = Field(description="raw internal status")
    task_status: str = Field(description="running | login_required | login_succeeded | failed | cancelled | expired | closed")
    message: str = ""
    platform: str = ""
    live_url: str = ""
    login_url: str = ""
    cookie_ready: bool = False
    # 仅 failed 时出现。
    error: ErrorInfo | None = None
    created_at: str = ""
    updated_at: str = ""


class LoginSessionListItem(BaseModel):
    session_id: str
    channel_id: str = ""
    status: str
    platform: str = ""
    created_at: str = ""
    updated_at: str = ""


class LoginSessionListResponse(BaseModel):
    sessions: list[LoginSessionListItem]
    count: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Channels（带 cookie 的已连接账号）
# ---------------------------------------------------------------------------
class ChannelResponse(BaseModel):
    channel_id: str
    platform: str = ""
    status: str = Field(description="pending | bound | invalid")
    account_name: str = ""
    has_valid_cookie: bool = False
    created_at: str = ""
    updated_at: str = ""


class ChannelListItem(BaseModel):
    channel_id: str
    platform: str = ""
    status: str = ""
    account_name: str = ""
    has_valid_cookie: bool = False
    created_at: str = ""
    updated_at: str = ""


class ChannelListResponse(BaseModel):
    channels: list[ChannelListItem]
    count: int
    limit: int
    offset: int


class ChannelDeleteResponse(BaseModel):
    channel_id: str
    deleted: bool


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------
# 登录态失效相关的关键词（与 orchestrator._looks_like_login_required 同源）。
_LOGIN_ERROR_MARKERS = (
    "未登录", "登录", "登陆", "cookie 无效", "cookie无效",
    "cookie 失效", "cookie失效", "login", "auth/page/login",
)


def _classify_error(detail: str) -> str:
    """把自由文本的失败原因粗分类成稳定的错误码，供调用方分支处理。"""
    text = (detail or "").lower()
    # 先判远程登录启动失败（其文案里也含「登录」，须排在登录态判断之前）。
    if "远程登录启动失败" in (detail or "") or "remote login" in text or "remote cookie" in text:
        return "remote_login_failed"
    if any(m.lower() in text for m in _LOGIN_ERROR_MARKERS):
        return "cookie_invalid"
    return "job_failed"


def _channel_has_valid_cookie(channel: Channel) -> bool:
    cookies = (channel.cookie or {}).get("cookies") or []
    if not cookies:
        return False
    config = registry.config_for(channel.platform)
    return bool(config and config.has_login_cookie(cookies))


def job_created_from(resp: AutoTaskCreateResponse, *, job_type: str) -> JobCreatedResponse:
    """Map the agent's AutoTaskCreateResponse into the typed JobCreatedResponse."""
    data = resp.data or {}
    detail = str(data.get("error_detail", "") or "")
    error = ErrorInfo(code=_classify_error(detail), detail=detail) if detail else None
    return JobCreatedResponse(
        job_id=str(data.get("job_id", "") or ""),
        type=job_type,
        channel_id=str(data.get("channel_id", "") or ""),
        task_status=str(data.get("task_status", "") or ""),
        message=resp.message or "",
        live_url=str(data.get("live_url", "") or ""),
        error=error,
    )


def _task_status_for(job: Job) -> tuple[str, str]:
    """Map a publish Job's internal status to (task_status, default message)."""
    status = job.status
    if status in _RUNNING_STATUSES:
        return "running", "发布任务正在后台执行"
    if status == STATUS_WAITING_COOKIE:
        return "login_required", "需要用户登录或 Cookie 已失效"
    if status == STATUS_SUCCEEDED:
        return "published", "文章发布成功" if job.article_url else "文章发布成功，但未获取到文章链接"
    if status == STATUS_CANCELLED:
        return "cancelled", "任务已取消"
    if status == "expired":
        return "expired", "浏览器 session 已过期"
    if status == "closed":
        return "closed", "浏览器 session 已关闭"
    return "failed", "发布失败"


def job_to_response(job: Job) -> JobResponse:
    payload = job.payload or {}
    result = job.result or {}
    task_status, message = _task_status_for(job)

    publish_result = None
    if task_status == "published":
        publish_result = PublishResult(
            success=bool(result.get("success", False)),
            account_name=str(result.get("account_name", "") or result.get("account", "") or ""),
            platform_user_id=str(result.get("user_id", "") or ""),
            article_title=str(result.get("article_title", "") or ""),
            publish_signal=str(result.get("publish_signal", "") or ""),
            operation_time=str(result.get("operation_time", "") or ""),
        )

    error = None
    if task_status == "failed":
        detail = job.error or str(result.get("failure_reason", "") or "") or str(result.get("message", "") or "")
        error = ErrorInfo(code=_classify_error(detail), detail=detail)

    return JobResponse(
        job_id=job.job_id,
        type=job.type,
        status=job.status,
        task_status=task_status,
        message=message,
        channel_id=str(payload.get("channel_id", "") or ""),
        platform=str(payload.get("platform", "") or ""),
        title=str(payload.get("title", "") or ""),
        cover_image_url=str(payload.get("cover_image_url", "") or ""),
        live_url=job.live_url or "",
        login_url=job.login_url or "",
        remote_session_id=job.remote_session_id or "",
        article_url=job.article_url or "",
        publish_result=publish_result,
        error=error,
        created_at=job.created_at or "",
        updated_at=job.updated_at or "",
    )


def job_to_list_item(job: Job) -> JobListItem:
    payload = job.payload or {}
    return JobListItem(
        job_id=job.job_id,
        type=job.type,
        status=job.status,
        channel_id=str(payload.get("channel_id", "") or ""),
        platform=str(payload.get("platform", "") or ""),
        title=str(payload.get("title", "") or ""),
        created_at=job.created_at or "",
        updated_at=job.updated_at or "",
    )


# ---------------------------------------------------------------------------
# Login-session mappers
# ---------------------------------------------------------------------------
def _login_task_status_for(job: Job) -> tuple[str, str]:
    """Map a login session's internal status to (task_status, default message).

    登录会话不走发布，所以这里的文案是登录语义，而非发文语义。"""
    status = job.status
    if status == STATUS_SUCCEEDED:
        return "login_succeeded", "登录 Cookie 保存成功"
    if status == STATUS_WAITING_COOKIE:
        return "login_required", "等待用户在浏览器中完成登录"
    if status in _RUNNING_STATUSES:
        return "running", "正在准备远程登录会话"
    if status == STATUS_CANCELLED:
        return "cancelled", "登录会话已取消"
    if status == "expired":
        return "expired", "浏览器 session 已过期"
    if status == "closed":
        return "closed", "浏览器 session 已关闭"
    return "failed", "登录失败"


def login_session_created_from(resp: AutoTaskCreateResponse) -> LoginSessionCreatedResponse:
    """Map the agent's AutoTaskCreateResponse into the typed LoginSessionCreatedResponse."""
    data = resp.data or {}
    detail = str(data.get("error_detail", "") or "")
    error = ErrorInfo(code=_classify_error(detail), detail=detail) if detail else None
    return LoginSessionCreatedResponse(
        session_id=str(data.get("job_id", "") or ""),
        channel_id=str(data.get("channel_id", "") or ""),
        task_status=str(data.get("task_status", "") or ""),
        message=resp.message or "",
        live_url=str(data.get("live_url", "") or ""),
        error=error,
    )


def login_session_to_response(job: Job) -> LoginSessionResponse:
    payload = job.payload or {}
    result = job.result or {}
    task_status, message = _login_task_status_for(job)

    error = None
    if task_status == "failed":
        detail = job.error or str(result.get("failure_reason", "") or "") or str(result.get("message", "") or "")
        error = ErrorInfo(code=_classify_error(detail), detail=detail)

    return LoginSessionResponse(
        session_id=job.job_id,
        channel_id=str(payload.get("channel_id", "") or ""),
        status=job.status,
        task_status=task_status,
        message=message,
        platform=str(payload.get("platform", "") or ""),
        live_url=job.live_url or "",
        login_url=job.login_url or "",
        cookie_ready=bool(result.get("cookie_ready", False)),
        error=error,
        created_at=job.created_at or "",
        updated_at=job.updated_at or "",
    )


def login_session_to_list_item(job: Job) -> LoginSessionListItem:
    payload = job.payload or {}
    return LoginSessionListItem(
        session_id=job.job_id,
        channel_id=str(payload.get("channel_id", "") or ""),
        status=job.status,
        platform=str(payload.get("platform", "") or ""),
        created_at=job.created_at or "",
        updated_at=job.updated_at or "",
    )


# ---------------------------------------------------------------------------
# Channel mappers
# ---------------------------------------------------------------------------
def channel_to_response(channel: Channel) -> ChannelResponse:
    return ChannelResponse(
        channel_id=channel.channel_id,
        platform=channel.platform,
        status=channel.status,
        account_name=channel.account_name,
        has_valid_cookie=_channel_has_valid_cookie(channel),
        created_at=channel.created_at or "",
        updated_at=channel.updated_at or "",
    )


def channel_to_list_item(channel: Channel) -> ChannelListItem:
    return ChannelListItem(
        channel_id=channel.channel_id,
        platform=channel.platform,
        status=channel.status,
        account_name=channel.account_name,
        has_valid_cookie=_channel_has_valid_cookie(channel),
        created_at=channel.created_at or "",
        updated_at=channel.updated_at or "",
    )
