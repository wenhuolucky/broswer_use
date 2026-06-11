from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from threading import RLock
from typing import Any

from app.core.config import REDIS_JOB_TTL_SECONDS, REDIS_KEY_PREFIX, REDIS_URL
from app.jobs.models import Job, STATUS_QUEUED


class JobStore:
    """Job registry backed by Redis when REDIS_URL is configured."""

    def __init__(
        self,
        redis_url: str = REDIS_URL,
        key_prefix: str = REDIS_KEY_PREFIX,
        ttl_seconds: int = REDIS_JOB_TTL_SECONDS,
    ):
        self.key_prefix = key_prefix.rstrip(":")
        self.ttl_seconds = ttl_seconds
        self._redis = self._connect_redis(redis_url) if redis_url else None
        self._jobs: dict[str, Job] = {}
        self._remote_session_index: dict[str, str] = {}
        self._lock = RLock()

    def create(self, payload: dict[str, Any]) -> Job:
        job = Job(job_id=str(uuid.uuid4()), status=STATUS_QUEUED, payload=payload)
        if self._redis:
            self._save_redis_job(job)
            return job
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        if self._redis:
            raw = self._redis.get(self._job_key(job_id))
            if raw is None:
                return None
            return self._deserialize_job(raw)
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **changes) -> Job:
        if self._redis:
            job = self.get(job_id)
            if job is None:
                raise KeyError(job_id)
            old_remote_session_id = job.remote_session_id
            for key, value in changes.items():
                setattr(job, key, value)
            job.touch()
            self._save_redis_job(job)
            self._sync_remote_session_index(job, old_remote_session_id)
            return job
        with self._lock:
            job = self._jobs[job_id]
            old_remote_session_id = job.remote_session_id
            for key, value in changes.items():
                setattr(job, key, value)
            job.touch()
            self._sync_memory_remote_session_index(job, old_remote_session_id)
            return job

    def find_by_remote_session(self, session_id: str) -> Job | None:
        if self._redis:
            job_id = self._redis.get(self._remote_session_key(session_id))
            if not job_id:
                return None
            return self.get(str(job_id))
        with self._lock:
            job_id = self._remote_session_index.get(session_id)
            return self._jobs.get(job_id) if job_id else None

    def list_by_statuses(self, statuses: set[str]) -> list[Job]:
        if self._redis:
            jobs: list[Job] = []
            for key in self._redis.scan_iter(match=f"{self.key_prefix}:jobs:*"):
                raw = self._redis.get(key)
                if not raw:
                    continue
                job = self._deserialize_job(raw)
                if job.status in statuses:
                    jobs.append(job)
            return jobs
        with self._lock:
            return [job for job in self._jobs.values() if job.status in statuses]

    def find_latest_by_user_platform_statuses(
        self,
        user_id: str,
        platform: str,
        statuses: set[str],
    ) -> Job | None:
        user_id = str(user_id or "")
        platform = str(platform or "")
        candidates = [
            job
            for job in self.list_by_statuses(statuses)
            if str((job.payload or {}).get("user_id", "")) == user_id
            and str((job.payload or {}).get("platform", "toutiao") or "toutiao") == platform
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda job: job.updated_at or job.created_at)

    def _connect_redis(self, redis_url: str):
        try:
            from redis import Redis
        except ImportError as exc:
            raise RuntimeError("REDIS_URL is configured, but package `redis` is not installed") from exc

        client = Redis.from_url(redis_url, decode_responses=True)
        client.ping()
        return client

    def _job_key(self, job_id: str) -> str:
        return f"{self.key_prefix}:jobs:{job_id}"

    def _remote_session_key(self, session_id: str) -> str:
        return f"{self.key_prefix}:remote_sessions:{session_id}"

    def _save_redis_job(self, job: Job) -> None:
        self._redis.set(self._job_key(job.job_id), self._serialize_job(job), ex=self.ttl_seconds)
        if job.remote_session_id:
            self._redis.set(self._remote_session_key(job.remote_session_id), job.job_id, ex=self.ttl_seconds)

    def _sync_remote_session_index(self, job: Job, old_remote_session_id: str) -> None:
        if old_remote_session_id and old_remote_session_id != job.remote_session_id:
            self._redis.delete(self._remote_session_key(old_remote_session_id))
        if job.remote_session_id:
            self._redis.set(self._remote_session_key(job.remote_session_id), job.job_id, ex=self.ttl_seconds)

    def _sync_memory_remote_session_index(self, job: Job, old_remote_session_id: str) -> None:
        if old_remote_session_id and old_remote_session_id != job.remote_session_id:
            self._remote_session_index.pop(old_remote_session_id, None)
        if job.remote_session_id:
            self._remote_session_index[job.remote_session_id] = job.job_id

    def _serialize_job(self, job: Job) -> str:
        return json.dumps(asdict(job), ensure_ascii=False)

    def _deserialize_job(self, raw: str | bytes) -> Job:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        return Job(**data)
