"""Data models for the publish_docker wrapper service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


JobStatus = Literal["queued", "running", "succeeded", "failed"]


class PublishJob(BaseModel):
    job_id: str
    status: JobStatus = "queued"
    title: str
    content: str
    cookie_text: str
    cover_image_path: str | None = None
    cover_image_url: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


# ---- 双层嵌套响应模型（Swagger 文档可见） ----

class ResponseContext(BaseModel):
    """外层上下文：唯一标识、状态码、失败原因。"""
    id: str = Field(..., description="任务唯一ID")
    code: int = Field(..., description="状态码：200=成功, 202=进行中, 500=失败")
    reason: str = Field(..., description="失败原因（成功时为空字符串）")


class ResponseData(BaseModel):
    """内层数据：发布文章的账号、URL、标题。"""
    user: str = Field(..., description="发布账号名称")
    url: str = Field(..., description="发布文章的完整URL")
    title: str = Field(..., description="文章标题")


class JobDetailResponse(BaseModel):
    """GET /jobs/{job_id} 的响应格式。"""
    context: ResponseContext
    data: ResponseData
