from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1 import channels as channels_route
from app.domain.channel import Channel, STATUS_CHANNEL_BOUND
from app.domain.job import (
    Job,
    STATUS_FAILED,
    STATUS_PUBLISHING,
    STATUS_QUEUED,
    STATUS_SUCCEEDED,
    STATUS_WAITING_COOKIE,
)


class FakeAgent:
    def __init__(self, channel: Channel | None, publish_count: int = 0):
        self.channel = channel
        self.publish_count = publish_count

    def get_channel(self, channel_id: str):
        return self.channel if self.channel and self.channel.channel_id == channel_id else None

    def count_unfinished_publish_jobs_for_channel(self, channel_id: str) -> int:
        return self.publish_count if self.channel and self.channel.channel_id == channel_id else 0


def make_app(fake_agent: FakeAgent, monkeypatch) -> FastAPI:
    monkeypatch.setattr(deps, "agent", fake_agent)
    monkeypatch.setattr(channels_route, "agent", fake_agent)
    app = FastAPI()
    app.include_router(channels_route.router)
    return app


def make_channel(channel_id: str = "channel123") -> Channel:
    return Channel(
        channel_id=channel_id,
        platform="toutiao",
        status=STATUS_CHANNEL_BOUND,
        account_name="账号名称",
    )


def make_job(channel_id: str, status: str, job_type: str = "publish", title: str = "测试标题") -> Job:
    return Job(
        job_id=f"{status}-job",
        status=status,
        payload={"channel_id": channel_id, "platform": "toutiao", "title": title},
        type=job_type,
        created_at="2026-06-29T08:00:00+00:00",
        updated_at="2026-06-29T08:01:00+00:00",
    )


def make_agent_with_channel(channel: Channel):
    from app.channels.store import ChannelStore
    from app.jobs.store import JobStore
    from app.publishing.orchestrator import PublishAgent

    agent = PublishAgent(job_store=JobStore(path=""), channel_store=ChannelStore(path=""), remote_runner=object())
    agent.channel_store._channels[channel.channel_id] = channel
    return agent


def test_channel_publish_status_returns_idle_when_no_active_publish_job(monkeypatch):
    channel = make_channel()
    client = TestClient(make_app(FakeAgent(channel, publish_count=0), monkeypatch))

    response = client.get("/channels/channel123/publish-status")

    assert response.status_code == 200
    assert response.json() == {
        "channel_id": "channel123",
        "account_status": "idle",
        "publish_count": 0,
    }


def test_channel_publish_status_returns_publishing_with_unfinished_publish_count(monkeypatch):
    channel = make_channel()
    client = TestClient(make_app(FakeAgent(channel, publish_count=3), monkeypatch))

    response = client.get("/channels/channel123/publish-status")

    assert response.status_code == 200
    assert response.json() == {
        "channel_id": "channel123",
        "account_status": "publishing",
        "publish_count": 3,
    }


def test_channel_publish_status_treats_waiting_cookie_publish_job_as_publishing(monkeypatch):
    channel = make_channel()
    client = TestClient(make_app(FakeAgent(channel, publish_count=1), monkeypatch))

    response = client.get("/channels/channel123/publish-status")

    assert response.status_code == 200
    assert response.json()["account_status"] == "publishing"
    assert response.json()["publish_count"] == 1


def test_channel_publish_status_count_ignores_terminal_and_login_jobs_via_agent_helper():
    channel = make_channel()
    agent = make_agent_with_channel(channel)
    succeeded = agent.job_store.create(
        {"channel_id": channel.channel_id, "platform": "toutiao", "title": "已成功"}
    )
    agent.job_store.update(succeeded.job_id, status=STATUS_SUCCEEDED)
    failed = agent.job_store.create(
        {"channel_id": channel.channel_id, "platform": "toutiao", "title": "已失败"}
    )
    agent.job_store.update(failed.job_id, status=STATUS_FAILED)
    login_job = agent.job_store.create(
        {"channel_id": channel.channel_id, "platform": "toutiao", "job_type": "login_only"}
    )
    agent.job_store.update(login_job.job_id, status=STATUS_WAITING_COOKIE)

    assert agent.count_unfinished_publish_jobs_for_channel(channel.channel_id) == 0


def test_channel_publish_status_count_includes_executing_and_queued_publish_jobs():
    channel = make_channel()
    agent = make_agent_with_channel(channel)
    queued = agent.job_store.create({"channel_id": channel.channel_id, "platform": "toutiao", "title": "排队"})
    agent.job_store.update(queued.job_id, status=STATUS_QUEUED)
    waiting = agent.job_store.create({"channel_id": channel.channel_id, "platform": "toutiao", "title": "等待登录"})
    agent.job_store.update(waiting.job_id, status=STATUS_WAITING_COOKIE)
    publishing = agent.job_store.create({"channel_id": channel.channel_id, "platform": "toutiao", "title": "发布中"})
    agent.job_store.update(publishing.job_id, status=STATUS_PUBLISHING)

    assert agent.count_unfinished_publish_jobs_for_channel(channel.channel_id) == 3


def test_channel_publish_status_returns_404_for_missing_channel(monkeypatch):
    client = TestClient(make_app(FakeAgent(None), monkeypatch))

    response = client.get("/channels/missing/publish-status")

    assert response.status_code == 404
    assert response.json() == {"detail": "渠道不存在"}
