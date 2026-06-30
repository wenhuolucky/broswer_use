from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.accounts.models import ArticleAccount, STATUS_DISABLED, STATUS_NORMAL, STATUS_WARNING
from app.accounts.service import AccountService
from app.api.v1.accounts import get_account_service
from app.core.auth import require_api_token
from app.domain.channel import Channel
from app.domain.job import AutoPublishResponse, Job, STATUS_CANCELLED, STATUS_FAILED, STATUS_PUBLISHING, STATUS_QUEUED
from app.server import app


@dataclass
class FakeStore:
    account: ArticleAccount
    existing_new_phone: bool = False

    def list_accounts(self, platform: str | None = None, limit: int = 100, offset: int = 0):
        if platform and platform != self.account.platform:
            return []
        return [self.account][offset : offset + limit]

    def count_accounts(self, platform: str | None = None):
        return len(self.list_accounts(platform=platform))

    def get_account(self, platform: str, phone: str):
        if platform == self.account.platform and phone == self.account.phone:
            return self.account
        return None

    def find_by_channel(self, channel: str):
        if channel == self.account.channel:
            return self.account
        return None

    def update_status(
        self,
        platform: str,
        phone: str,
        *,
        status: str,
        consecutive_failures: int | None = None,
    ):
        account = self.get_account(platform, phone)
        if account is None:
            raise KeyError(phone)
        account.status = status
        if consecutive_failures is not None:
            account.consecutive_failures = consecutive_failures
        return account

    def rename_phone(self, platform: str, phone: str, new_phone: str):
        if self.existing_new_phone:
            raise ValueError("account_phone_exists")
        account = self.get_account(platform, phone)
        if account is None:
            raise KeyError(phone)
        account.phone = new_phone
        return account

    def delete_account(self, platform: str, phone: str):
        account = self.get_account(platform, phone)
        if account is None:
            return None
        return account


class FakeChannelStore:
    def __init__(self, channel: Channel):
        self.channel = channel

    def get(self, channel_id: str):
        if channel_id == self.channel.channel_id:
            return self.channel
        return None

    def has_valid_cookie(self, channel_id: str):
        return channel_id == self.channel.channel_id


class FakeJobStore:
    def __init__(self):
        self.jobs = [
            Job(
                job_id="running-job",
                status=STATUS_PUBLISHING,
                payload={"channel_id": "channel-1", "platform": "toutiao", "title": "当前文章"},
                type="publish",
                live_url="http://example.test/view",
                created_at="2026-06-30T08:00:00+00:00",
                updated_at="2026-06-30T08:01:00+00:00",
            ),
            Job(
                job_id="queued-job",
                status=STATUS_QUEUED,
                payload={"channel_id": "channel-1", "platform": "toutiao", "title": "排队文章"},
                type="publish",
                created_at="2026-06-30T08:02:00+00:00",
                updated_at="2026-06-30T08:02:00+00:00",
            ),
        ]

    def list_jobs(
        self,
        *,
        channel_id: str | None = None,
        statuses: set[str] | None = None,
        job_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        jobs = list(self.jobs)
        if channel_id is not None:
            jobs = [job for job in jobs if (job.payload or {}).get("channel_id") == channel_id]
        if statuses:
            jobs = [job for job in jobs if job.status in statuses]
        if job_type is not None:
            jobs = [job for job in jobs if job.type == job_type]
        return jobs[offset : offset + limit]

    def count_unfinished_publish_jobs(self, channel_id: str):
        return len(
            self.list_jobs(
                channel_id=channel_id,
                statuses={STATUS_PUBLISHING, STATUS_QUEUED},
                job_type="publish",
            )
        )

    def get(self, job_id: str):
        for job in self.jobs:
            if job.job_id == job_id:
                return job
        return None

    def update(self, job_id: str, **changes):
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        for key, value in changes.items():
            setattr(job, key, value)
        return job


class FakeAgent:
    def __init__(self):
        self.channel_store = FakeChannelStore(
            Channel(
                channel_id="channel-1",
                platform="toutiao",
                status="bound",
                account_name="账号名称",
                cookie={"cookies": [{"name": "uid_tt", "value": "abc"}]},
                created_at="2026-06-30T08:00:00+00:00",
                updated_at="2026-06-30T08:01:00+00:00",
            )
        )
        self.job_store = FakeJobStore()
        self.deleted_channels: list[str] = []

    def get_channel(self, channel_id: str):
        return self.channel_store.get(channel_id)

    def count_unfinished_publish_jobs_for_channel(self, channel_id: str):
        return self.job_store.count_unfinished_publish_jobs(channel_id)

    async def cancel_job(self, job_id: str):
        job = self.job_store.get(job_id)
        if job is None:
            return AutoPublishResponse(code=404, job_id=job_id, status=STATUS_FAILED, message="not found")
        self.job_store.update(job_id, status=STATUS_CANCELLED)
        return AutoPublishResponse(code=200, job_id=job_id, status=STATUS_CANCELLED, message="cancelled")

    def delete_channel(self, channel_id: str):
        self.deleted_channels.append(channel_id)
        return channel_id == "channel-1"


@pytest.fixture
def client():
    account = ArticleAccount(
        id=1,
        platform="toutiao",
        phone="13800138000",
        channel="channel-1",
        status=STATUS_NORMAL,
        consecutive_failures=0,
        group_id="tenant-a",
        group_text="Tenant A",
        created_at="2026-06-30T08:00:00+00:00",
        updated_at="2026-06-30T08:00:00+00:00",
    )
    store = FakeStore(account)
    agent = FakeAgent()
    service = AccountService(
        store=store,
        agent=agent,
        group_id="tenant-a",
        group_text="Tenant A",
    )
    app.dependency_overrides[get_account_service] = lambda: service
    app.dependency_overrides[require_api_token] = lambda: "test-token"
    try:
        test_client = TestClient(app)
        test_client.store = store
        test_client.agent = agent
        yield test_client
    finally:
        app.dependency_overrides.clear()


def test_list_accounts_includes_runtime_status(client: TestClient):
    response = client.get("/api/v1/accounts")

    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    assert data["group_id"] == "tenant-a"
    assert data["count"] == 1
    account = data["accounts"][0]
    assert account["phone"] == "13800138000"
    assert account["phone_masked"] == "138****8000"
    assert account["availability"] == "busy"
    assert account["runtime"]["account_status"] == "publishing"
    assert account["runtime"]["publish_status"] == "publishing"
    assert account["runtime"]["publish_count"] == 2


def test_account_publish_status_shows_running_and_queued_jobs(client: TestClient):
    response = client.get("/api/v1/accounts/toutiao/13800138000/publish-status")

    assert response.status_code == 200
    data = response.json()
    assert data["account_status"] == "publishing"
    assert data["running_job"]["job_id"] == "running-job"
    assert data["queued_count"] == 1
    assert data["queued_jobs"][0]["job_id"] == "queued-job"


def test_disable_account_sets_persistent_status(client: TestClient):
    response = client.patch(
        "/api/v1/accounts/toutiao/13800138000/status",
        json={"status": "disabled", "reset_failures": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == STATUS_DISABLED


def test_rename_phone_conflict_returns_stable_error(client: TestClient):
    client.store.existing_new_phone = True

    response = client.patch(
        "/api/v1/accounts/toutiao/13800138000/phone",
        json={"new_phone": "13900139000"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "account_phone_exists"


def test_publish_rejects_busy_account(client: TestClient):
    response = client.post(
        "/api/v1/accounts/toutiao/13800138000/publish",
        json={"title": "标题", "content": "正文"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "account_busy"


def test_terminal_failed_job_updates_account_once(client: TestClient):
    client.agent.job_store.jobs = [
        Job(
            job_id="failed-job",
            status=STATUS_FAILED,
            payload={"channel_id": "channel-1", "platform": "toutiao", "title": "失败文章"},
            type="publish",
            error="平台发布失败",
        )
    ]
    client.store.account.status = STATUS_NORMAL
    client.store.account.consecutive_failures = 4

    response = client.get("/api/v1/accounts/jobs/failed-job")

    assert response.status_code == 200
    data = response.json()
    assert data["account_update"]["applied"] is True
    assert data["account_update"]["new_status"] == STATUS_WARNING
    assert client.store.account.status == STATUS_WARNING
    assert client.store.account.consecutive_failures == 5
    assert client.agent.job_store.get("failed-job").payload["account_status_applied"] is True


def test_delete_account_force_cancels_jobs_and_deletes_channel(client: TestClient):
    response = client.delete("/api/v1/accounts/toutiao/13800138000?force=true")

    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] is True
    assert data["channel_deleted"] is True
    assert set(data["cancelled_jobs"]) == {"running-job", "queued-job"}
    assert client.agent.deleted_channels == ["channel-1"]
