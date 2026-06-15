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
    "PublishResult",
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
    type: str = Field(default="publish", description="资源类型，恒为 publish")
    channel_id: str = Field(default="", description="发文渠道句柄")
    status: str = Field(description="任务状态（原始内部状态，如 publishing/waiting_cookie/failed）")
    # 仅 waiting_cookie 时有意义：让用户去这个流式浏览器地址完成登录。
    live_url: str = Field(default="", description="远程登录流式浏览器地址（仅 waiting_cookie 时有值）")
    # 仅 failed 时出现。
    error: ErrorInfo | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "job_id": "9c833a78-4f42-4c98-aef6-cc7d0d06a7f8",
                    "type": "publish",
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
class PublishResult(BaseModel):
    success: bool = Field(default=False, description="是否发布成功")
    account_name: str = Field(default="", description="发布账号名")
    platform_user_id: str = Field(default="", description="平台用户 ID")
    article_title: str = Field(default="", description="文章标题")
    publish_signal: str = Field(default="", description="发布成功信号文案")
    operation_time: str = Field(default="", description="操作时间")


class JobResponse(BaseModel):
    job_id: str = Field(description="发文任务 ID")
    type: str = Field(default="publish", description="资源类型，恒为 publish")
    status: str = Field(description="任务状态（原始内部状态，如 queued/publishing/waiting_cookie/succeeded/failed/cancelled）")
    channel_id: str = Field(default="", description="发文渠道句柄")
    platform: str = Field(default="", description="平台标识")
    title: str = Field(default="", description="文章标题")
    cover_image_url: str = Field(default="", description="封面图片 URL")
    live_url: str = Field(default="", description="远程登录流式浏览器地址（login_required 时打开它完成登录；其余状态可用于服务端实时调试查看）")
    session_id: str = Field(default="", description="远程登录会话 ID（内部句柄，第三方集成可忽略）")
    article_url: str = Field(default="", description="发布成功后的文章链接")
    publish_result: PublishResult | None = None
    # 仅 failed 时出现；统一失败建模，不再有 reason/error 两个并存的字符串字段。
    error: ErrorInfo | None = None
    created_at: str = Field(default="", description="创建时间")
    updated_at: str = Field(default="", description="更新时间")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "job_id": "9c833a78-4f42-4c98-aef6-cc7d0d06a7f8",
                    "type": "publish",
                    "status": "succeeded",
                    "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
                    "platform": "toutiao",
                    "title": "测试标题",
                    "article_url": "https://www.toutiao.com/article/123456/",
                    "publish_result": {"success": True, "account_name": "账号名称"},
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------
def job_created_from(resp: AutoTaskCreateResponse, *, job_type: str) -> JobCreatedResponse:
    """Map the agent's AutoTaskCreateResponse into the typed JobCreatedResponse."""
    data = resp.data or {}
    detail = str(data.get("error_detail", "") or "")
    error = ErrorInfo(code=classify_error(detail), detail=detail) if detail else None
    return JobCreatedResponse(
        job_id=str(data.get("job_id", "") or ""),
        type=job_type,
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
    # task_status 不再下发，但仍用它来判定何时填充 publish_result / error。
    task_status = task_status_for(job)

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
        error = ErrorInfo(code=classify_error(detail), detail=detail)

    return JobResponse(
        job_id=job.job_id,
        type=job.type,
        status=job.status,
        channel_id=str(payload.get("channel_id", "") or ""),
        platform=str(payload.get("platform", "") or ""),
        title=str(payload.get("title", "") or ""),
        cover_image_url=str(payload.get("cover_image_url", "") or ""),
        live_url=job.live_url or "",
        session_id=job.remote_session_id or "",
        article_url=job.article_url or "",
        publish_result=publish_result,
        error=error,
        created_at=job.created_at or "",
        updated_at=job.updated_at or "",
    )
