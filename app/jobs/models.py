from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


STATUS_QUEUED = "queued"
STATUS_CHECKING_COOKIE = "checking_cookie"
STATUS_COOKIE_READY = "cookie_ready"
STATUS_COOKIE_MISSING = "cookie_missing"
STATUS_STARTING_REMOTE_LOGIN = "starting_remote_login"
STATUS_WAITING_COOKIE = "waiting_cookie"
STATUS_PUBLISHING = "publishing"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


class AutoPublishRequest(BaseModel):
    user_id: str = Field(..., min_length=1, description="用户 ID，用于隔离 Cookie")
    platform: str = Field(default="toutiao", min_length=1, description="发布平台")
    title: str = Field(..., min_length=1, max_length=200, description="文章标题")
    content: str = Field(..., min_length=1, description="文章正文")
    cover_image_url: str | None = Field(default=None, description="封面图片 URL")


class LoginRequest(BaseModel):
    user_id: str = Field(..., min_length=1, description="User ID for cookie isolation")
    platform: str = Field(default="toutiao", min_length=1, description="Login platform")


class AutoPublishResponse(BaseModel):
    code: int = 202
    job_id: str
    status: str
    message: str = ""
    login_url: str = ""
    log_file_path: str = ""
    result: dict[str, Any] = Field(default_factory=dict)


class AutoTaskCreateResponse(BaseModel):
    code: int = 200
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


@dataclass
class Job:
    job_id: str
    status: str
    payload: dict[str, Any]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    login_url: str = ""
    remote_session_id: str = ""
    live_url: str = ""
    log_file_path: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC).isoformat()
