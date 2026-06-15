"""Shared PostgreSQL plumbing for the PG-backed stores (JobStore / ChannelStore).

Both stores follow the same shape: a connection pool when ``PGSQL_DSN`` is set,
an in-memory dict fallback otherwise. This mixin hosts the bits that were
byte-for-byte identical between them — pool creation, schema bootstrap, JSONB
wrapping, and the readiness probe. Subclasses set ``_SCHEMA_DDL`` (a class
attribute holding their CREATE TABLE block) and own a ``self._pool`` attribute
(``None`` in the in-memory fallback).
"""

from __future__ import annotations

from typing import Any

from app.core.config import PGSQL_POOL_MAX, PGSQL_POOL_MIN


class PgStoreMixin:
    _SCHEMA_DDL = ""

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
        # Validate connectivity eagerly so a misconfigured DSN fails fast at
        # startup rather than on the first request.
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        return pool

    def _ensure_schema(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(self._SCHEMA_DDL)

    def _json(self, value: dict[str, Any]):
        from psycopg.types.json import Jsonb

        return Jsonb(value or {})

    def ready(self) -> bool:
        """Lightweight readiness probe (in-memory mode is always ready)."""
        if self._pool is None:
            return True
        with self._pool.connection() as conn:
            conn.execute("SELECT 1")
        return True
