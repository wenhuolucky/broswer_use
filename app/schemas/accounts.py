from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.accounts.models import ACCOUNT_STATUSES, STATUS_NORMAL, validate_phone
from app.domain.job import Platform
from app.schemas.channels import ChannelResponse
from app.schemas.common import ErrorInfo
from app.schemas.jobs import JobCreatedResponse, JobResponse
from app.schemas.login_sessions import LoginSessionResponse


class AccountErrorDetail(BaseModel):
    code: str
    message: str
    extra: dict = Field(default_factory=dict)


class AccountJobSummary(BaseModel):
    job_id: str
    status: str
    title: str = ""
    live_url: str = ""
    created_at: str = ""
    updated_at: str = ""


class AccountPublishRuntime(BaseModel):
    channel_id: str = ""
    account_status: str
    publish_status: str
    publish_count: int = Field(ge=0)
    is_idle: bool
    running_job: AccountJobSummary | None = None
    queued_count: int = Field(ge=0)
    queued_jobs: list[AccountJobSummary] = Field(default_factory=list)


class AccountResponse(BaseModel):
    id: int | str
    platform: str
    phone: str
    phone_masked: str
    status: str
    consecutive_failures: int
    group_id: str
    group_text: str
    channel_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    channel: ChannelResponse | None = None
    runtime: AccountPublishRuntime | None = None
    availability: str
    unavailable_reasons: list[str] = Field(default_factory=list)


class AccountListResponse(BaseModel):
    group_id: str
    group_text: str
    count: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    accounts: list[AccountResponse]


class CreateAccountLoginSessionRequest(BaseModel):
    platform: Platform = "toutiao"
    phone: str

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, value: str) -> str:
        return validate_phone(value)


class AccountLoginSessionCreatedResponse(BaseModel):
    session_id: str
    channel_id: str = ""
    status: str
    live_url: str = ""
    error: ErrorInfo | None = None
    platform: str
    phone: str
    phone_masked: str
    group_id: str
    group_text: str


class AccountLoginSessionResponse(LoginSessionResponse):
    phone: str = ""
    phone_masked: str = ""
    group_id: str = ""
    group_text: str = ""
    account_saved: bool = False
    account: AccountResponse | None = None


class UpdateAccountStatusRequest(BaseModel):
    status: str
    reset_failures: bool | None = None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        if value not in ACCOUNT_STATUSES:
            raise ValueError("invalid account status")
        return value


class RenameAccountPhoneRequest(BaseModel):
    new_phone: str

    @field_validator("new_phone")
    @classmethod
    def _validate_new_phone(cls, value: str) -> str:
        return validate_phone(value)


class AccountPublishStatusResponse(BaseModel):
    platform: str
    phone: str
    phone_masked: str
    status: str
    channel_id: str | None = None
    channel: ChannelResponse | None = None
    channel_id_source: str = "account.channel"
    account_status: str
    publish_status: str
    publish_count: int = Field(ge=0)
    is_idle: bool
    running_job: AccountJobSummary | None = None
    queued_count: int = Field(ge=0)
    queued_jobs: list[AccountJobSummary] = Field(default_factory=list)


class CreateAccountPublishRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    cover_image_url: str | None = None
    allow_warning: bool = False


class AccountPublishCreatedResponse(JobCreatedResponse):
    platform: str
    phone: str
    phone_masked: str
    group_id: str
    group_text: str


class AccountJobStatusUpdateResponse(BaseModel):
    applied: bool
    reason: str
    previous_status: str
    new_status: str
    previous_consecutive_failures: int
    new_consecutive_failures: int


class AccountJobResponse(BaseModel):
    job: JobResponse
    account: AccountResponse | None = None
    account_update: AccountJobStatusUpdateResponse | None = None


class AccountJobCancelResponse(BaseModel):
    job: JobResponse
    account: dict[str, str]


class AccountDeleteResponse(BaseModel):
    platform: str
    phone: str
    phone_masked: str
    group_id: str
    group_text: str
    deleted: bool
    channel_id: str
    channel_deleted: bool
    proxy_unassigned: bool
    cancelled_jobs: list[str] = Field(default_factory=list)
    cleanup_warnings: list[str] = Field(default_factory=list)

    model_config = ConfigDict(json_schema_extra={"examples": [{"deleted": True}]})
