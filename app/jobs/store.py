from __future__ import annotations

import uuid
from threading import RLock
from typing import Any

from app.core.config import PGSQL_DSN
from app.core.pg_store import PgStoreMixin
from app.domain.job import Job, STATUS_QUEUED
from app.utils.urls import normalize_article_url


# Columns that map 1:1 to a Job text attribute and can be SET directly.
_TEXT_COLUMNS = {
    "status",
    "login_url",
    "live_url",
    "remote_session_id",
    "log_file_path",
    "error",
    "article_url",
    "type",
}

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id            TEXT PRIMARY KEY,
    type              TEXT NOT NULL DEFAULT 'publish',
    channel_id        TEXT NOT NULL DEFAULT '',
    platform          TEXT NOT NULL DEFAULT '',
    title             TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL,
    remote_session_id TEXT NOT NULL DEFAULT '',
    login_url         TEXT NOT NULL DEFAULT '',
    live_url          TEXT NOT NULL DEFAULT '',
    article_url       TEXT NOT NULL DEFAULT '',
    error             TEXT NOT NULL DEFAULT '',
    log_file_path     TEXT NOT NULL DEFAULT '',
    payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    result            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_remote_session ON jobs (remote_session_id) WHERE remote_session_id <> '';
CREATE INDEX IF NOT EXISTS idx_jobs_channel_created ON jobs (channel_id, created_at DESC);
"""

# Columns selected and mapped back to Job (order independent thanks to dict_row).
_SELECT_COLUMNS = (
    "job_id, type, channel_id, platform, title, status, remote_session_id, "
    "login_url, live_url, article_url, error, log_file_path, payload, result, "
    "created_at, updated_at"
)


def _job_type_from_payload(payload: dict[str, Any]) -> str:
    return "login" if (payload or {}).get("job_type") == "login_only" else "publish"


def _derive_article_url(platform: str, result: dict[str, Any]) -> str:
    # 搜狐号数字 id 在发文内核侧已经应用过（见 sohu/kernel._parse_agent_outcome），
    # 到这里 article_url 已是终态，再以 account_id="" 归一化只是幂等 passthrough；
    # 头条归一化本就不需要 account id。故 JobStore 不必感知任何渠道/平台账号。
    raw = str((result or {}).get("article_url", "") or "")
    if not raw:
        return ""
    return normalize_article_url(platform, raw, account_id="")


class JobStore(PgStoreMixin):
    """Job registry backed by PostgreSQL when PGSQL_DSN is configured.

    Falls back to an in-memory dict when no DSN is set (local dev / tests).
    Records are durable (no TTL) — the table doubles as the publish/login
    business log. The public interface is intentionally synchronous: it is
    called synchronously from PublishAgent across ~30 sites.
    """

    _SCHEMA_DDL = _SCHEMA_DDL

    def __init__(self, dsn: str = PGSQL_DSN):
        self._pool = self._connect_pool(dsn) if dsn else None
        # In-memory fallback state (only used when no DSN is configured).
        self._jobs: dict[str, Job] = {}
        self._remote_session_index: dict[str, str] = {}
        self._lock = RLock()
        if self._pool is not None:
            self._ensure_schema()

    # ------------------------------------------------------------------
    # public API (synchronous)
    # ------------------------------------------------------------------
    def create(self, payload: dict[str, Any]) -> Job:
        job_id = uuid.uuid4().hex  # 无连字符 32 位，与 channel_id 一致，也用作日志文件名
        payload = payload or {}
        job_type = _job_type_from_payload(payload)
        channel_id = str(payload.get("channel_id", "") or "")
        platform = str(payload.get("platform", "") or "")
        title = str(payload.get("title", "") or "")

        if self._pool is None:
            job = Job(
                job_id=job_id,
                status=STATUS_QUEUED,
                payload=payload,
                type=job_type,
            )
            with self._lock:
                self._jobs[job_id] = job
            return job

        with self._pool.connection() as conn:
            row = conn.execute(
                f"""
                INSERT INTO jobs (job_id, type, channel_id, platform, title, status, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING {_SELECT_COLUMNS}
                """,
                (job_id, job_type, channel_id, platform, title, STATUS_QUEUED, self._json(payload)),
            ).fetchone()
        return self._row_to_job(row)

    def get(self, job_id: str) -> Job | None:
        if self._pool is None:
            with self._lock:
                return self._jobs.get(job_id)
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM jobs WHERE job_id = %s",
                (job_id,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def update(self, job_id: str, **changes) -> Job:
        if self._pool is None:
            return self._update_memory(job_id, **changes)
        return self._update_pg(job_id, **changes)

    def find_by_remote_session(self, session_id: str) -> Job | None:
        if not session_id:
            return None
        if self._pool is None:
            with self._lock:
                mapped = self._remote_session_index.get(session_id)
                return self._jobs.get(mapped) if mapped else None
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM jobs "
                "WHERE remote_session_id = %s ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def list_by_statuses(self, statuses: set[str]) -> list[Job]:
        statuses = set(statuses or set())
        if self._pool is None:
            with self._lock:
                return [job for job in self._jobs.values() if job.status in statuses]
        if not statuses:
            return []
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM jobs WHERE status = ANY(%s)",
                (list(statuses),),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def list_jobs(
        self,
        *,
        channel_id: str | None = None,
        statuses: set[str] | None = None,
        job_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Job]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        if self._pool is None:
            with self._lock:
                jobs = list(self._jobs.values())
            if channel_id is not None:
                jobs = [j for j in jobs if (j.payload or {}).get("channel_id") == channel_id]
            if statuses:
                jobs = [j for j in jobs if j.status in statuses]
            if job_type is not None:
                jobs = [j for j in jobs if j.type == job_type]
            jobs.sort(key=lambda j: j.created_at, reverse=True)
            return jobs[offset : offset + limit]

        clauses: list[str] = []
        params: list[Any] = []
        if channel_id is not None:
            clauses.append("channel_id = %s")
            params.append(channel_id)
        if statuses:
            clauses.append("status = ANY(%s)")
            params.append(list(statuses))
        if job_type is not None:
            clauses.append("type = %s")
            params.append(job_type)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM jobs{where} "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params,
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    # ------------------------------------------------------------------
    # PostgreSQL internals
    # ------------------------------------------------------------------
    def _update_pg(self, job_id: str, **changes) -> Job:
        set_parts: list[str] = ["updated_at = now()"]
        params: list[Any] = []

        for key, value in changes.items():
            if key in _TEXT_COLUMNS:
                set_parts.append(f"{key} = %s")
                params.append(value)
            elif key == "payload":
                payload = value or {}
                set_parts.append("payload = %s")
                params.append(self._json(payload))
                # keep derived/queryable columns in sync with the payload
                set_parts.append("type = %s")
                params.append(_job_type_from_payload(payload))
                set_parts.append("channel_id = %s")
                params.append(str(payload.get("channel_id", "") or ""))
                set_parts.append("platform = %s")
                params.append(str(payload.get("platform", "") or ""))
                set_parts.append("title = %s")
                params.append(str(payload.get("title", "") or ""))
            elif key == "result":
                # `result` is set alone (without payload), so read platform/user_id
                # from the row to derive the normalized article_url column.
                result = value or {}
                set_parts.append("result = %s")
                params.append(self._json(result))

        with self._pool.connection() as conn, conn.transaction():
            if "result" in changes and "article_url" not in changes:
                meta = conn.execute(
                    "SELECT platform FROM jobs WHERE job_id = %s FOR UPDATE",
                    (job_id,),
                ).fetchone()
                if meta is None:
                    raise KeyError(job_id)
                article_url = _derive_article_url(
                    str(meta["platform"] or ""),
                    changes["result"] or {},
                )
                set_parts.append("article_url = %s")
                params.append(article_url)

            params.append(job_id)
            row = conn.execute(
                f"UPDATE jobs SET {', '.join(set_parts)} WHERE job_id = %s "
                f"RETURNING {_SELECT_COLUMNS}",
                params,
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._row_to_job(row)

    def _row_to_job(self, row: dict[str, Any]) -> Job:
        created_at = row.get("created_at")
        updated_at = row.get("updated_at")
        return Job(
            job_id=row["job_id"],
            status=row["status"],
            payload=row.get("payload") or {},
            created_at=created_at.isoformat() if created_at is not None else "",
            updated_at=updated_at.isoformat() if updated_at is not None else "",
            login_url=row.get("login_url") or "",
            remote_session_id=row.get("remote_session_id") or "",
            live_url=row.get("live_url") or "",
            log_file_path=row.get("log_file_path") or "",
            result=row.get("result") or {},
            error=row.get("error") or "",
            type=row.get("type") or "publish",
            article_url=row.get("article_url") or "",
        )

    # ------------------------------------------------------------------
    # in-memory fallback internals
    # ------------------------------------------------------------------
    def _update_memory(self, job_id: str, **changes) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            old_remote_session_id = job.remote_session_id
            for key, value in changes.items():
                setattr(job, key, value)
            if "payload" in changes:
                job.type = _job_type_from_payload(job.payload)
            if "result" in changes and "article_url" not in changes:
                job.article_url = _derive_article_url(
                    str((job.payload or {}).get("platform", "") or ""),
                    job.result or {},
                )
            job.touch()
            self._sync_memory_remote_session_index(job, old_remote_session_id)
            return job

    def _sync_memory_remote_session_index(self, job: Job, old_remote_session_id: str) -> None:
        if old_remote_session_id and old_remote_session_id != job.remote_session_id:
            self._remote_session_index.pop(old_remote_session_id, None)
        if job.remote_session_id:
            self._remote_session_index[job.remote_session_id] = job.job_id
