from __future__ import annotations

from dataclasses import dataclass

from app.accounts.models import STATUS_DISABLED, STATUS_MUTED, STATUS_NORMAL, STATUS_WARNING
from app.domain.job import STATUS_CANCELLED, STATUS_FAILED, STATUS_SUCCEEDED

WARNING_FAILURE_THRESHOLD = 5

_SEVERE_MARKERS = (
    "封号",
    "禁言",
    "封禁",
    "账号异常",
    "账号被封",
    "禁止发布",
    "无发布权限",
    "banned",
    "muted",
    "forbidden",
    "account disabled",
    "suspended",
)


@dataclass(frozen=True)
class AccountStatusUpdate:
    status: str
    consecutive_failures: int
    reason: str
    applied: bool = True


def is_severe_account_error(detail: str) -> bool:
    text = (detail or "").lower()
    return any(marker.lower() in text for marker in _SEVERE_MARKERS)


def apply_publish_result(
    *,
    current_status: str,
    consecutive_failures: int,
    job_status: str,
    error_detail: str,
) -> AccountStatusUpdate:
    """把 publish job 终态映射到账号持久状态。"""
    failures = max(0, int(consecutive_failures or 0))
    if current_status == STATUS_DISABLED:
        return AccountStatusUpdate(current_status, failures, "account_disabled", applied=False)
    if job_status == STATUS_SUCCEEDED:
        return AccountStatusUpdate(STATUS_NORMAL, 0, "publish_succeeded")
    if job_status == STATUS_CANCELLED:
        return AccountStatusUpdate(current_status, failures, "publish_cancelled", applied=False)
    if job_status != STATUS_FAILED:
        return AccountStatusUpdate(current_status, failures, "publish_not_terminal", applied=False)

    failures += 1
    if is_severe_account_error(error_detail):
        return AccountStatusUpdate(STATUS_MUTED, failures, "account_muted")
    next_status = STATUS_WARNING if failures >= WARNING_FAILURE_THRESHOLD else current_status
    return AccountStatusUpdate(next_status, failures, "publish_failed")
