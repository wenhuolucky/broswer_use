"""Shared SQLite plumbing for the file-backed stores (JobStore / ChannelStore).

单节点自部署：作业/渠道持久化用一个文件型 SQLite 取代 PostgreSQL。两个 store 形态
一致：``SQLITE_PATH`` 配置时各持有一条连接，否则退化为内存 dict（仅本地开发/测试，
重启即丢）。

并发模型：服务是单进程、写并发被稀缺的浏览器/Xvnc 槽位天然限制（量很小），故每个
store 用一条 ``check_same_thread=False`` 连接 + ``self._lock`` 把读写串行化即可，
无需连接池。WAL + busy_timeout 让 JobStore / ChannelStore 两条连接并发访问同一文件
不至于 "database is locked"。

子类设置 ``_SCHEMA_DDL``（CREATE TABLE 块）并自带 ``self._conn``（内存兜底时为
``None``）与 ``self._lock``（RLock）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any


def now_iso() -> str:
    """统一的时间戳格式：UTC ISO8601 字符串。

    两个后端（SQLite / 内存兜底）都用它，存成 TEXT 后可直接做字典序比较（同为 UTC
    偏移，字典序即时间序），故 purge 之类的时间过滤无需解析回 datetime。
    """
    return datetime.now(UTC).isoformat()


class SqliteStoreMixin:
    _SCHEMA_DDL = ""

    def _connect(self, path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.executescript(self._SCHEMA_DDL)

    def _json(self, value: Any) -> str:
        """把 dict/list 序列化为存进 TEXT 列的 JSON 字符串。"""
        return json.dumps(value or {}, ensure_ascii=False)

    @staticmethod
    def _loads(value: Any) -> Any:
        """把 JSON 文本列读回 Python 对象（空值归一为 {}）。"""
        if not value:
            return {}
        if isinstance(value, (dict, list)):
            return value
        return json.loads(value)

    def ready(self) -> bool:
        """Lightweight readiness probe (in-memory mode is always ready)."""
        if self._conn is None:
            return True
        with self._lock:
            self._conn.execute("SELECT 1")
        return True
