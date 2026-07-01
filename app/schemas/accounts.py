from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.accounts.models import (
    ACCOUNT_STATUS_DISABLED,
    ACCOUNT_STATUS_MUTED,
    ACCOUNT_STATUS_NORMAL,
    ACCOUNT_STATUS_WARNING,
    ArticleAccount,
    mask_phone,
)
from app.domain.job import Platform

AccountStatus = Literal["normal", "warning", "muted", "disabled"]

ACCOUNT_STATUS_SCHEMA_EXTRA = {
    "enum": [
        ACCOUNT_STATUS_NORMAL,
        ACCOUNT_STATUS_WARNING,
        ACCOUNT_STATUS_MUTED,
        ACCOUNT_STATUS_DISABLED,
    ]
}


class AccountChannelSummary(BaseModel):
    channel_id: str = Field(default="", description="渠道 ID，登录成功后由服务签发，用于后续发文。")
    platform: Platform = Field(description="平台枚举。可选值：toutiao（今日头条/头条号）、sohu（搜狐号）。")
    status: str = Field(default="", description="channel 当前状态，例如 pending、bound、invalid。")
    account_name: str = Field(default="", description="平台账号显示名；取不到时为空字符串。")
    has_valid_cookie: bool = Field(default=False, description="当前 channel 是否有可用 cookie。")


class AccountRuntimeStatus(BaseModel):
    account_status: str = Field(
        default="idle",
        description="运行时发文状态。idle 表示没有未完成发文任务；publishing 表示正在发文、等待登录或队列中。",
        json_schema_extra={"enum": ["idle", "publishing"]},
    )
    publish_count: int = Field(default=0, ge=0, description="该 channel 下未完成 publish job 数量。")
    is_idle: bool = Field(default=True, description="是否空闲；等价于 publish_count == 0。")


class AccountResponse(BaseModel):
    id: int = Field(description="article_accounts.id。")
    platform: Platform = Field(description="平台枚举。可选值：toutiao（今日头条/头条号）、sohu（搜狐号）。")
    phone: str = Field(description="账号手机号，必须是 11 位且以 1 开头；同一 group_id + platform 下唯一。")
    phone_masked: str = Field(description="脱敏手机号，用于展示。")
    channel_id: str = Field(description="绑定的 channel id；对应 article_accounts 表的 channel 字段。")
    status: str = Field(
        description="账号持久状态。normal 正常；warning 警告但仍可用；muted 禁言不可用；disabled 人工禁用不可用。",
        json_schema_extra=ACCOUNT_STATUS_SCHEMA_EXTRA,
    )
    consecutive_failures: int = Field(description="连续发文失败次数。")
    group_id: str = Field(description="调用方传入的账号分组/租户 id。")
    group_text: str = Field(default="", description="分组展示文本，仅用于展示，不参与过滤和唯一性判断。")
    created_at: str = Field(default="", description="账号记录创建时间。")
    updated_at: str = Field(default="", description="账号记录更新时间。")
    channel: AccountChannelSummary | None = Field(default=None, description="channel 摘要；include_channel=false 时为 null。")
    runtime: AccountRuntimeStatus | None = Field(default=None, description="实时发文状态；include_runtime=false 时为 null。")


class AccountListResponse(BaseModel):
    group_id: str = Field(description="本次查询使用的账号分组/租户 id。")
    group_text: str = Field(default="", description="分组展示文本；请求未传时取账号记录中的 group_text 或空字符串。")
    count: int = Field(description="本页返回账号数量。")
    limit: int = Field(description="分页大小。")
    offset: int = Field(description="分页偏移。")
    accounts: list[AccountResponse] = Field(description="账号列表。")


class AccountUpsertRequest(BaseModel):
    group_id: str = Field(
        ...,
        description="必填。调用方传入的账号分组/租户 id；服务端不会从 .env 读取 GROUP_ID。",
        examples=["TianQW"],
    )
    group_text: str = Field(default="", description="可选。分组展示文本，不参与查询过滤和唯一性判断。", examples=["测试组002"])
    channel_id: str = Field(
        description="必填。已存在的 channel id，通常来自登录成功后的 Login Session 响应。",
        examples=["3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c"],
    )
    status: str = Field(
        default=ACCOUNT_STATUS_NORMAL,
        description="账号持久状态。normal 正常；warning 警告但仍可用；muted 禁言不可用；disabled 人工禁用不可用。",
        json_schema_extra=ACCOUNT_STATUS_SCHEMA_EXTRA,
    )
    reset_failures: bool = Field(default=True, description="是否把 consecutive_failures 重置为 0。")
    consecutive_failures: int = Field(default=0, ge=0, description="连续失败次数；reset_failures=false 时写入该值。")

    model_config = ConfigDict(extra="forbid")


class AccountPatchRequest(BaseModel):
    group_id: str = Field(
        ...,
        description="必填。调用方传入的账号分组/租户 id；只会修改该 group_id 下的账号。",
        examples=["TianQW"],
    )
    group_text: str | None = Field(default=None, description="可选。更新分组展示文本；空字符串按未提供处理。")
    new_phone: str | None = Field(default=None, description="可选。新手机号，必须是 11 位且以 1 开头；空字符串按未提供处理。")
    status: str | None = Field(
        default=None,
        description="可选。账号持久状态；空字符串按未提供处理。normal 会默认清零失败次数。",
        json_schema_extra=ACCOUNT_STATUS_SCHEMA_EXTRA,
    )
    reset_failures: bool | None = Field(default=None, description="可选。true 时把 consecutive_failures 置 0。")
    consecutive_failures: int | None = Field(default=None, ge=0, description="可选。显式设置连续失败次数，必须 >= 0。")

    model_config = ConfigDict(extra="forbid")

    @field_validator("group_text", "new_phone", "status", mode="before")
    @classmethod
    def blank_string_to_none(cls, value):
        if isinstance(value, str) and value == "":
            return None
        return value


class AccountDeleteResponse(BaseModel):
    platform: Platform = Field(description="平台枚举。可选值：toutiao（今日头条/头条号）、sohu（搜狐号）。")
    phone: str = Field(description="被删除账号手机号。")
    phone_masked: str = Field(description="被删除账号手机号的脱敏展示。")
    group_id: str = Field(description="本次删除使用的账号分组/租户 id。")
    group_text: str = Field(default="", description="分组展示文本。")
    deleted: bool = Field(description="账号表记录是否已删除。")
    channel_id: str = Field(description="原绑定 channel id。")
    channel_deleted: bool = Field(description="是否已删除关联 channel/cookie。")
    proxy_unassigned: bool = Field(description="是否已解除 proxy assignment。")
    cancelled_jobs: list[str] = Field(description="force=true 时被取消的未完成 publish job id 列表。")
    cleanup_warnings: list[str] = Field(description="清理过程中的非致命告警。")


def account_to_response(
    account: ArticleAccount,
    *,
    channel: AccountChannelSummary | None = None,
    runtime: AccountRuntimeStatus | None = None,
) -> AccountResponse:
    return AccountResponse(
        id=account.id,
        platform=account.platform,
        phone=account.phone,
        phone_masked=mask_phone(account.phone),
        channel_id=account.channel,
        status=account.status,
        consecutive_failures=account.consecutive_failures,
        group_id=account.group_id,
        group_text=account.group_text,
        created_at=account.created_at,
        updated_at=account.updated_at,
        channel=channel,
        runtime=runtime,
    )
