from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.accounts.models import ArticleAccount, mask_phone


class AccountChannelSummary(BaseModel):
    channel_id: str = ""
    platform: str = ""
    status: str = ""
    account_name: str = ""
    has_valid_cookie: bool = False


class AccountRuntimeStatus(BaseModel):
    account_status: str = "idle"
    publish_count: int = Field(default=0, ge=0)
    is_idle: bool = True


class AccountResponse(BaseModel):
    id: int
    platform: str
    phone: str
    phone_masked: str
    channel_id: str
    status: str
    consecutive_failures: int
    group_id: str
    group_text: str = ""
    created_at: str = ""
    updated_at: str = ""
    channel: AccountChannelSummary | None = None
    runtime: AccountRuntimeStatus | None = None


class AccountListResponse(BaseModel):
    group_id: str
    group_text: str = ""
    count: int
    limit: int
    offset: int
    accounts: list[AccountResponse]


class AccountUpsertRequest(BaseModel):
    group_id: str = ""
    group_text: str = ""
    channel_id: str
    status: str = "normal"
    reset_failures: bool = True
    consecutive_failures: int = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid")


class AccountPatchRequest(BaseModel):
    group_id: str = ""
    group_text: str | None = None
    new_phone: str | None = None
    status: str | None = None
    reset_failures: bool | None = None
    consecutive_failures: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")

    @field_validator("group_text", "new_phone", "status", mode="before")
    @classmethod
    def blank_string_to_none(cls, value):
        if isinstance(value, str) and value == "":
            return None
        return value


class AccountDeleteResponse(BaseModel):
    platform: str
    phone: str
    phone_masked: str
    group_id: str
    group_text: str = ""
    deleted: bool
    channel_id: str
    channel_deleted: bool
    proxy_unassigned: bool
    cancelled_jobs: list[str]
    cleanup_warnings: list[str]


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
