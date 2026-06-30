from __future__ import annotations

from typing import Any

from app.accounts.models import (
    ArticleAccount,
    STATUS_DISABLED,
    STATUS_MUTED,
    STATUS_NORMAL,
    STATUS_WARNING,
    mask_phone,
    validate_account_status,
    validate_phone,
)
from app.accounts.status import apply_publish_result
from app.domain.job import (
    AutoPublishRequest,
    LoginRequest,
    STATUS_CANCELLED,
    STATUS_CHECKING_COOKIE,
    STATUS_COOKIE_READY,
    STATUS_FAILED,
    STATUS_PUBLISHING,
    STATUS_QUEUED,
    STATUS_STARTING_REMOTE_LOGIN,
    STATUS_SUCCEEDED,
    STATUS_WAITING_COOKIE,
)
from app.schemas.accounts import (
    AccountDeleteResponse,
    AccountJobCancelResponse,
    AccountJobResponse,
    AccountJobStatusUpdateResponse,
    AccountJobSummary,
    AccountListResponse,
    AccountLoginSessionCreatedResponse,
    AccountLoginSessionResponse,
    AccountPublishCreatedResponse,
    AccountPublishRuntime,
    AccountPublishStatusResponse,
    AccountResponse,
)
from app.schemas.channels import channel_publish_status_from, channel_to_response
from app.schemas.jobs import job_created_from, job_to_response
from app.schemas.login_sessions import login_session_created_from, login_session_to_response

EXECUTING_PUBLISH_STATUSES = {
    STATUS_CHECKING_COOKIE,
    STATUS_COOKIE_READY,
    STATUS_STARTING_REMOTE_LOGIN,
    STATUS_WAITING_COOKIE,
    STATUS_PUBLISHING,
}
UNFINISHED_PUBLISH_STATUSES = EXECUTING_PUBLISH_STATUSES | {STATUS_QUEUED}
TERMINAL_STATUSES = {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED}


class AccountServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.extra = extra or {}


class AccountService:
    def __init__(self, *, store, agent, group_id: str, group_text: str = ""):
        self.store = store
        self.agent = agent
        self.group_id = group_id
        self.group_text = group_text or group_id

    def list_accounts(
        self,
        *,
        platform: str | None,
        include_runtime: bool,
        availability: str,
        show_channel: bool,
        limit: int,
        offset: int,
    ) -> AccountListResponse:
        accounts = self.store.list_accounts(platform=platform, limit=limit, offset=offset)
        rendered = [
            self.account_response(account, include_runtime=include_runtime, show_channel=show_channel)
            for account in accounts
        ]
        if availability != "all":
            rendered = [item for item in rendered if self._matches_availability(item, availability)]
        return AccountListResponse(
            group_id=self.group_id,
            group_text=self.group_text,
            count=len(rendered) if availability != "all" else self.store.count_accounts(platform=platform),
            limit=max(1, min(int(limit), 500)),
            offset=max(0, int(offset)),
            accounts=rendered,
        )

    def get_account_response(
        self,
        platform: str,
        phone: str,
        *,
        include_runtime: bool,
        show_channel: bool,
    ) -> AccountResponse:
        account = self.require_account(platform, phone)
        return self.account_response(account, include_runtime=include_runtime, show_channel=show_channel)

    def account_response(
        self,
        account: ArticleAccount,
        *,
        include_runtime: bool,
        show_channel: bool,
    ) -> AccountResponse:
        channel = self.agent.get_channel(account.channel) if account.channel else None
        channel_resp = channel_to_response(channel) if channel else None
        runtime = self.runtime_for_account(account) if include_runtime and channel else None
        availability, reasons = self._availability(account, channel_resp, runtime)
        return AccountResponse(
            id=account.id,
            platform=account.platform,
            phone=account.phone,
            phone_masked=account.phone_masked,
            status=account.status,
            consecutive_failures=account.consecutive_failures,
            group_id=account.group_id or self.group_id,
            group_text=account.group_text or self.group_text,
            channel_id=account.channel if show_channel else None,
            created_at=account.created_at,
            updated_at=account.updated_at,
            channel=channel_resp,
            runtime=runtime,
            availability=availability,
            unavailable_reasons=reasons,
        )

    async def create_login_session(self, *, platform: str, phone: str) -> AccountLoginSessionCreatedResponse:
        phone = validate_phone(phone)
        resp = await self.agent.start_login_only(LoginRequest(platform=platform))
        created = login_session_created_from(resp)
        job = self.agent.job_store.get(created.session_id)
        if job is not None:
            payload = dict(job.payload or {})
            payload["account_platform"] = platform
            payload["account_phone"] = phone
            payload["group_id"] = self.group_id
            payload["group_text"] = self.group_text
            self.agent.job_store.update(job.job_id, payload=payload)
        return AccountLoginSessionCreatedResponse(
            **created.model_dump(),
            platform=platform,
            phone=phone,
            phone_masked=mask_phone(phone),
            group_id=self.group_id,
            group_text=self.group_text,
        )

    def get_login_session(self, session_id: str) -> AccountLoginSessionResponse:
        job = self._require_job(session_id, "login", "account_login_session_not_found")
        response = login_session_to_response(job)
        payload = job.payload or {}
        phone = str(payload.get("account_phone", "") or "")
        platform = str(payload.get("account_platform", "") or response.platform)
        account = None
        account_saved = False
        if job.status == STATUS_SUCCEEDED and phone:
            channel_id = str((job.result or {}).get("channel_id", "") or response.channel_id)
            existing = self.store.get_account(platform, phone)
            if payload.get("account_saved") and existing is not None:
                account = existing
            else:
                account = self.store.upsert_account(
                    platform=platform,
                    phone=phone,
                    channel=channel_id,
                    status=STATUS_NORMAL,
                    consecutive_failures=0,
                )
                updated_payload = dict(payload)
                updated_payload["account_saved"] = True
                self.agent.job_store.update(job.job_id, payload=updated_payload)
            account_saved = True
        return AccountLoginSessionResponse(
            **response.model_dump(),
            phone=phone,
            phone_masked=mask_phone(phone) if phone else "",
            group_id=self.group_id,
            group_text=self.group_text,
            account_saved=account_saved,
            account=self.account_response(account, include_runtime=True, show_channel=False) if account else None,
        )

    async def cancel_login_session(self, session_id: str) -> AccountLoginSessionResponse:
        self._require_job(session_id, "login", "account_login_session_not_found")
        resp = await self.agent.cancel_job(session_id)
        if resp.code not in (200, 202):
            raise AccountServiceError("login_session_cancel_failed", resp.message, resp.code)
        return self.get_login_session(session_id)

    def update_status(self, platform: str, phone: str, *, status: str, reset_failures: bool | None) -> AccountResponse:
        validate_phone(phone)
        status = validate_account_status(status)
        failures = 0 if (reset_failures is True or (status == STATUS_NORMAL and reset_failures is not False)) else None
        try:
            account = self.store.update_status(platform, phone, status=status, consecutive_failures=failures)
        except KeyError as exc:
            raise AccountServiceError("account_not_found", "账号不存在", 404) from exc
        return self.account_response(account, include_runtime=True, show_channel=False)

    def rename_phone(self, platform: str, phone: str, *, new_phone: str) -> AccountResponse:
        validate_phone(phone)
        validate_phone(new_phone)
        try:
            account = self.store.rename_phone(platform, phone, new_phone)
        except ValueError as exc:
            raise AccountServiceError("account_phone_exists", "手机号已存在", 409) from exc
        except KeyError as exc:
            raise AccountServiceError("account_not_found", "账号不存在", 404) from exc
        return self.account_response(account, include_runtime=True, show_channel=False)

    def publish_status(self, platform: str, phone: str, *, show_channel: bool = False) -> AccountPublishStatusResponse:
        account = self.require_account(platform, phone)
        runtime = self.runtime_for_account(account)
        channel = self.agent.get_channel(account.channel) if account.channel else None
        return AccountPublishStatusResponse(
            platform=account.platform,
            phone=account.phone,
            phone_masked=account.phone_masked,
            status=account.status,
            channel_id=account.channel if show_channel else None,
            channel=channel_to_response(channel) if channel else None,
            account_status=runtime.account_status,
            publish_status=runtime.publish_status,
            publish_count=runtime.publish_count,
            is_idle=runtime.is_idle,
            running_job=runtime.running_job,
            queued_count=runtime.queued_count,
            queued_jobs=runtime.queued_jobs,
        )

    async def publish(
        self,
        platform: str,
        phone: str,
        *,
        title: str,
        content: str,
        cover_image_url: str | None,
        allow_warning: bool,
    ) -> AccountPublishCreatedResponse:
        account = self.require_account(platform, phone)
        if account.status == STATUS_DISABLED:
            raise AccountServiceError("account_disabled", "账号已禁用", 409)
        if account.status == STATUS_MUTED:
            raise AccountServiceError("account_muted", "账号疑似禁言或封号", 409)
        if account.status == STATUS_WARNING and not allow_warning:
            raise AccountServiceError("account_warning", "账号处于 warning 状态", 409)
        channel = self.agent.get_channel(account.channel)
        if channel is None:
            raise AccountServiceError("channel_not_found", "账号 channel 不存在", 404)
        channel_resp = channel_to_response(channel)
        if channel_resp.status != "bound" or not channel_resp.has_valid_cookie:
            raise AccountServiceError("cookie_invalid", "账号 cookie 无效，请重新登录", 409)
        runtime = self.runtime_for_account(account)
        if not runtime.is_idle:
            raise AccountServiceError(
                "account_busy",
                "账号当前存在未完成发文任务",
                409,
                {"publish_count": runtime.publish_count},
            )
        resp = await self.agent.submit(
            AutoPublishRequest(
                channel_id=account.channel,
                title=title,
                content=content,
                cover_image_url=cover_image_url,
            )
        )
        created = job_created_from(resp)
        return AccountPublishCreatedResponse(
            **created.model_dump(),
            platform=account.platform,
            phone=account.phone,
            phone_masked=account.phone_masked,
            group_id=self.group_id,
            group_text=self.group_text,
        )

    def get_job(self, job_id: str) -> AccountJobResponse:
        job = self._require_job(job_id, "publish", "account_job_not_found")
        account = self._account_for_job(job)
        job_resp = job_to_response(job)
        update_resp = None
        if account and job.status in TERMINAL_STATUSES and not (job.payload or {}).get("account_status_applied"):
            update_resp = self._apply_job_status(account, job)
            payload = dict(job.payload or {})
            payload["account_status_applied"] = True
            self.agent.job_store.update(job.job_id, payload=payload)
        current_account = self.store.find_by_channel(job_resp.channel_id) if job_resp.channel_id else account
        return AccountJobResponse(
            job=job_resp,
            account=self.account_response(current_account, include_runtime=False, show_channel=False)
            if current_account
            else None,
            account_update=update_resp,
        )

    async def cancel_job(self, job_id: str) -> AccountJobCancelResponse:
        job = self._require_job(job_id, "publish", "account_job_not_found")
        account = self._account_for_job(job)
        if account is None:
            raise AccountServiceError("account_job_not_found", "任务不存在或不属于当前租户账号", 404)
        resp = await self.agent.cancel_job(job_id)
        if resp.code not in (200, 202):
            raise AccountServiceError("job_cancel_failed", resp.message, resp.code)
        cancelled = self._require_job(job_id, "publish", "account_job_not_found")
        return AccountJobCancelResponse(
            job=job_to_response(cancelled),
            account={
                "platform": account.platform,
                "phone": account.phone,
                "phone_masked": account.phone_masked,
            },
        )

    async def delete_account(self, platform: str, phone: str, *, force: bool) -> AccountDeleteResponse:
        account = self.require_account(platform, phone)
        unfinished = self.agent.job_store.list_jobs(
            channel_id=account.channel,
            statuses=UNFINISHED_PUBLISH_STATUSES,
            job_type="publish",
            limit=500,
        )
        cancelled_jobs: list[str] = []
        if unfinished and not force:
            raise AccountServiceError(
                "account_busy",
                "账号当前存在未完成发文任务",
                409,
                {"publish_count": len(unfinished)},
            )
        for job in unfinished:
            resp = await self.agent.cancel_job(job.job_id)
            if resp.code in (200, 202):
                cancelled_jobs.append(job.job_id)

        deleted = self.store.delete_account(platform, phone)
        if deleted is None:
            raise AccountServiceError("account_not_found", "账号不存在", 404)

        cleanup_warnings: list[str] = []
        channel_deleted = False
        if account.channel:
            try:
                channel_deleted = bool(self.agent.delete_channel(account.channel))
            except Exception as exc:  # best effort cleanup
                cleanup_warnings.append(f"channel_delete_failed: {exc}")

        proxy_unassigned = False
        try:
            from app.proxy.assignment import get_assignment_manager

            manager = get_assignment_manager()
            if manager is not None and account.channel:
                proxy_unassigned = bool(manager.unassign_channel(account.channel))
        except Exception as exc:
            cleanup_warnings.append(f"proxy_unassign_failed: {exc}")

        return AccountDeleteResponse(
            platform=account.platform,
            phone=account.phone,
            phone_masked=account.phone_masked,
            group_id=self.group_id,
            group_text=self.group_text,
            deleted=True,
            channel_id=account.channel,
            channel_deleted=channel_deleted,
            proxy_unassigned=proxy_unassigned,
            cancelled_jobs=cancelled_jobs,
            cleanup_warnings=cleanup_warnings,
        )

    def require_account(self, platform: str, phone: str) -> ArticleAccount:
        phone = validate_phone(phone)
        account = self.store.get_account(platform, phone)
        if account is None:
            raise AccountServiceError("account_not_found", "账号不存在", 404)
        return account

    def runtime_for_account(self, account: ArticleAccount) -> AccountPublishRuntime:
        publish_count = self.agent.count_unfinished_publish_jobs_for_channel(account.channel) if account.channel else 0
        status = channel_publish_status_from(account.channel, publish_count)
        running_jobs = self.agent.job_store.list_jobs(
            channel_id=account.channel,
            statuses=EXECUTING_PUBLISH_STATUSES,
            job_type="publish",
            limit=1,
        )
        queued_jobs = self.agent.job_store.list_jobs(
            channel_id=account.channel,
            statuses={STATUS_QUEUED},
            job_type="publish",
            limit=50,
        )
        running = self._job_summary(running_jobs[0]) if running_jobs else None
        queued = [self._job_summary(job) for job in queued_jobs]
        return AccountPublishRuntime(
            channel_id=account.channel,
            account_status=status.account_status,
            publish_status=status.account_status,
            publish_count=status.publish_count,
            is_idle=status.account_status == "idle" and status.publish_count == 0,
            running_job=running,
            queued_count=len(queued),
            queued_jobs=queued,
        )

    def _job_summary(self, job) -> AccountJobSummary:
        response = job_to_response(job)
        return AccountJobSummary(
            job_id=response.job_id,
            status=response.status,
            title=response.title,
            live_url=response.live_url,
            created_at=response.created_at,
            updated_at=response.updated_at,
        )

    def _availability(self, account: ArticleAccount, channel, runtime) -> tuple[str, list[str]]:
        reasons: list[str] = []
        if account.status == STATUS_DISABLED:
            reasons.append("status_disabled")
            return "disabled", reasons
        if account.status == STATUS_MUTED:
            reasons.append("status_muted")
            return "muted", reasons
        if account.status == STATUS_WARNING:
            reasons.append("status_warning")
            return "warning", reasons
        if channel is None:
            reasons.append("channel_missing")
            return "missing_channel", reasons
        if channel.status == "pending":
            reasons.append("channel_pending")
            return "missing_channel", reasons
        if channel.status != "bound":
            reasons.append("channel_invalid")
            return "invalid_cookie", reasons
        if not channel.has_valid_cookie:
            reasons.append("cookie_invalid")
            return "invalid_cookie", reasons
        if runtime is not None and not runtime.is_idle:
            reasons.append("runtime_busy")
            return "busy", reasons
        return "usable", reasons

    def _matches_availability(self, account: AccountResponse, availability: str) -> bool:
        if availability == "usable":
            return account.availability == "usable"
        if availability == "unusable":
            return account.availability != "usable"
        return account.availability == availability

    def _require_job(self, job_id: str, expected_type: str, code: str):
        job = self.agent.job_store.get(job_id)
        if job is None or job.type != expected_type:
            raise AccountServiceError(code, "任务不存在或不属于当前租户账号", 404)
        return job

    def _account_for_job(self, job) -> ArticleAccount | None:
        channel_id = str((job.payload or {}).get("channel_id", "") or "")
        if not channel_id:
            return None
        return self.store.find_by_channel(channel_id)

    def _apply_job_status(self, account: ArticleAccount, job) -> AccountJobStatusUpdateResponse:
        detail = job.error or str((job.result or {}).get("failure_reason", "") or "")
        update = apply_publish_result(
            current_status=account.status,
            consecutive_failures=account.consecutive_failures,
            job_status=job.status,
            error_detail=detail,
        )
        if update.applied:
            self.store.update_status(
                account.platform,
                account.phone,
                status=update.status,
                consecutive_failures=update.consecutive_failures,
            )
        return AccountJobStatusUpdateResponse(
            applied=update.applied,
            reason=update.reason,
            previous_status=account.status,
            new_status=update.status,
            previous_consecutive_failures=account.consecutive_failures,
            new_consecutive_failures=update.consecutive_failures,
        )
