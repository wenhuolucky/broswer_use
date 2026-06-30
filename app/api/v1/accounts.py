from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query

from app.accounts.service import AccountService, AccountServiceError
from app.accounts.store import AccountStore
from app.api.deps import agent
from app.core.config import GROUP_ID, GROUP_TEXT
from app.schemas.accounts import (
    AccountDeleteResponse,
    AccountErrorDetail,
    AccountJobCancelResponse,
    AccountJobResponse,
    AccountListResponse,
    AccountLoginSessionCreatedResponse,
    AccountLoginSessionResponse,
    AccountPublishCreatedResponse,
    AccountPublishStatusResponse,
    AccountResponse,
    CreateAccountLoginSessionRequest,
    CreateAccountPublishRequest,
    RenameAccountPhoneRequest,
    UpdateAccountStatusRequest,
)

router = APIRouter(prefix="/accounts", tags=["accounts"])


@lru_cache(maxsize=1)
def _default_account_service() -> AccountService:
    return AccountService(
        store=AccountStore(),
        agent=agent,
        group_id=GROUP_ID,
        group_text=GROUP_TEXT or GROUP_ID,
    )


def get_account_service() -> AccountService:
    return _default_account_service()


def _raise_account_error(exc: AccountServiceError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail=AccountErrorDetail(code=exc.code, message=exc.message, extra=exc.extra).model_dump(),
    ) from exc


@router.get("", response_model=AccountListResponse)
def list_accounts(
    platform: str | None = None,
    include_runtime: bool = True,
    availability: str = "all",
    show_channel: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: AccountService = Depends(get_account_service),
):
    try:
        return service.list_accounts(
            platform=platform,
            include_runtime=include_runtime,
            availability=availability,
            show_channel=show_channel,
            limit=limit,
            offset=offset,
        )
    except AccountServiceError as exc:
        _raise_account_error(exc)


@router.post("/login-sessions", response_model=AccountLoginSessionCreatedResponse, status_code=202)
async def create_account_login_session(
    request: CreateAccountLoginSessionRequest,
    service: AccountService = Depends(get_account_service),
):
    try:
        return await service.create_login_session(platform=request.platform, phone=request.phone)
    except AccountServiceError as exc:
        _raise_account_error(exc)


@router.get("/login-sessions/{session_id}", response_model=AccountLoginSessionResponse)
def get_account_login_session(
    session_id: str,
    service: AccountService = Depends(get_account_service),
):
    try:
        return service.get_login_session(session_id)
    except AccountServiceError as exc:
        _raise_account_error(exc)


@router.delete("/login-sessions/{session_id}", response_model=AccountLoginSessionResponse)
async def cancel_account_login_session(
    session_id: str,
    service: AccountService = Depends(get_account_service),
):
    try:
        return await service.cancel_login_session(session_id)
    except AccountServiceError as exc:
        _raise_account_error(exc)


@router.patch("/{platform}/{phone}/status", response_model=AccountResponse)
def update_account_status(
    platform: str,
    phone: str,
    request: UpdateAccountStatusRequest,
    service: AccountService = Depends(get_account_service),
):
    try:
        return service.update_status(platform, phone, status=request.status, reset_failures=request.reset_failures)
    except (AccountServiceError, ValueError) as exc:
        if isinstance(exc, ValueError):
            _raise_account_error(AccountServiceError("invalid_phone", str(exc), 400))
        _raise_account_error(exc)


@router.post("/{platform}/{phone}/disable", response_model=AccountResponse)
def disable_account(
    platform: str,
    phone: str,
    service: AccountService = Depends(get_account_service),
):
    try:
        return service.update_status(platform, phone, status="disabled", reset_failures=False)
    except (AccountServiceError, ValueError) as exc:
        if isinstance(exc, ValueError):
            _raise_account_error(AccountServiceError("invalid_phone", str(exc), 400))
        _raise_account_error(exc)


@router.post("/{platform}/{phone}/enable", response_model=AccountResponse)
def enable_account(
    platform: str,
    phone: str,
    service: AccountService = Depends(get_account_service),
):
    try:
        return service.update_status(platform, phone, status="normal", reset_failures=True)
    except (AccountServiceError, ValueError) as exc:
        if isinstance(exc, ValueError):
            _raise_account_error(AccountServiceError("invalid_phone", str(exc), 400))
        _raise_account_error(exc)


@router.patch("/{platform}/{phone}/phone", response_model=AccountResponse)
def rename_account_phone(
    platform: str,
    phone: str,
    request: RenameAccountPhoneRequest,
    service: AccountService = Depends(get_account_service),
):
    try:
        return service.rename_phone(platform, phone, new_phone=request.new_phone)
    except (AccountServiceError, ValueError) as exc:
        if isinstance(exc, ValueError):
            _raise_account_error(AccountServiceError("invalid_phone", str(exc), 400))
        _raise_account_error(exc)


@router.get("/{platform}/{phone}/publish-status", response_model=AccountPublishStatusResponse)
def get_account_publish_status(
    platform: str,
    phone: str,
    show_channel: bool = False,
    service: AccountService = Depends(get_account_service),
):
    try:
        return service.publish_status(platform, phone, show_channel=show_channel)
    except (AccountServiceError, ValueError) as exc:
        if isinstance(exc, ValueError):
            _raise_account_error(AccountServiceError("invalid_phone", str(exc), 400))
        _raise_account_error(exc)


@router.post("/{platform}/{phone}/publish", response_model=AccountPublishCreatedResponse, status_code=202)
async def publish_with_account(
    platform: str,
    phone: str,
    request: CreateAccountPublishRequest,
    service: AccountService = Depends(get_account_service),
):
    try:
        return await service.publish(
            platform,
            phone,
            title=request.title,
            content=request.content,
            cover_image_url=request.cover_image_url,
            allow_warning=request.allow_warning,
        )
    except (AccountServiceError, ValueError) as exc:
        if isinstance(exc, ValueError):
            _raise_account_error(AccountServiceError("invalid_phone", str(exc), 400))
        _raise_account_error(exc)


@router.get("/jobs/{job_id}", response_model=AccountJobResponse)
def get_account_job(
    job_id: str,
    service: AccountService = Depends(get_account_service),
):
    try:
        return service.get_job(job_id)
    except AccountServiceError as exc:
        _raise_account_error(exc)


@router.post("/jobs/{job_id}/cancel", response_model=AccountJobCancelResponse)
async def cancel_account_job(
    job_id: str,
    service: AccountService = Depends(get_account_service),
):
    try:
        return await service.cancel_job(job_id)
    except AccountServiceError as exc:
        _raise_account_error(exc)


@router.delete("/jobs/{job_id}", response_model=AccountJobCancelResponse)
async def delete_account_job(
    job_id: str,
    service: AccountService = Depends(get_account_service),
):
    return await cancel_account_job(job_id, service)


@router.get("/{platform}/{phone}", response_model=AccountResponse)
def get_account(
    platform: str,
    phone: str,
    include_runtime: bool = True,
    show_channel: bool = False,
    service: AccountService = Depends(get_account_service),
):
    try:
        return service.get_account_response(
            platform,
            phone,
            include_runtime=include_runtime,
            show_channel=show_channel,
        )
    except (AccountServiceError, ValueError) as exc:
        if isinstance(exc, ValueError):
            _raise_account_error(AccountServiceError("invalid_phone", str(exc), 400))
        _raise_account_error(exc)


@router.delete("/{platform}/{phone}", response_model=AccountDeleteResponse)
async def delete_account(
    platform: str,
    phone: str,
    force: bool = False,
    service: AccountService = Depends(get_account_service),
):
    try:
        return await service.delete_account(platform, phone, force=force)
    except (AccountServiceError, ValueError) as exc:
        if isinstance(exc, ValueError):
            _raise_account_error(AccountServiceError("invalid_phone", str(exc), 400))
        _raise_account_error(exc)
