from __future__ import annotations

from app.domain.job import (
    STATUS_CHECKING_COOKIE,
    STATUS_FAILED,
    STATUS_PUBLISHING,
    STATUS_QUEUED,
    STATUS_SUCCEEDED,
    STATUS_WAITING_COOKIE,
)
from app.jobs.store import JobStore


def _create_publish_job(store: JobStore, channel_id: str, title: str, status: str):
    job = store.create({"channel_id": channel_id, "platform": "toutiao", "title": title})
    store.update(job.job_id, status=status)
    return job


def test_next_queued_publish_job_returns_oldest_queued_publish_job_for_channel():
    store = JobStore(path="")
    first = _create_publish_job(store, "channel-a", "first", STATUS_QUEUED)
    _create_publish_job(store, "channel-a", "running", STATUS_PUBLISHING)
    _create_publish_job(store, "channel-b", "other-channel", STATUS_QUEUED)
    _create_publish_job(store, "channel-a", "second", STATUS_QUEUED)
    store.create({"channel_id": "channel-a", "platform": "toutiao", "job_type": "login_only"})

    next_job = store.next_queued_publish_job("channel-a")

    assert next_job is not None
    assert next_job.job_id == first.job_id
    assert next_job.payload["title"] == "first"


def test_has_executing_publish_job_ignores_queued_terminal_and_login_jobs():
    store = JobStore(path="")
    _create_publish_job(store, "channel-a", "queued", STATUS_QUEUED)
    _create_publish_job(store, "channel-a", "done", STATUS_SUCCEEDED)
    login_job = store.create({"channel_id": "channel-a", "platform": "toutiao", "job_type": "login_only"})
    store.update(login_job.job_id, status=STATUS_WAITING_COOKIE)

    assert store.has_executing_publish_job("channel-a") is False

    _create_publish_job(store, "channel-a", "checking", STATUS_CHECKING_COOKIE)

    assert store.has_executing_publish_job("channel-a") is True


def test_count_unfinished_publish_jobs_counts_executing_and_queued_publish_jobs_only():
    store = JobStore(path="")
    _create_publish_job(store, "channel-a", "queued", STATUS_QUEUED)
    _create_publish_job(store, "channel-a", "publishing", STATUS_PUBLISHING)
    _create_publish_job(store, "channel-a", "waiting", STATUS_WAITING_COOKIE)
    _create_publish_job(store, "channel-a", "failed", STATUS_FAILED)
    _create_publish_job(store, "channel-b", "other", STATUS_QUEUED)
    login_job = store.create({"channel_id": "channel-a", "platform": "toutiao", "job_type": "login_only"})
    store.update(login_job.job_id, status=STATUS_WAITING_COOKIE)

    assert store.count_unfinished_publish_jobs("channel-a") == 3


def test_queued_publish_channel_ids_returns_distinct_channels_with_queued_publish_jobs():
    store = JobStore(path="")
    _create_publish_job(store, "channel-b", "queued-b", STATUS_QUEUED)
    _create_publish_job(store, "channel-a", "queued-a1", STATUS_QUEUED)
    _create_publish_job(store, "channel-a", "queued-a2", STATUS_QUEUED)
    _create_publish_job(store, "channel-c", "running-c", STATUS_PUBLISHING)
    login_job = store.create({"channel_id": "channel-d", "platform": "toutiao", "job_type": "login_only"})
    store.update(login_job.job_id, status=STATUS_QUEUED)

    assert store.queued_publish_channel_ids() == ["channel-a", "channel-b"]


def test_publish_queue_helpers_work_with_sqlite_store(tmp_path):
    store = JobStore(path=str(tmp_path / "jobs.sqlite3"))
    first = _create_publish_job(store, "channel-a", "first", STATUS_QUEUED)
    _create_publish_job(store, "channel-a", "second", STATUS_QUEUED)
    _create_publish_job(store, "channel-a", "running", STATUS_PUBLISHING)
    _create_publish_job(store, "channel-b", "other", STATUS_QUEUED)

    assert store.next_queued_publish_job("channel-a").job_id == first.job_id
    assert store.has_executing_publish_job("channel-a") is True
    assert store.count_unfinished_publish_jobs("channel-a") == 3
    assert store.queued_publish_channel_ids() == ["channel-a", "channel-b"]
