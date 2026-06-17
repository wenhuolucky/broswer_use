"""Channel domain model.

A *channel* is the service-issued, platform-agnostic handle for one connected
publishing account. One ``channel_id`` binds 1:1 to a single platform, a single
platform account (头条号 / 搜狐号) and a single cookie. Callers only ever deal
with ``channel_id``; the platform is a bound attribute, not part of the id.

Lifecycle: a channel is minted (``pending``) the moment a login session starts,
then bound (``bound``) to a real platform account once the cookie is captured.
It flips to ``invalid`` when its cookie stops working.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

STATUS_CHANNEL_PENDING = "pending"
STATUS_CHANNEL_BOUND = "bound"
STATUS_CHANNEL_INVALID = "invalid"

_CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def new_channel_id() -> str:
    """Mint a fresh opaque channel id (no platform prefix)."""
    return uuid.uuid4().hex


def validate_channel_id(value: str) -> str:
    """校验 channel_id 合法性。

    channel_id 由本服务签发（uuid4 hex），但仍会作为路径参数出现在
    ``/channels/{channel_id}`` 等接口里，这里对外部传入的值做一次格式兜底，
    挡住空值与非法字符，返回干净的 422 而不是让下游抛 500。
    """
    if not value or not _CHANNEL_ID_RE.match(value):
        raise ValueError("channel_id 不合法：只能包含字母、数字、'_'、'-'，长度 1-64")
    return value


@dataclass
class Channel:
    channel_id: str
    platform: str
    status: str = STATUS_CHANNEL_PENDING
    account_name: str = ""
    # 平台原生账号键（头条 uid_tt / 搜狐 ppinf|SUV），用于「同账号重复登录」去重。
    native_key: str = ""
    # normalize 后的 storage_state：{"cookies": [...], "origins": [...]}。
    cookie: dict[str, Any] = field(default_factory=dict)
    # 平台特定属性袋（如搜狐 {"account_number": "122702850"}），新增平台不改 schema。
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC).isoformat()
