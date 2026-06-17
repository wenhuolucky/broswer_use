from __future__ import annotations

import uuid
from threading import RLock
from typing import Any

from app.core.config import SQLITE_PATH
from app.core.sqlite_store import SqliteStoreMixin, now_iso
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
    payload           TEXT NOT NULL DEFAULT '{}',
    result            TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_remote_session ON jobs (remote_session_id) WHERE remote_session_id <> '';
CREATE INDEX IF NOT EXISTS idx_jobs_channel_created ON jobs (channel_id, created_at DESC);
"""

# Columns selected and mapped back to Job (order independent thanks to dict keys).
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


class JobStore(SqliteStoreMixin):
    """Job registry backed by SQLite when SQLITE_PATH is configured.

    Falls back to an in-memory dict when no path is set (local dev / tests).
    Records are durable (no TTL) — the table doubles as the publish/login
    business log. The public interface is intentionally synchronous: it is
    called synchronously from PublishAgent across ~30 sites.
    """

    _SCHEMA_DDL = _SCHEMA_DDL

    def __init__(self, path: str = SQLITE_PATH):
        self._lock = RLock()
        self._conn = self._connect(path) if path else None
        # In-memory fallback state (only used when no path is configured).
        self._jobs: dict[str, Job] = {}
        self._remote_session_index: dict[str, str] = {}
        if self._conn is not None:
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

        if self._conn is None:
            job = Job(
                job_id=job_id,
                status=STATUS_QUEUED,
                payload=payload,
                type=job_type,
            )
            with self._lock:
                self._jobs[job_id] = job
            return job

        ts = now_iso()
        with self._lock:
            row = self._conn.execute(
                f"""
                INSERT INTO jobs (job_id, type, channel_id, platform, title, status, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING {_SELECT_COLUMNS}
                """,
                (job_id, job_type, channel_id, platform, title, STATUS_QUEUED, self._json(payload), ts, ts),
            ).fetchone()
        return self._row_to_job(row)

    def get(self, job_id: str) -> Job | None:
        if self._conn is None:
            with self._lock:
                return self._jobs.get(job_id)
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def update(self, job_id: str, **changes) -> Job:
        if self._conn is None:
            return self._update_memory(job_id, **changes)
        return self._update_db(job_id, **changes)

    def find_by_remote_session(self, session_id: str) -> Job | None:
        if not session_id:
            return None
        if self._conn is None:
            with self._lock:
                mapped = self._remote_session_index.get(session_id)
                return self._jobs.get(mapped) if mapped else None
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM jobs "
                "WHERE remote_session_id = ? ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def list_by_statuses(self, statuses: set[str]) -> list[Job]:
        statuses = set(statuses or set())
        if self._conn is None:
            with self._lock:
                return [job for job in self._jobs.values() if job.status in statuses]
        if not statuses:
            return []
        placeholders = ", ".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM jobs WHERE status IN ({placeholders})",
                tuple(statuses),
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
        if self._conn is None:
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
            clauses.append("channel_id = ?")
            params.append(channel_id)
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        if job_type is not None:
            clauses.append("type = ?")
            params.append(job_type)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM jobs{where} "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    # ------------------------------------------------------------------
    # SQLite internals
    # ------------------------------------------------------------------
    def _update_db(self, job_id: str, **changes) -> Job:
        set_parts: list[str] = ["updated_at = ?"]
        params: list[Any] = [now_iso()]

        for key, value in changes.items():
            if key in _TEXT_COLUMNS:
                set_parts.append(f"{key} = ?")
                params.append(value)
            elif key == "payload":
                payload = value or {}
                set_parts.append("payload = ?")
                params.append(self._json(payload))
                # keep derived/queryable columns in sync with the payload
                set_parts.append("type = ?")
                params.append(_job_type_from_payload(payload))
                set_parts.append("channel_id = ?")
                params.append(str(payload.get("channel_id", "") or ""))
                set_parts.append("platform = ?")
                params.append(str(payload.get("platform", "") or ""))
                set_parts.append("title = ?")
                params.append(str(payload.get("title", "") or ""))
            elif key == "result":
                # `result` is set alone (without payload), so read platform from the
                # row to derive the normalized article_url column.
                set_parts.append("result = ?")
                params.append(self._json(value or {}))

        # 单连接 + self._lock 串行化：读 platform 与写 UPDATE 之间无并发，等价于原先
        # PG 的 SELECT ... FOR UPDATE 事务，故无需显式行锁。
        with self._lock:
            if "result" in changes and "article_url" not in changes:
                meta = self._conn.execute(
                    "SELECT platform FROM jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if meta is None:
                    raise KeyError(job_id)
                article_url = _derive_article_url(
                    str(meta["platform"] or ""),
                    changes["result"] or {},
                )
                set_parts.append("article_url = ?")
                params.append(article_url)

            params.append(job_id)
            row = self._conn.execute(
                f"UPDATE jobs SET {', '.join(set_parts)} WHERE job_id = ? "
                f"RETURNING {_SELECT_COLUMNS}",
                params,
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._row_to_job(row)

    def _row_to_job(self, row: Any) -> Job:
        return Job(
            job_id=row["job_id"],
            status=row["status"],
            payload=self._loads(row["payload"]),
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
            login_url=row["login_url"] or "",
            remote_session_id=row["remote_session_id"] or "",
            live_url=row["live_url"] or "",
            log_file_path=row["log_file_path"] or "",
            result=self._loads(row["result"]),
            error=row["error"] or "",
            type=row["type"] or "publish",
            article_url=row["article_url"] or "",
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
