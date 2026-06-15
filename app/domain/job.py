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


_EXAMPLE_TITLE = "把养生过成日常：真正有效的健康，从不需要太用力"
_EXAMPLE_CONTENT = (
    "很多人一提到养生，就会想到复杂的食谱、昂贵的补品、严格的作息表。可真正能长期坚持的养生，"
    "往往不是把生活改得面目全非，而是在每天的小事里，慢慢把身体照顾好。\n\n"
    "养生的第一步，是睡好觉。睡眠不是浪费时间，而是身体修复、情绪恢复和精力补充的过程。"
    "与其熬夜后再用咖啡硬撑，不如尽量保持规律作息，让身体有稳定的节奏。晚上少刷一会儿手机，"
    "睡前让房间安静一点，第二天的状态往往就会轻松许多。\n\n"
    "饮食上，养生不等于清淡到没有味道，也不是一味忌口。更现实的做法，是少吃过度油腻和高糖食物，"
    "多吃新鲜蔬菜、优质蛋白和粗粮。每顿饭吃到七八分饱，给肠胃留一点余地，比偶尔大补更有意义。\n\n"
    "运动也是养生里最朴素的一件事。它不一定非要去健身房，也不一定要追求高强度。每天散步、拉伸、"
    "爬楼梯、做几组简单的力量训练，都能让身体慢慢变得轻盈。关键不是一次练得多狠，而是让身体经常动起来。\n\n"
    "情绪同样需要被照顾。长期焦虑、压抑和紧绷，会悄悄消耗人的气血与精神。学会给自己留一点独处时间，"
    "减少无意义的比较，遇到烦心事时及时倾诉或整理，都是对身体的保护。心安下来，身体也会跟着舒服很多。\n\n"
    "真正的养生，不是追求立刻见效，而是把健康当成一种长期关系去经营。早睡一点，少吃一点，常走一点，"
    "想开一点。这些看似普通的习惯，日积月累，就会成为身体最可靠的底气。\n\n"
    "愿我们都能在忙碌的生活里，慢慢学会照顾自己。养生不是为了活得紧张，而是为了活得更舒展、更清醒，也更自在。"
)
_EXAMPLE_COVER = "https://darizi.com.my/media/article/images/b58ce_a6cb2a1bdff0bf11110c473752895aae2a90ec47.png"


class AutoPublishRequest(BaseModel):
    # 发文只认 channel_id：平台由该渠道解析，不再单独传 platform/user_id。
    channel_id: str = Field(
        ..., min_length=1, description="渠道 ID（登录时由服务签发）",
        examples=["3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c"],
    )
    title: str = Field(..., min_length=1, max_length=200, description="文章标题", examples=[_EXAMPLE_TITLE])
    content: str = Field(..., min_length=1, description="文章正文", examples=[_EXAMPLE_CONTENT])
    cover_image_url: str | None = Field(
        default=None, description="封面图片 URL", examples=[_EXAMPLE_COVER]
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
                    "title": _EXAMPLE_TITLE,
                    "content": _EXAMPLE_CONTENT,
                    "cover_image_url": _EXAMPLE_COVER,
                }
            ]
        }
    }

    @field_validator("channel_id")
    @classmethod
    def _check_channel_id(cls, v: str) -> str:
        return validate_channel_id(v)


class LoginRequest(BaseModel):
    # 登录只需平台：服务据此签发一个新的 channel_id。
    platform: Platform = Field(default="toutiao", description="登录平台", examples=["toutiao"])

    model_config = {
        "json_schema_extra": {"examples": [{"platform": "toutiao"}]}
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
