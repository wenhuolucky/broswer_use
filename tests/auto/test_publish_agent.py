import asyncio

import pytest

from auto.job_store import JobStore
from auto.models import AutoPublishRequest
from auto.publish_agent import PublishAgent


@pytest.mark.asyncio
async def test_publish_agent_creates_job_and_publishes_existing_cookie_in_background():
    publish_started = asyncio.Event()
    publish_can_finish = asyncio.Event()

    class CookieStoreStub:
        def has_valid_cookie(self, platform, user_id):
            return True

        def load_storage_state_text(self, platform, user_id):
            return '{"cookies":[{"domain":".toutiao.com"}]}'

    class PublishAdapterStub:
        async def publish(self, **kwargs):
            publish_started.set()
            await publish_can_finish.wait()
            return {"success": True, "article_url": "https://toutiao.com/a"}

    agent = PublishAgent(
        job_store=JobStore(),
        cookie_store=CookieStoreStub(),
        publish_adapter=PublishAdapterStub(),
        remote_runner=None,
    )

    response = await asyncio.wait_for(
        agent.submit(AutoPublishRequest(
            user_id="user1",
            platform="toutiao",
            title="title",
            content="content",
        )),
        timeout=0.2,
    )

    assert response.code == 200
    assert response.data["task_status"] == "running"
    assert response.data["job_id"]
    assert response.data["query_url"] == f"/api/v1/auto/jobs/{response.data['job_id']}"
    assert response.data["login_url"] == ""
    assert response.data["remote_session_id"] == ""
    assert response.data["log_file_path"].endswith(f"{response.data['job_id']}.log")

    await asyncio.wait_for(publish_started.wait(), timeout=1)
    job = agent.job_store.get(response.data["job_id"])
    assert job.status == "publishing"

    publish_can_finish.set()
    for _ in range(20):
        await asyncio.sleep(0.01)
        job = agent.job_store.get(response.data["job_id"])
        if job.status == "succeeded":
            break
    assert job.status == "succeeded"
    assert job.result["article_url"] == "https://toutiao.com/a"


@pytest.mark.asyncio
async def test_publish_agent_starts_remote_login_when_cookie_missing():
    class CookieStoreStub:
        def has_valid_cookie(self, platform, user_id):
            return False

    class RemoteRunnerStub:
        async def start(self, platform, user_id):
            class Session:
                session_id = "session-1"
                login_url = "https://login.example"

            return Session()

    agent = PublishAgent(
        job_store=JobStore(),
        cookie_store=CookieStoreStub(),
        publish_adapter=None,
        remote_runner=RemoteRunnerStub(),
    )

    response = await agent.submit(AutoPublishRequest(
        user_id="user1",
        platform="toutiao",
        title="title",
        content="content",
    ))

    assert response.code == 200
    assert response.data["task_status"] == "login_required"
    assert response.data["login_url"] == "https://login.example"
    assert response.data["remote_session_id"] == "session-1"
    assert response.data["query_url"] == f"/api/v1/auto/jobs/{response.data['job_id']}"
    job = agent.job_store.get(response.data["job_id"])
    assert job.remote_session_id == "session-1"


@pytest.mark.asyncio
async def test_publish_agent_writes_job_log_for_cookie_missing(tmp_path):
    class CookieStoreStub:
        def has_valid_cookie(self, platform, user_id):
            return False

    class RemoteRunnerStub:
        async def start(self, platform, user_id):
            class Session:
                session_id = "session-1"
                login_url = "https://login.example"

            return Session()

    agent = PublishAgent(
        job_store=JobStore(),
        cookie_store=CookieStoreStub(),
        publish_adapter=None,
        remote_runner=RemoteRunnerStub(),
        log_dir=tmp_path,
    )

    response = await agent.submit(AutoPublishRequest(
        user_id="user1",
        platform="toutiao",
        title="title",
        content="content",
    ))

    job = agent.job_store.get(response.data["job_id"])
    log_path = tmp_path / "jobs" / f"{response.data['job_id']}.log"
    assert response.data["log_file_path"] == str(log_path)
    assert job.log_file_path == str(log_path)
    text = log_path.read_text(encoding="utf-8")
    assert "收到自动发文请求" in text
    assert "Cookie 不存在或无效" in text
    assert "远程登录已启动" in text


@pytest.mark.asyncio
async def test_publish_agent_resumes_after_remote_cookie_completion():
    saved = []

    class CookieStoreStub:
        def __init__(self):
            self.ready = False

        def has_valid_cookie(self, platform, user_id):
            return self.ready

        def save(self, platform, user_id, cookies):
            saved.append((platform, user_id, cookies))
            self.ready = True

        def load_storage_state_text(self, platform, user_id):
            return '{"cookies":[{"domain":".toutiao.com"}]}'

    class RemoteRunnerStub:
        async def start(self, platform, user_id):
            class Session:
                session_id = "session-1"
                login_url = "https://login.example"

            return Session()

        async def complete_with_cookies(self, session_id, cookies):
            pass

    class PublishAdapterStub:
        async def publish(self, **kwargs):
            return {"success": True, "article_url": "https://toutiao.com/resumed"}

    cookie_store = CookieStoreStub()
    agent = PublishAgent(
        job_store=JobStore(),
        cookie_store=cookie_store,
        publish_adapter=PublishAdapterStub(),
        remote_runner=RemoteRunnerStub(),
    )
    first = await agent.submit(AutoPublishRequest(
        user_id="user1",
        platform="toutiao",
        title="title",
        content="content",
    ))

    resumed = await agent.resume_after_cookie(
        first.data["job_id"],
        [{"name": "sid", "value": "1", "domain": ".toutiao.com"}],
    )

    assert saved == [("toutiao", "user1", [{"name": "sid", "value": "1", "domain": ".toutiao.com"}])]
    assert resumed.status == "succeeded"
    assert resumed.result["article_url"] == "https://toutiao.com/resumed"


@pytest.mark.asyncio
async def test_publish_agent_starts_remote_login_when_existing_cookie_is_rejected():
    class CookieStoreStub:
        def has_valid_cookie(self, platform, user_id):
            return True

        def load_storage_state_text(self, platform, user_id):
            return '{"cookies":[{"domain":".toutiao.com"}]}'

    class PublishAdapterStub:
        async def publish(self, **kwargs):
            return {
                "success": False,
                "login_required": True,
                "failure_reason": "账号未登录，Cookie 已失效",
            }

    class RemoteRunnerStub:
        async def start(self, platform, user_id):
            class Session:
                session_id = "session-1"
                login_url = "https://login.example"

            return Session()

    agent = PublishAgent(
        job_store=JobStore(),
        cookie_store=CookieStoreStub(),
        publish_adapter=PublishAdapterStub(),
        remote_runner=RemoteRunnerStub(),
    )

    response = await agent.submit(AutoPublishRequest(
        user_id="user1",
        platform="toutiao",
        title="title",
        content="content",
    ))

    assert response.data["task_status"] == "running"
    job = agent.job_store.get(response.data["job_id"])
    for _ in range(20):
        await asyncio.sleep(0.01)
        job = agent.job_store.get(response.data["job_id"])
        if job.status == "waiting_cookie":
            break
    assert job.status == "waiting_cookie"
    assert job.remote_session_id == "session-1"
    assert job.payload["cookie_refresh_attempted"] is True


@pytest.mark.asyncio
async def test_publish_agent_does_not_loop_remote_login_after_cookie_refresh_attempt():
    class CookieStoreStub:
        def has_valid_cookie(self, platform, user_id):
            return True

        def load_storage_state_text(self, platform, user_id):
            return '{"cookies":[{"domain":".toutiao.com"}]}'

    class PublishAdapterStub:
        async def publish(self, **kwargs):
            return {
                "success": False,
                "failure_reason": "当前页面仍然未登录",
            }

    class RemoteRunnerStub:
        async def start(self, platform, user_id):
            raise AssertionError("remote login should not start again")

    agent = PublishAgent(
        job_store=JobStore(),
        cookie_store=CookieStoreStub(),
        publish_adapter=PublishAdapterStub(),
        remote_runner=RemoteRunnerStub(),
    )
    job = agent.job_store.create({
        "user_id": "user1",
        "platform": "toutiao",
        "title": "title",
        "content": "content",
        "cover_image_url": None,
        "cookie_refresh_attempted": True,
    })

    response = await agent._publish_with_cookie(
        job.job_id,
        AutoPublishRequest(user_id="user1", platform="toutiao", title="title", content="content"),
    )

    assert response.status == "failed"
    assert response.message == "当前页面仍然未登录"
