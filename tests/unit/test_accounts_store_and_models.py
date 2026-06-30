from __future__ import annotations

import pytest

from app.accounts.errors import AccountStoreUnavailable
from app.accounts.models import (
    UNAVAILABLE_STATUSES,
    mask_phone,
    validate_phone,
)
from app.accounts.store import MySQLAccountStore


def test_validate_phone_accepts_11_digit_mobile_number():
    assert validate_phone("13800138000") == "13800138000"


@pytest.mark.parametrize("phone", ["", "23800138000", "1380013800", "138001380000", "13800138abc"])
def test_validate_phone_rejects_invalid_phone(phone: str):
    with pytest.raises(ValueError):
        validate_phone(phone)


def test_mask_phone_masks_middle_digits():
    assert mask_phone("13800138000") == "138****8000"


def test_only_disabled_and_muted_are_unavailable():
    assert UNAVAILABLE_STATUSES == {"disabled", "muted"}


def test_mysql_account_store_ready_reports_missing_required_env(monkeypatch):
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    monkeypatch.delenv("MYSQL_USER", raising=False)
    monkeypatch.delenv("MYSQL_DATABASE", raising=False)
    monkeypatch.setenv("MYSQL_PASSWORD", "")

    store = MySQLAccountStore()

    with pytest.raises(AccountStoreUnavailable) as exc:
        store.ready()
    message = str(exc.value)
    assert "MYSQL_HOST" in message
    assert "MYSQL_USER" in message
    assert "MYSQL_DATABASE" in message
