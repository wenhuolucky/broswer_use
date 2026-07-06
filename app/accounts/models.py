from __future__ import annotations

import re
from dataclasses import dataclass


ACCOUNT_STATUS_NORMAL = "normal"
ACCOUNT_STATUS_WARNING = "warning"
ACCOUNT_STATUS_MUTED = "muted"
ACCOUNT_STATUS_DISABLED = "disabled"

ACCOUNT_STATUSES = {
    ACCOUNT_STATUS_NORMAL,
    ACCOUNT_STATUS_WARNING,
    ACCOUNT_STATUS_MUTED,
    ACCOUNT_STATUS_DISABLED,
}
UNAVAILABLE_STATUSES = {ACCOUNT_STATUS_MUTED, ACCOUNT_STATUS_DISABLED}

_PHONE_RE = re.compile(r"^1\d{10}$")


def validate_phone(phone: str) -> str:
    if not _PHONE_RE.match(phone or ""):
        raise ValueError("手机号必须是 11 位且以 1 开头")
    return phone


def normalize_phone(phone: str) -> str:
    """归一化手机号：去除空格和连字符，校验 11 位格式。

    用于加密和查询索引生成前统一手机号格式。
    """
    normalized = (phone or "").replace(" ", "").replace("-", "")
    if not _PHONE_RE.match(normalized):
        raise ValueError("手机号格式不正确")
    return normalized


def mask_phone(phone: str) -> str:
    if len(phone) != 11:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"


@dataclass
class ArticleAccount:
    id: int
    phone: str
    platform: str
    channel: str
    status: str
    consecutive_failures: int
    created_at: str
    updated_at: str
    group_id: str
    group_text: str = ""
