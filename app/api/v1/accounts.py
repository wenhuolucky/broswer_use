from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.accounts.errors import AccountConflict, AccountStoreUnavailable
from app.accounts.models import ACCOUNT_STATUSES, mask_phone, validate_phone
from app.accounts.store import MySQLAccountStore
from app.api.deps import agent
from app.domain.job import (
    STATUS_CHECKING_COOKIE,
    STATUS_COOKIE_READY,
    STATUS_PUBLISHING,
    STATUS_QUEUED,
    STATUS_STARTING_REMOTE_LOGIN,
    STATUS_WAITING_COOKIE,
)
from app.schemas.accounts import (
    AccountChannelSummary,
    AccountDeleteResponse,
    AccountListResponse,
    AccountPatchRequest,
    AccountResponse,
    AccountRuntimeStatus,
    AccountUpsertRequest,
    account_to_response,
)
from app.schemas.channels import channel_has_valid_cookie

router = APIRouter(prefix="/accounts", tags=["accounts"])
account_store = MySQLAccountStore()


def account_error(status_code: int, code: str, message: str, extra: dict[str, Any] | None = None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "extra": extra or {}},
    )


def _require_group_id(group_id: str) -> str:
    group_id = (group_id or "").strip()
    if not group_id:
        raise account_error(400, "missing_group_id", "缺少账号分组 group_id")
    return group_id


def _validate_phone_or_400(phone: str) -> str:
    try:
        return validate_phone(phone)
    except ValueError as exc:
        raise account_error(400, "invalid_phone", str(exc)) from exc


def _validate_status_or_400(status: str | None) -> str | None:
    if status is None:
        return None
    if status not in ACCOUNT_STATUSES:
        raise account_error(400, "invalid_account_status", "账号状态非法")
    return status


def _handle_store_error(exc: Exception) -> None:
    if isinstance(exc, AccountStoreUnavailable):
        raise account_error(503, "account_store_unavailable", str(exc)) from exc
    if isinstance(exc, AccountConflict):
        raise account_error(409, exc.code, exc.message) from exc
    raise exc


def _channel_summary(channel_id: str) -> AccountChannelSummary | None:
    channel = agent.get_channel(channel_id)
    if channel is None:
        return None
    return AccountChannelSummary(
        channel_id=channel.channel_id,
        platform=channel.platform,
        status=channel.status,
        account_name=channel.account_name,
        has_valid_cookie=channel_has_valid_cookie(channel),
    )


def _runtime_status(channel_id: str) -> AccountRuntimeStatus | None:
    try:
        publish_count = agent.count_unfinished_publish_jobs_for_channel(channel_id)
    except Exception as exc:
        raise account_error(503, "publish_status_unavailable", "查询账号发文状态失败") from exc
    publish_count = max(0, int(publish_count))
    return AccountRuntimeStatus(
        account_status="idle" if publish_count == 0 else "publishing",
        publish_count=publish_count,
        is_idle=publish_count == 0,
    )


def _account_response(account, *, include_channel: bool, include_runtime: bool) -> AccountResponse:
    channel = _channel_summary(account.channel) if include_channel and account.channel else None
    runtime = _runtime_status(account.channel) if include_runtime and account.channel else None
    return account_to_response(account, channel=channel, runtime=runtime)


@router.get("/all", response_model=AccountListResponse)
async def list_all_accounts(
    group_id: str = Query(default=""),
    platform: str | None = None,
    status: str | None = None,
    group_text: str | None = None,
    include_channel: bool = True,
    include_runtime: bool = False,
    limit: int = 100,
    offset: int = 0,
):
    group_id = _require_group_id(group_id)
    _validate_status_or_400(status)
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    try:
        rows = account_store.list_accounts(
            group_id=group_id,
            platform=platform,
            status=status,
            available_only=False,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        _handle_store_error(exc)
    return AccountListResponse(
        group_id=group_id,
        group_text=group_text or (rows[0].group_text if rows else ""),
        count=len(rows),
        limit=limit,
        offset=offset,
        accounts=[
            _account_response(row, include_channel=include_channel, include_runtime=include_runtime)
            for row in rows
        ],
    )


@router.get("/available", response_model=AccountListResponse)
async def list_available_accounts(
    group_id: str = Query(default=""),
    platform: str | None = None,
    group_text: str | None = None,
    include_channel: bool = True,
    include_runtime: bool = False,
    limit: int = 100,
    offset: int = 0,
):
    group_id = _require_group_id(group_id)
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    try:
        rows = account_store.list_accounts(
            group_id=group_id,
            platform=platform,
            available_only=True,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        _handle_store_error(exc)
    return AccountListResponse(
        group_id=group_id,
        group_text=group_text or (rows[0].group_text if rows else ""),
        count=len(rows),
        limit=limit,
        offset=offset,
        accounts=[
            _account_response(row, include_channel=include_channel, include_runtime=include_runtime)
            for row in rows
        ],
    )


@router.get("/{platform}/{phone}", response_model=AccountResponse)
async def get_account(
    platform: str,
    phone: str,
    group_id: str = Query(default=""),
    include_channel: bool = True,
    include_runtime: bool = False,
):
    group_id = _require_group_id(group_id)
    phone = _validate_phone_or_400(phone)
    try:
        account = account_store.get_account(group_id=group_id, platform=platform, phone=phone)
    except Exception as exc:
        _handle_store_error(exc)
    if account is None:
        raise account_error(404, "account_not_found", "请求分组下没有该账号")
    return _account_response(account, include_channel=include_channel, include_runtime=include_runtime)


@router.put("/{platform}/{phone}", response_model=AccountResponse)
async def upsert_account(platform: str, phone: str, request: AccountUpsertRequest):
    group_id = _require_group_id(request.group_id)
    phone = _validate_phone_or_400(phone)
    status = _validate_status_or_400(request.status) or "normal"
    channel = agent.get_channel(request.channel_id)
    if channel is None:
        raise account_error(404, "channel_not_found", "保存绑定时 channel 不存在")
    if channel.platform != platform:
        raise account_error(409, "channel_platform_mismatch", "channel 平台和账号平台不一致")
    try:
        account = account_store.upsert_account(
            group_id=group_id,
            group_text=request.group_text or group_id,
            platform=platform,
            phone=phone,
            channel=request.channel_id,
            status=status,
            consecutive_failures=request.consecutive_failures,
            reset_failures=request.reset_failures,
        )
    except Exception as exc:
        _handle_store_error(exc)
    return _account_response(account, include_channel=True, include_runtime=False)


@router.patch("/{platform}/{phone}", response_model=AccountResponse)
async def patch_account(platform: str, phone: str, request: AccountPatchRequest):
    group_id = _require_group_id(request.group_id)
    phone = _validate_phone_or_400(phone)
    if request.new_phone is not None:
        _validate_phone_or_400(request.new_phone)
    _validate_status_or_400(request.status)
    if not any(
        value is not None
        for value in (
            request.group_text,
            request.new_phone,
            request.status,
            request.reset_failures,
            request.consecutive_failures,
        )
    ):
        raise account_error(400, "empty_account_patch", "PATCH 至少需要一个可修改字段")
    reset_failures = request.reset_failures
    if request.status == "normal" and reset_failures is None:
        reset_failures = True
    try:
        account = account_store.update_account(
            group_id=group_id,
            platform=platform,
            phone=phone,
            group_text=request.group_text,
            new_phone=request.new_phone,
            status=request.status,
            reset_failures=reset_failures,
            consecutive_failures=request.consecutive_failures,
        )
    except Exception as exc:
        _handle_store_error(exc)
    if account is None:
        raise account_error(404, "account_not_found", "请求分组下没有该账号")
    return _account_response(account, include_channel=True, include_runtime=False)


@router.delete("/{platform}/{phone}", response_model=AccountDeleteResponse)
async def delete_account(
    platform: str,
    phone: str,
    group_id: str = Query(default=""),
    force: bool = False,
    delete_channel: bool = True,
):
    group_id = _require_group_id(group_id)
    phone = _validate_phone_or_400(phone)
    try:
        account = account_store.get_account(group_id=group_id, platform=platform, phone=phone)
    except Exception as exc:
        _handle_store_error(exc)
    if account is None:
        raise account_error(404, "account_not_found", "请求分组下没有该账号")

    publish_count = agent.count_unfinished_publish_jobs_for_channel(account.channel) if account.channel else 0
    if publish_count and not force:
        raise account_error(409, "account_busy", "账号仍有未完成发文任务")

    cancelled_jobs: list[str] = []
    cleanup_warnings: list[str] = []
    if force and account.channel:
        for job in _unfinished_publish_jobs(account.channel):
            job_id = getattr(job, "job_id", "")
            if not job_id:
                continue
            try:
                result = agent.cancel_job(job_id)
                if inspect.isawaitable(result):
                    await result
                cancelled_jobs.append(job_id)
            except Exception as exc:
                cleanup_warnings.append(f"cancel_job:{job_id}:{exc}")

    try:
        deleted = account_store.delete_account(group_id=group_id, platform=platform, phone=phone)
    except Exception as exc:
        _handle_store_error(exc)
    if deleted is None:
        raise account_error(404, "account_not_found", "请求分组下没有该账号")

    channel_deleted = False
    if delete_channel and account.channel:
        try:
            channel_deleted = bool(agent.delete_channel(account.channel))
        except Exception as exc:
            cleanup_warnings.append(f"delete_channel:{account.channel}:{exc}")

    proxy_unassigned = _unassign_proxy(account.channel, cleanup_warnings) if account.channel else False

    return AccountDeleteResponse(
        platform=platform,
        phone=phone,
        phone_masked=mask_phone(deleted.phone),
        group_id=group_id,
        group_text=deleted.group_text,
        deleted=True,
        channel_id=deleted.channel,
        channel_deleted=channel_deleted,
        proxy_unassigned=proxy_unassigned,
        cancelled_jobs=cancelled_jobs,
        cleanup_warnings=cleanup_warnings,
    )


def _unfinished_publish_jobs(channel_id: str) -> list[Any]:
    job_store = getattr(agent, "job_store", None)
    if job_store is None or not hasattr(job_store, "list_jobs"):
        return []
    statuses = {
        STATUS_CHECKING_COOKIE,
        STATUS_COOKIE_READY,
        STATUS_STARTING_REMOTE_LOGIN,
        STATUS_WAITING_COOKIE,
        STATUS_PUBLISHING,
        STATUS_QUEUED,
    }
    try:
        return list(
            job_store.list_jobs(
                channel_id=channel_id,
                statuses=statuses,
                job_type="publish",
                limit=500,
            )
        )
    except TypeError:
        return []


def _unassign_proxy(channel_id: str, cleanup_warnings: list[str]) -> bool:
    try:
        from app.proxy.assignment import get_assignment_manager

        manager = get_assignment_manager()
        if manager is None:
            return False
        if hasattr(manager, "unassign_channel"):
            return bool(manager.unassign_channel(channel_id))
        if hasattr(manager, "_unassign_channel"):
            manager._unassign_channel(channel_id)
            return True
        return False
    except Exception as exc:
        cleanup_warnings.append(f"unassign_proxy:{channel_id}:{exc}")
        return False
