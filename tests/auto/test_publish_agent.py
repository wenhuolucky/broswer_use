import pytest

from auto.job_store import JobStore
from auto.models import AutoPublishRequest
from auto.publish_agent import PublishAgent


@pytest.mark.asyncio
async def test_publish_agent_uses_existing_cookie_and_publishes():
    class CookieStoreStub:
        def has_valid_cookie(self, platform, user_id):
            return True

        def load_storage_state_text(self, platform, user_id):
            return '{"cookies":[{"domain":".toutiao.com"}]}'

    class PublishAdapterStub:
        async def publish(self, **kwargs):
            return {"success": True, "article_url": "https://toutiao.com/a"}

    agent = PublishAgent(
        job_store=JobStore(),
        cookie_store=CookieStoreStub(),
        publish_adapter=PublishAdapterStub(),
        remote_runner=None,
    )

    response = await agent.submit(AutoPublishRequest(
        user_id="user1",
        platform="toutiao",
        title="title",
        content="content",
    ))

    assert response.status == "succeeded"
    assert response.result["article_url"] == "https://toutiao.com/a"


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

    assert response.status == "waiting_cookie"
    assert response.login_url == "https://login.example"
    job = agent.job_store.get(response.job_id)
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

    job = agent.job_store.get(response.job_id)
    log_path = tmp_path / "jobs" / f"{response.job_id}.log"
    assert response.log_file_path == str(log_path)
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
        first.job_id,
        [{"name": "sid", "value": "1", "domain": ".toutiao.com"}],
    )

    assert saved == [("toutiao", "user1", [{"name": "sid", "value": "1", "domain": ".toutiao.com"}])]
    assert resumed.status == "succeeded"
    assert resumed.result["article_url"] == "https://toutiao.com/resumed"
