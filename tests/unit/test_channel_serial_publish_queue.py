from __future__ import annotations

import asyncio

import pytest

from app.domain.channel import Channel, STATUS_CHANNEL_BOUND
from app.domain.job import AutoPublishRequest, STATUS_CANCELLED, STATUS_CHECKING_COOKIE, STATUS_PUBLISHING, STATUS_QUEUED
from app.domain.job import STATUS_FAILED
from app.domain.job import STATUS_WAITING_COOKIE
from app.jobs.store import JobStore
from app.publishing.orchestrator import PublishAgent


class FakeChannelStore:
    def __init__(self, *channel_ids: str, platform: str = "toutiao"):
        self.channels = {
            channel_id: Channel(
                channel_id=channel_id,
                platform=platform,
                status=STATUS_CHANNEL_BOUND,
                cookie={"cookies": [{"name": "sessionid", "value": "ok"}], "origins": []},
            )
            for channel_id in channel_ids
        }

    def get(self, channel_id: str):
        return self.channels.get(channel_id)

    def has_valid_cookie(self, channel_id: str) -> bool:
        return channel_id in self.channels

    def cookie_text(self, channel_id: str) -> str:
        return "{}"


class BlockingPublishAdapter:
    def __init__(self):
        self.started: list[str] = []
        self.release = asyncio.Event()

    async def publish(self, **kwargs):
        self.started.append(kwargs["request_id"])
        await self.release.wait()
        return {"success": True, "article_url": "https://example.test/article"}


class ControlledPublishAdapter:
    def __init__(self):
        self.started: list[str] = []
        self._release_by_job: dict[str, asyncio.Event] = {}

    async def publish(self, **kwargs):
        job_id = kwargs["request_id"]
        self.started.append(job_id)
        event = asyncio.Event()
        self._release_by_job[job_id] = event
        await event.wait()
        return {"success": True, "article_url": f"https://example.test/{job_id}"}

    def release_job(self, job_id: str) -> None:
        self._release_by_job[job_id].set()


class LoginRequiredPublishAdapter:
    def __init__(self):
        self.started: list[str] = []

    async def publish(self, **kwargs):
        self.started.append(kwargs["request_id"])
        return {"success": False, "login_required": True, "failure_reason": "login required"}


class FakeRemoteSession:
    session_id = "remote-session-1"
    login_url = "http://127.0.0.1/login"


class FakeRemoteRunner:
    async def start(self, platform: str, channel_id: str):
        return FakeRemoteSession()

    async def cleanup(self, session_id: str) -> None:
        return None


def make_request(channel_id: str, title: str) -> AutoPublishRequest:
    return AutoPublishRequest(channel_id=channel_id, title=title, content="正文")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform", "title"),
    [
        ("toutiao", "短"),
        ("toutiao", "头" * 31),
        ("sohu", "短题"),
        ("sohu", "搜" * 73),
    ],
)
async def test_publish_rejects_platform_title_length_out_of_range(platform: str, title: str):
    agent = PublishAgent(
        job_store=JobStore(path=""),
        channel_store=FakeChannelStore("channel-a", platform=platform),
        publish_adapter=BlockingPublishAdapter(),
        remote_runner=object(),
    )

    response = await agent.submit(make_request("channel-a", title))

    assert response.code == 422
    assert response.message == "标题长度不符合要求"
    assert response.data["status"] == STATUS_FAILED
    assert response.data["error_detail"] == "标题长度不符合要求"
    assert agent.job_store.list_jobs() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform", "title"),
    [
        ("toutiao", "头" * 2),
        ("toutiao", "头" * 30),
        ("sohu", "搜" * 5),
        ("sohu", "搜" * 72),
    ],
)
async def test_publish_accepts_platform_title_length_boundaries(platform: str, title: str):
    adapter = BlockingPublishAdapter()
    agent = PublishAgent(
        job_store=JobStore(path=""),
        channel_store=FakeChannelStore("channel-a", platform=platform),
        publish_adapter=adapter,
        remote_runner=object(),
    )

    response = await agent.submit(make_request("channel-a", title))
    await asyncio.sleep(0)

    assert response.code == 200
    assert response.data["status"] in {STATUS_CHECKING_COOKIE, STATUS_PUBLISHING}
    assert len(agent.job_store.list_jobs()) == 1

    await wait_until(lambda: assert_started(adapter, [response.data["job_id"]]))
    adapter.release.set()
    await asyncio.gather(*list(agent._background_tasks))


async def wait_until(assertion, attempts: int = 20) -> None:
    last_error = None
    for _ in range(attempts):
        try:
            assertion()
            return
        except AssertionError as exc:
            last_error = exc
            await asyncio.sleep(0)
    if last_error is not None:
        raise last_error


@pytest.mark.asyncio
async def test_same_channel_second_publish_job_stays_queued_while_first_is_running():
    adapter = BlockingPublishAdapter()
    agent = PublishAgent(
        job_store=JobStore(path=""),
        channel_store=FakeChannelStore("channel-a"),
        publish_adapter=adapter,
        remote_runner=object(),
    )

    first = await agent.submit(make_request("channel-a", "first"))
    await asyncio.sleep(0)
    second = await agent.submit(make_request("channel-a", "second"))
    await asyncio.sleep(0)

    first_job = agent.job_store.get(first.data["job_id"])
    second_job = agent.job_store.get(second.data["job_id"])
    assert first_job.status == STATUS_PUBLISHING
    assert second_job.status == STATUS_QUEUED
    assert adapter.started == [first.data["job_id"]]

    adapter.release.set()
    await asyncio.gather(*list(agent._background_tasks))


@pytest.mark.asyncio
async def test_same_channel_next_queued_job_starts_after_current_job_succeeds():
    adapter = ControlledPublishAdapter()
    agent = PublishAgent(
        job_store=JobStore(path=""),
        channel_store=FakeChannelStore("channel-a"),
        publish_adapter=adapter,
        remote_runner=object(),
    )

    first = await agent.submit(make_request("channel-a", "first"))
    await asyncio.sleep(0)
    second = await agent.submit(make_request("channel-a", "second"))
    await asyncio.sleep(0)

    assert adapter.started == [first.data["job_id"]]
    assert agent.job_store.get(second.data["job_id"]).status == STATUS_QUEUED

    adapter.release_job(first.data["job_id"])

    await wait_until(lambda: assert_started(adapter, [first.data["job_id"], second.data["job_id"]]))
    assert agent.job_store.get(second.data["job_id"]).status == STATUS_PUBLISHING

    adapter.release_job(second.data["job_id"])
    await asyncio.gather(*list(agent._background_tasks))


@pytest.mark.asyncio
async def test_different_channels_can_start_publish_jobs_independently():
    adapter = BlockingPublishAdapter()
    agent = PublishAgent(
        job_store=JobStore(path=""),
        channel_store=FakeChannelStore("channel-a", "channel-b"),
        publish_adapter=adapter,
        remote_runner=object(),
    )

    first = await agent.submit(make_request("channel-a", "first"))
    second = await agent.submit(make_request("channel-b", "second"))
    await asyncio.sleep(0)

    assert agent.job_store.get(first.data["job_id"]).status == STATUS_PUBLISHING
    assert agent.job_store.get(second.data["job_id"]).status == STATUS_PUBLISHING
    assert set(adapter.started) == {first.data["job_id"], second.data["job_id"]}

    adapter.release.set()
    await asyncio.gather(*list(agent._background_tasks))


def assert_started(adapter, expected: list[str]) -> None:
    assert adapter.started == expected


@pytest.mark.asyncio
async def test_cancelling_running_job_starts_next_queued_job_for_same_channel():
    adapter = BlockingPublishAdapter()
    agent = PublishAgent(
        job_store=JobStore(path=""),
        channel_store=FakeChannelStore("channel-a"),
        publish_adapter=adapter,
        remote_runner=object(),
    )

    first = await agent.submit(make_request("channel-a", "first"))
    await asyncio.sleep(0)
    second = await agent.submit(make_request("channel-a", "second"))
    await asyncio.sleep(0)

    response = await agent.cancel_job(first.data["job_id"])

    assert response.status == STATUS_CANCELLED
    await wait_until(lambda: assert_started(adapter, [first.data["job_id"], second.data["job_id"]]))
    assert agent.job_store.get(second.data["job_id"]).status == STATUS_PUBLISHING

    adapter.release.set()
    await asyncio.gather(*list(agent._background_tasks))


@pytest.mark.asyncio
async def test_cancelling_queued_job_does_not_interrupt_running_job():
    adapter = BlockingPublishAdapter()
    agent = PublishAgent(
        job_store=JobStore(path=""),
        channel_store=FakeChannelStore("channel-a"),
        publish_adapter=adapter,
        remote_runner=object(),
    )

    first = await agent.submit(make_request("channel-a", "first"))
    await asyncio.sleep(0)
    second = await agent.submit(make_request("channel-a", "second"))
    await asyncio.sleep(0)

    response = await agent.cancel_job(second.data["job_id"])

    assert response.status == STATUS_CANCELLED
    assert agent.job_store.get(first.data["job_id"]).status == STATUS_PUBLISHING
    assert agent.job_store.get(second.data["job_id"]).status == STATUS_CANCELLED
    assert adapter.started == [first.data["job_id"]]

    adapter.release.set()
    await asyncio.gather(*list(agent._background_tasks))


@pytest.mark.asyncio
async def test_waiting_cookie_job_keeps_same_channel_queued_jobs_waiting():
    adapter = LoginRequiredPublishAdapter()
    agent = PublishAgent(
        job_store=JobStore(path=""),
        channel_store=FakeChannelStore("channel-a"),
        publish_adapter=adapter,
        remote_runner=FakeRemoteRunner(),
    )

    first = await agent.submit(make_request("channel-a", "first"))
    await asyncio.sleep(0)
    second = await agent.submit(make_request("channel-a", "second"))
    await asyncio.gather(*list(agent._background_tasks))

    assert agent.job_store.get(first.data["job_id"]).status == STATUS_WAITING_COOKIE
    assert agent.job_store.get(second.data["job_id"]).status == STATUS_QUEUED
    assert adapter.started == [first.data["job_id"]]


def test_restart_cleanup_does_not_fail_queued_publish_jobs():
    agent = PublishAgent(
        job_store=JobStore(path=""),
        channel_store=FakeChannelStore("channel-a"),
        publish_adapter=BlockingPublishAdapter(),
        remote_runner=object(),
    )
    queued = _create_publish_job(agent.job_store, "channel-a", "queued", STATUS_QUEUED)
    running = _create_publish_job(agent.job_store, "channel-a", "running", STATUS_PUBLISHING)

    closed_count = agent.close_stale_running_jobs_after_restart()

    assert closed_count == 1
    assert agent.job_store.get(queued.job_id).status == STATUS_QUEUED
    assert agent.job_store.get(running.job_id).status == STATUS_FAILED


@pytest.mark.asyncio
async def test_restart_recovery_starts_one_queued_publish_job_per_channel():
    adapter = BlockingPublishAdapter()
    agent = PublishAgent(
        job_store=JobStore(path=""),
        channel_store=FakeChannelStore("channel-a", "channel-b"),
        publish_adapter=adapter,
        remote_runner=object(),
    )
    first_a = _create_publish_job(agent.job_store, "channel-a", "first-a", STATUS_QUEUED)
    second_a = _create_publish_job(agent.job_store, "channel-a", "second-a", STATUS_QUEUED)
    first_b = _create_publish_job(agent.job_store, "channel-b", "first-b", STATUS_QUEUED)

    await agent.resume_queued_publish_jobs_after_restart()
    await asyncio.sleep(0)

    assert set(adapter.started) == {first_a.job_id, first_b.job_id}
    assert agent.job_store.get(first_a.job_id).status == STATUS_PUBLISHING
    assert agent.job_store.get(second_a.job_id).status == STATUS_QUEUED
    assert agent.job_store.get(first_b.job_id).status == STATUS_PUBLISHING

    adapter.release.set()
    await asyncio.gather(*list(agent._background_tasks))


def _create_publish_job(store: JobStore, channel_id: str, title: str, status: str):
    job = store.create({"channel_id": channel_id, "platform": "toutiao", "title": title})
    store.update(job.job_id, status=status)
    return job
