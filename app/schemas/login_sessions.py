"""登录会话（login-sessions）的 HTTP 请求/响应 schema 与 mapper。

登录由真人在流式浏览器里手动操作，与发文任务（jobs）刻意分开建模：它仍是有状态
的异步会话（占用稀缺的 Xvnc 显示槽），故同样需要查询状态与取消，但响应里不带任何
发文相关字段（标题/正文/文章链接/发布结果）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.job import (
    AutoTaskCreateResponse,
    LoginRequest as CreateLoginJobRequest,
    STATUS_CANCELLED,
    STATUS_SUCCEEDED,
    STATUS_WAITING_COOKIE,
    Job,
)
from app.schemas.common import ErrorInfo, _RUNNING_STATUSES, classify_error

__all__ = [
    "CreateLoginJobRequest",
    "LoginSessionCreatedResponse",
    "LoginSessionResponse",
    "login_task_status_for",
    "login_session_created_from",
    "login_session_to_response",
]


# ---------------------------------------------------------------------------
# 创建登录会话
# ---------------------------------------------------------------------------
class LoginSessionCreatedResponse(BaseModel):
    session_id: str = Field(description="登录会话 ID")
    # 本次登录绑定/将绑定到的渠道句柄；登录成功后用它发文。
    channel_id: str = Field(default="", description="登录绑定的渠道句柄")
    status: str = Field(description="登录会话状态（原始内部状态，如 waiting_cookie/failed）")
    # 让用户去这个流式浏览器地址完成登录。
    live_url: str = Field(default="", description="远程登录流式浏览器地址")
    # 仅 failed 时出现。
    error: ErrorInfo | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "session_id": "9c833a78-4f42-4c98-aef6-cc7d0d06a7f8",
                    "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
                    "status": "waiting_cookie",
                    "live_url": "https://example.com/vnc/8c3d.../?token=...",
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# 查询登录会话
# ---------------------------------------------------------------------------
class LoginSessionResponse(BaseModel):
    session_id: str = Field(description="登录会话 ID")
    channel_id: str = Field(default="", description="登录绑定的渠道句柄")
    status: str = Field(
        description="登录会话状态（原始内部状态，如 waiting_cookie/succeeded/failed/cancelled）"
    )
    platform: str = Field(default="", description="平台标识")
    live_url: str = Field(default="", description="远程登录流式浏览器地址")
    # 仅 failed 时出现。
    error: ErrorInfo | None = None
    created_at: str = Field(default="", description="创建时间")
    updated_at: str = Field(default="", description="更新时间")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "session_id": "9c833a78-4f42-4c98-aef6-cc7d0d06a7f8",
                    "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
                    "status": "waiting_cookie",
                    "platform": "toutiao",
                    "live_url": "https://example.com/vnc/8c3d.../?token=...",
                    "created_at": "2026-06-15T08:00:00+00:00",
                    "updated_at": "2026-06-15T08:00:30+00:00",
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------
def login_task_status_for(job: Job) -> str:
    """Map a login session's internal status to the external task_status.

    登录会话不走发布，所以语义是登录态，而非发文态。"""
    status = job.status
    if status == STATUS_SUCCEEDED:
        return "login_succeeded"
    if status == STATUS_WAITING_COOKIE:
        return "login_required"
    if status in _RUNNING_STATUSES:
        return "running"
    if status == STATUS_CANCELLED:
        return "cancelled"
    return "failed"


def login_session_created_from(resp: AutoTaskCreateResponse) -> LoginSessionCreatedResponse:
    """Map the agent's AutoTaskCreateResponse into the typed LoginSessionCreatedResponse."""
    data = resp.data or {}
    detail = str(data.get("error_detail", "") or "")
    error = ErrorInfo(code=classify_error(detail), detail=detail) if detail else None
    return LoginSessionCreatedResponse(
        session_id=str(data.get("job_id", "") or ""),
        channel_id=str(data.get("channel_id", "") or ""),
        status=str(data.get("status", "") or ""),
        live_url=str(data.get("live_url", "") or ""),
        error=error,
    )


def login_session_to_response(job: Job) -> LoginSessionResponse:
    payload = job.payload or {}
    result = job.result or {}
    # status 不再下发派生态，但仍用 login_task_status_for 判定是否为失败、决定填 error。
    task_status = login_task_status_for(job)

    error = None
    if task_status == "failed":
        detail = job.error or str(result.get("failure_reason", "") or "") or str(result.get("message", "") or "")
        error = ErrorInfo(code=classify_error(detail), detail=detail)

    return LoginSessionResponse(
        session_id=job.job_id,
        channel_id=str(payload.get("channel_id", "") or ""),
        status=job.status,
        platform=str(payload.get("platform", "") or ""),
        live_url=job.live_url or "",
        error=error,
        created_at=job.created_at or "",
        updated_at=job.updated_at or "",
    )
