"""In-memory queue and job store for phase 1."""

from __future__ import annotations

import asyncio
import uuid

from api.publish_docker.app.models import PublishJob
from api.publish_docker.app.settings import settings


class JobStore:
    def __init__(self):
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.jobs: dict[str, PublishJob] = {}
        self.worker_state = "idle"

    def create_job(self, **kwargs) -> PublishJob:
        job = PublishJob(job_id=str(uuid.uuid4()), **kwargs)
        self.jobs[job.job_id] = job
        self.queue.put_nowait(job.job_id)
        return job

    def get_job(self, job_id: str) -> PublishJob | None:
        return self.jobs.get(job_id)

    def can_accept(self) -> bool:
        return self.queue.qsize() < settings.queue_max_size


store = JobStore()
