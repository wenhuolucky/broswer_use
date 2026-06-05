from __future__ import annotations

import uuid
from threading import RLock
from typing import Any

from auto.models import Job, STATUS_QUEUED


class JobStore:
    """In-memory job registry for the auto service."""

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = RLock()

    def create(self, payload: dict[str, Any]) -> Job:
        job = Job(job_id=str(uuid.uuid4()), status=STATUS_QUEUED, payload=payload)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **changes) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)
            job.touch()
            return job

    def find_by_remote_session(self, session_id: str) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if job.remote_session_id == session_id:
                    return job
        return None
