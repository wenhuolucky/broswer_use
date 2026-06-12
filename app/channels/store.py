"""Channel store — durable home for the service-issued publishing channels.

Replaces the old filesystem cookie store: a *channel* (handle + bound platform
account + cookie) is now a first-class record living in PostgreSQL, mirroring
``JobStore``. When ``PGSQL_DSN`` is empty it falls back to an in-memory dict for
local dev / tests (cookies are then lost on restart, same as jobs).

The store is platform-agnostic: it keys dedup on the opaque ``native_key`` string
and never reasons about any concrete platform. Platform-specific bits (cookie
validity, native key extraction, article-url account id) live in the platform
configs reached through ``app.platforms.registry``.
"""

from __future__ import annotations

import json
from threading import RLock
from typing import Any

from app.core.config import PGSQL_DSN, PGSQL_POOL_MAX, PGSQL_POOL_MIN
from app.domain.channel import (
    STATUS_CHANNEL_BOUND,
    STATUS_CHANNEL_PENDING,
    Channel,
    new_channel_id,
)
from app.platforms import registry

# Text columns that map 1:1 to a Channel attribute and can be SET directly.
_TEXT_COLUMNS = {"platform", "status", "account_name", "native_key"}
# JSONB columns.
_JSON_COLUMNS = {"cookie", "metadata"}

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id   TEXT PRIMARY KEY,
    platform     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    account_name TEXT NOT NULL DEFAULT '',
    native_key   TEXT NOT NULL DEFAULT '',
    cookie       JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_channels_platform_native
    ON channels (platform, native_key) WHERE native_key <> '';
CREATE INDEX IF NOT EXISTS idx_channels_platform ON channels (platform);
"""

_SELECT_COLUMNS = (
    "channel_id, platform, status, account_name, native_key, cookie, metadata, "
    "created_at, updated_at"
)


class ChannelStore:
    """Channel registry backed by PostgreSQL when ``PGSQL_DSN`` is configured."""

    def __init__(self, dsn: str = PGSQL_DSN):
        self._pool = self._connect_pool(dsn) if dsn else None
        self._channels: dict[str, Channel] = {}
        self._native_index: dict[tuple[str, str], str] = {}
        self._lock = RLock()
        if self._pool is not None:
            self._ensure_schema()

    # ------------------------------------------------------------------
    # public API (synchronous)
    # ------------------------------------------------------------------
    def create(self, platform: str) -> Channel:
        """Mint a fresh ``pending`` channel for an in-flight login."""
        channel_id = new_channel_id()
        if self._pool is None:
            channel = Channel(channel_id=channel_id, platform=platform, status=STATUS_CHANNEL_PENDING)
            with self._lock:
                self._channels[channel_id] = channel
            return channel
        with self._pool.connection() as conn:
            row = conn.execute(
                f"INSERT INTO channels (channel_id, platform, status) VALUES (%s, %s, %s) "
                f"RETURNING {_SELECT_COLUMNS}",
                (channel_id, platform, STATUS_CHANNEL_PENDING),
            ).fetchone()
        return self._row_to_channel(row)

    def get(self, channel_id: str) -> Channel | None:
        if not channel_id:
            return None
        if self._pool is None:
            with self._lock:
                return self._channels.get(channel_id)
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM channels WHERE channel_id = %s",
                (channel_id,),
            ).fetchone()
        return self._row_to_channel(row) if row else None

    def update(self, channel_id: str, **changes) -> Channel:
        if self._pool is None:
            return self._update_memory(channel_id, **changes)
        return self._update_pg(channel_id, **changes)

    def find_by_native(self, platform: str, native_key: str) -> Channel | None:
        if not native_key:
            return None
        if self._pool is None:
            with self._lock:
                mapped = self._native_index.get((platform, native_key))
                return self._channels.get(mapped) if mapped else None
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM channels "
                "WHERE platform = %s AND native_key = %s ORDER BY created_at LIMIT 1",
                (platform, native_key),
            ).fetchone()
        return self._row_to_channel(row) if row else None

    def bind(
        self,
        channel_id: str,
        *,
        cookie: dict[str, Any],
        native_key: str,
        account_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> Channel:
        """Bind a pending channel to a real platform account.

        Dedup strategy A: if another channel on the same platform already carries
        this ``native_key`` (same account logging in again), merge onto it —
        refresh its cookie and drop the freshly-minted pending channel, returning
        the canonical channel.
        """
        channel = self.get(channel_id)
        if channel is None:
            raise KeyError(channel_id)
        platform = channel.platform
        metadata = metadata or {}

        existing = self.find_by_native(platform, native_key) if native_key else None
        if existing is not None and existing.channel_id != channel_id:
            merged = self.update(
                existing.channel_id,
                status=STATUS_CHANNEL_BOUND,
                cookie=cookie,
                native_key=native_key,
                account_name=account_name or existing.account_name,
                metadata={**(existing.metadata or {}), **metadata},
            )
            self.delete(channel_id)
            return merged

        return self.update(
            channel_id,
            status=STATUS_CHANNEL_BOUND,
            cookie=cookie,
            native_key=native_key,
            account_name=account_name,
            metadata={**(channel.metadata or {}), **metadata},
        )

    def list_channels(
        self, *, platform: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Channel]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        if self._pool is None:
            with self._lock:
                channels = list(self._channels.values())
            if platform is not None:
                channels = [c for c in channels if c.platform == platform]
            channels.sort(key=lambda c: c.created_at, reverse=True)
            return channels[offset : offset + limit]
        clauses: list[str] = []
        params: list[Any] = []
        if platform is not None:
            clauses.append("platform = %s")
            params.append(platform)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM channels{where} "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params,
            ).fetchall()
        return [self._row_to_channel(row) for row in rows]

    def delete(self, channel_id: str) -> bool:
        if self._pool is None:
            with self._lock:
                channel = self._channels.pop(channel_id, None)
                if channel is None:
                    return False
                self._native_index.pop((channel.platform, channel.native_key), None)
                return True
        with self._pool.connection() as conn:
            row = conn.execute(
                "DELETE FROM channels WHERE channel_id = %s RETURNING channel_id",
                (channel_id,),
            ).fetchone()
        return row is not None

    def purge_pending_channels(self) -> int:
        """Delete all ``pending`` channels. Called at startup: a pending channel
        only exists during an active login session, which can't survive a restart."""
        if self._pool is None:
            with self._lock:
                stale = [c.channel_id for c in self._channels.values() if c.status == STATUS_CHANNEL_PENDING]
                for cid in stale:
                    self.delete(cid)
                return len(stale)
        with self._pool.connection() as conn:
            rows = conn.execute(
                "DELETE FROM channels WHERE status = %s RETURNING channel_id",
                (STATUS_CHANNEL_PENDING,),
            ).fetchall()
        return len(rows)

    # ------------------------------------------------------------------
    # cookie helpers (the seam the publish kernel consumes)
    # ------------------------------------------------------------------
    def has_valid_cookie(self, channel_id: str) -> bool:
        channel = self.get(channel_id)
        if channel is None:
            return False
        cookies = (channel.cookie or {}).get("cookies") or []
        if not cookies:
            return False
        config = registry.config_for(channel.platform)
        if config is None:
            return False
        return config.has_login_cookie(cookies)

    def cookie_text(self, channel_id: str) -> str:
        """The channel cookie as a JSON string (what the publish kernel ingests)."""
        channel = self.get(channel_id)
        if channel is None:
            raise KeyError(channel_id)
        return json.dumps(channel.cookie or {"cookies": [], "origins": []}, ensure_ascii=False)

    def ready(self) -> bool:
        if self._pool is None:
            return True
        with self._pool.connection() as conn:
            conn.execute("SELECT 1")
        return True

    # ------------------------------------------------------------------
    # PostgreSQL internals
    # ------------------------------------------------------------------
    def _connect_pool(self, dsn: str):
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - dependency missing
            raise RuntimeError(
                "PGSQL_DSN is configured, but the `psycopg[binary,pool]` package is not installed"
            ) from exc

        pool = ConnectionPool(
            dsn,
            min_size=PGSQL_POOL_MIN,
            max_size=PGSQL_POOL_MAX,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        return pool

    def _ensure_schema(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(_SCHEMA_DDL)

    def _json(self, value: dict[str, Any]):
        from psycopg.types.json import Jsonb

        return Jsonb(value or {})

    def _update_pg(self, channel_id: str, **changes) -> Channel:
        set_parts: list[str] = ["updated_at = now()"]
        params: list[Any] = []
        for key, value in changes.items():
            if key in _TEXT_COLUMNS:
                set_parts.append(f"{key} = %s")
                params.append(value)
            elif key in _JSON_COLUMNS:
                set_parts.append(f"{key} = %s")
                params.append(self._json(value))
        params.append(channel_id)
        with self._pool.connection() as conn:
            row = conn.execute(
                f"UPDATE channels SET {', '.join(set_parts)} WHERE channel_id = %s "
                f"RETURNING {_SELECT_COLUMNS}",
                params,
            ).fetchone()
        if row is None:
            raise KeyError(channel_id)
        return self._row_to_channel(row)

    def _row_to_channel(self, row: dict[str, Any]) -> Channel:
        created_at = row.get("created_at")
        updated_at = row.get("updated_at")
        return Channel(
            channel_id=row["channel_id"],
            platform=row.get("platform") or "",
            status=row.get("status") or STATUS_CHANNEL_PENDING,
            account_name=row.get("account_name") or "",
            native_key=row.get("native_key") or "",
            cookie=row.get("cookie") or {},
            metadata=row.get("metadata") or {},
            created_at=created_at.isoformat() if created_at is not None else "",
            updated_at=updated_at.isoformat() if updated_at is not None else "",
        )

    # ------------------------------------------------------------------
    # in-memory fallback internals
    # ------------------------------------------------------------------
    def _update_memory(self, channel_id: str, **changes) -> Channel:
        with self._lock:
            channel = self._channels[channel_id]
            old_native = (channel.platform, channel.native_key)
            for key, value in changes.items():
                setattr(channel, key, value)
            channel.touch()
            if old_native != (channel.platform, channel.native_key):
                self._native_index.pop(old_native, None)
            if channel.native_key:
                self._native_index[(channel.platform, channel.native_key)] = channel_id
            return channel
