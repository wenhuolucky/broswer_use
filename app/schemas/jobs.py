"""发文任务（jobs）的 HTTP 请求/响应 schema 与 mapper。

发文由 LLM 驱动浏览器执行，长时间运行，故需轮询状态、取消。请求模型复用
``app.domain.job`` 的命令对象（PublishAgent 直接消费），这里只 re-export 改名。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.job import (
    AutoPublishRequest as CreatePublishJobRequest,
    AutoTaskCreateResponse,
    STATUS_CANCELLED,
    STATUS_SUCCEEDED,
    STATUS_WAITING_COOKIE,
    Job,
)
from app.schemas.common import ErrorInfo, _RUNNING_STATUSES, classify_error

__all__ = [
    "CreatePublishJobRequest",
    "JobCreatedResponse",
    "JobResponse",
    "job_created_from",
    "task_status_for",
    "job_to_response",
]


# ---------------------------------------------------------------------------
# 创建发文任务
# ---------------------------------------------------------------------------
class JobCreatedResponse(BaseModel):
    job_id: str = Field(description="发文任务 ID")
    channel_id: str = Field(default="", description="发文渠道句柄")
    status: str = Field(
        description=(
            "发文任务状态。常见值：queued、checking_cookie、starting_remote_login、"
            "waiting_cookie、publishing、succeeded、failed、cancelled。"
        )
    )
    # waiting_cookie 时用于登录；publishing 时可能用于实时查看发文过程。
    live_url: str = Field(
        default="",
        description=(
            "状态相关的远程浏览器地址：waiting_cookie 时为登录地址，publishing 时可能为发文实时查看地址，"
            "其余状态通常为空。"
        ),
    )
    # 仅 failed 时出现。
    error: ErrorInfo | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "job_id": "9c833a78-4f42-4c98-aef6-cc7d0d06a7f8",
                    "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
                    "status": "waiting_cookie",
                    "live_url": "https://example.com/vnc/8c3d.../?token=...",
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# 查询发文任务
# ---------------------------------------------------------------------------
class JobResponse(BaseModel):
    job_id: str = Field(description="发文任务 ID")
    status: str = Field(
        description=(
            "发文任务状态。常见值：queued、checking_cookie、starting_remote_login、"
            "waiting_cookie、publishing、succeeded、failed、cancelled。"
        )
    )
    channel_id: str = Field(default="", description="发文渠道句柄")
    platform: str = Field(default="", description="平台枚举值：toutiao、sohu；取不到时为空字符串。")
    title: str = Field(default="", description="文章标题")
    cover_image_url: str = Field(default="", description="封面图片 URL")
    live_url: str = Field(
        default="",
        description=(
            "状态相关的远程浏览器地址：waiting_cookie 时为登录地址，publishing 时可能为发文实时查看地址，"
            "终态通常为空。"
        ),
    )
    session_id: str = Field(default="", description="远程登录会话 ID（内部句柄，第三方集成可忽略）")
    article_url: str = Field(default="", description="发布成功后的文章链接")
    # 仅 failed 时出现；统一失败建模，不再有 reason/error 两个并存的字符串字段。
    error: ErrorInfo | None = None
    created_at: str = Field(default="", description="创建时间")
    updated_at: str = Field(default="", description="更新时间（成功时即发布完成时刻）")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "job_id": "9c833a78-4f42-4c98-aef6-cc7d0d06a7f8",
                    "status": "succeeded",
                    "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
                    "platform": "toutiao",
                    "title": "测试标题",
                    "article_url": "https://www.toutiao.com/article/123456/",
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------
def job_created_from(resp: AutoTaskCreateResponse) -> JobCreatedResponse:
    """Map the agent's AutoTaskCreateResponse into the typed JobCreatedResponse."""
    data = resp.data or {}
    detail = str(data.get("error_detail", "") or "")
    error = ErrorInfo(code=classify_error(detail), detail=detail) if detail else None
    return JobCreatedResponse(
        job_id=str(data.get("job_id", "") or ""),
        channel_id=str(data.get("channel_id", "") or ""),
        status=str(data.get("status", "") or ""),
        live_url=str(data.get("live_url", "") or ""),
        error=error,
    )


def task_status_for(job: Job) -> str:
    """Map a publish Job's internal status to the external task_status."""
    status = job.status
    if status in _RUNNING_STATUSES:
        return "running"
    if status == STATUS_WAITING_COOKIE:
        return "login_required"
    if status == STATUS_SUCCEEDED:
        return "published"
    if status == STATUS_CANCELLED:
        return "cancelled"
    return "failed"


def job_to_response(job: Job) -> JobResponse:
    payload = job.payload or {}
    result = job.result or {}
    # task_status 不再下发，但仍用它来判定何时填充 error。
    task_status = task_status_for(job)

    error = None
    if task_status == "failed":
        detail = job.error or str(result.get("failure_reason", "") or "") or str(result.get("message", "") or "")
        error = ErrorInfo(code=classify_error(detail), detail=detail)

    return JobResponse(
        job_id=job.job_id,
        status=job.status,
        channel_id=str(payload.get("channel_id", "") or ""),
        platform=str(payload.get("platform", "") or ""),
        title=str(payload.get("title", "") or ""),
        cover_image_url=str(payload.get("cover_image_url", "") or ""),
        live_url=job.live_url or "",
        session_id=job.remote_session_id or "",
        article_url=job.article_url or "",
        error=error,
        created_at=job.created_at or "",
        updated_at=job.updated_at or "",
    )
