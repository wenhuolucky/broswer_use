from __future__ import annotations

from app.accounts.models import STATUS_DISABLED, STATUS_MUTED, STATUS_NORMAL, STATUS_WARNING
from app.accounts.status import apply_publish_result, is_severe_account_error


def test_phone_must_be_mainland_mobile_number():
    from app.accounts.models import validate_phone

    assert validate_phone("13800138000") == "13800138000"


def test_phone_rejects_aliases_and_masked_values():
    from app.accounts.models import validate_phone

    for value in ("main", "", "138****8000", "23800138000"):
        try:
            validate_phone(value)
        except ValueError:
            continue
        raise AssertionError(f"{value!r} should be rejected")


def test_publish_success_resets_failures_to_normal():
    result = apply_publish_result(
        current_status=STATUS_WARNING,
        consecutive_failures=5,
        job_status="succeeded",
        error_detail="",
    )

    assert result.status == STATUS_NORMAL
    assert result.consecutive_failures == 0
    assert result.reason == "publish_succeeded"


def test_disabled_status_is_not_changed_by_publish_result():
    result = apply_publish_result(
        current_status=STATUS_DISABLED,
        consecutive_failures=3,
        job_status="succeeded",
        error_detail="",
    )

    assert result.status == STATUS_DISABLED
    assert result.consecutive_failures == 3
    assert result.applied is False


def test_fifth_normal_failure_enters_warning():
    result = apply_publish_result(
        current_status=STATUS_NORMAL,
        consecutive_failures=4,
        job_status="failed",
        error_detail="平台发布失败",
    )

    assert result.status == STATUS_WARNING
    assert result.consecutive_failures == 5
    assert result.reason == "publish_failed"


def test_severe_account_error_enters_muted():
    assert is_severe_account_error("账号已被禁言，禁止发布")
    result = apply_publish_result(
        current_status=STATUS_NORMAL,
        consecutive_failures=0,
        job_status="failed",
        error_detail="账号已被禁言，禁止发布",
    )

    assert result.status == STATUS_MUTED
    assert result.consecutive_failures == 1
    assert result.reason == "account_muted"
