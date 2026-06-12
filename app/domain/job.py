from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.channel import validate_channel_id


# Platforms this service can publish to / log in. Constrained at the request
# boundary so an unknown platform is rejected with a clean 422.
Platform = Literal["toutiao", "sohu"]


STATUS_QUEUED = "queued"
STATUS_CHECKING_COOKIE = "checking_cookie"
STATUS_COOKIE_READY = "cookie_ready"
STATUS_COOKIE_MISSING = "cookie_missing"
STATUS_STARTING_REMOTE_LOGIN = "starting_remote_login"
STATUS_WAITING_COOKIE = "waiting_cookie"
STATUS_PUBLISHING = "publishing"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


class AutoPublishRequest(BaseModel):
    # 发文只认 channel_id：平台由该渠道解析，不再单独传 platform/user_id。
    channel_id: str = Field(..., min_length=1, description="渠道 ID（登录时由服务签发）")
    title: str = Field(..., min_length=1, max_length=200, description="文章标题")
    content: str = Field(..., min_length=1, description="文章正文")
    cover_image_url: str | None = Field(default=None, description="封面图片 URL")

    model_config = {
        "json_schema_extra": {
            "example": {
                "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
                "title": "测试标题",
                "content": "文章正文内容",
                "cover_image_url": "https://example.com/cover.jpg",
            }
        }
    }

    @field_validator("channel_id")
    @classmethod
    def _check_channel_id(cls, v: str) -> str:
        return validate_channel_id(v)


class LoginRequest(BaseModel):
    # 登录只需平台：服务据此签发一个新的 channel_id。
    platform: Platform = Field(default="toutiao", description="登录平台")

    model_config = {
        "json_schema_extra": {"example": {"platform": "toutiao"}}
    }


class AutoPublishResponse(BaseModel):
    code: int = 202
    job_id: str
    status: str
    message: str = ""
    live_url: str = ""
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
    # Derived/queryable convenience fields (kept optional so Job(**data) over
    # legacy/serialized payloads keeps working).
    type: str = "publish"  # 'publish' | 'login'
    article_url: str = ""

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC).isoformat()
