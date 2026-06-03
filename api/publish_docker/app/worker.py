"""Single background worker for publish_docker phase 1."""

from __future__ import annotations

from datetime import UTC, datetime

from api.publish_docker.app.queue_store import store
from api.publish.publish_service import PublishService


async def process_single_job(job_id: str):
    job = store.get_job(job_id)
    if job is None:
        return

    store.worker_state = "busy"
    job.status = "running"
    job.started_at = datetime.now(UTC).isoformat()

    try:
        publisher = PublishService()
        result = await publisher.publish(
            title=job.title,
            content=job.content,
            cookie=job.cookie_text,
            request_id=job.job_id,
            cover_image_path=job.cover_image_path,
            cover_image_url=job.cover_image_url,
        )
        job.result = result
        job.status = "succeeded" if result.get("success") else "failed"
        job.error = "" if result.get("success") else result.get("failure_reason", "")
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.result = {}
    finally:
        job.finished_at = datetime.now(UTC).isoformat()
        store.worker_state = "idle"


async def worker_loop():
    while True:
        job_id = await store.queue.get()
        try:
            await process_single_job(job_id)
        finally:
            store.queue.task_done()
