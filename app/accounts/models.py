from __future__ import annotations

import re
from dataclasses import dataclass

STATUS_NORMAL = "normal"
STATUS_WARNING = "warning"
STATUS_MUTED = "muted"
STATUS_DISABLED = "disabled"

ACCOUNT_STATUSES = {
    STATUS_NORMAL,
    STATUS_WARNING,
    STATUS_MUTED,
    STATUS_DISABLED,
}

_PHONE_RE = re.compile(r"^1\d{10}$")


def validate_phone(phone: str) -> str:
    """校验业务账号标识：必须是 11 位手机号。"""
    value = (phone or "").strip()
    if not _PHONE_RE.fullmatch(value):
        raise ValueError("phone must match ^1\\d{10}$")
    return value


def mask_phone(phone: str) -> str:
    value = validate_phone(phone)
    return f"{value[:3]}****{value[-4:]}"


def validate_account_status(status: str) -> str:
    value = (status or "").strip()
    if value not in ACCOUNT_STATUSES:
        raise ValueError("invalid account status")
    return value


@dataclass
class ArticleAccount:
    id: int | str
    platform: str
    phone: str
    channel: str
    status: str = STATUS_NORMAL
    consecutive_failures: int = 0
    group_id: str = ""
    group_text: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def phone_masked(self) -> str:
        return mask_phone(self.phone)
