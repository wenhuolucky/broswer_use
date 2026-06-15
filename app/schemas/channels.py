"""渠道（channels）的 HTTP 响应 schema 与 mapper。

渠道 = 已连接的平台账号：channel_id ↔ 平台 ↔ 平台账号 ↔ cookie。无创建请求体
（渠道由登录流程签发），故这里只有响应模型。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.channel import Channel
from app.platforms import registry

__all__ = [
    "ChannelResponse",
    "ChannelDeleteResponse",
    "channel_has_valid_cookie",
    "channel_to_response",
]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ChannelResponse(BaseModel):
    channel_id: str = Field(description="渠道句柄")
    platform: str = Field(default="", description="平台标识")
    status: str = Field(description="pending | bound | invalid")
    account_name: str = Field(default="", description="平台账号名")
    has_valid_cookie: bool = Field(default=False, description="cookie 是否仍有效")
    created_at: str = Field(default="", description="创建时间")
    updated_at: str = Field(default="", description="更新时间")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
                    "platform": "toutiao",
                    "status": "bound",
                    "account_name": "账号名称",
                    "has_valid_cookie": True,
                    "created_at": "2026-06-15T08:00:00+00:00",
                    "updated_at": "2026-06-15T08:01:00+00:00",
                }
            ]
        }
    )


class ChannelDeleteResponse(BaseModel):
    channel_id: str = Field(description="渠道句柄")
    deleted: bool = Field(description="是否已删除")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c", "deleted": True}]
        }
    )


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------
def channel_has_valid_cookie(channel: Channel) -> bool:
    cookies = (channel.cookie or {}).get("cookies") or []
    if not cookies:
        return False
    config = registry.config_for(channel.platform)
    return bool(config and config.has_login_cookie(cookies))


def channel_to_response(channel: Channel) -> ChannelResponse:
    return ChannelResponse(
        channel_id=channel.channel_id,
        platform=channel.platform,
        status=channel.status,
        account_name=channel.account_name,
        has_valid_cookie=channel_has_valid_cookie(channel),
        created_at=channel.created_at or "",
        updated_at=channel.updated_at or "",
    )
