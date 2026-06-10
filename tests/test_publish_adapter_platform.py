from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_publish_adapter_routes_by_platform():
    from app.publishing.adapter import PublishServiceAdapter

    calls = []

    class Publisher:
        def __init__(self, name):
            self.name = name

        async def publish(self, **kwargs):
            calls.append((self.name, kwargs))
            return {"success": True, "article_url": f"https://example.com/{self.name}"}

    adapter = PublishServiceAdapter(
        publisher_factories={
            "toutiao": lambda: Publisher("toutiao"),
            "sohu": lambda: Publisher("sohu"),
        }
    )

    result = await adapter.publish(
        platform="sohu",
        title="title",
        content="content",
        cookie="{}",
        request_id="job1",
    )

    assert result["article_url"] == "https://example.com/sohu"
    assert calls[0][0] == "sohu"


@pytest.mark.asyncio
async def test_publish_adapter_rejects_unknown_platform():
    from app.publishing.adapter import PublishServiceAdapter

    adapter = PublishServiceAdapter(publisher_factories={})

    with pytest.raises(ValueError, match="unsupported platform"):
        await adapter.publish(
            platform="unknown",
            title="title",
            content="content",
            cookie="{}",
            request_id="job1",
        )


def test_publish_adapter_builds_sohu_publisher_with_user_id(monkeypatch):
    from app.publishing.adapter import PublishServiceAdapter

    created = []

    class Publisher:
        def __init__(self, user_id=""):
            created.append(user_id)

    monkeypatch.setattr("app.publishing.sohu_service.AutoSohuPublishService", Publisher)

    publisher = PublishServiceAdapter()._publisher("sohu", user_id="user1")

    assert isinstance(publisher, Publisher)
    assert created == ["user1"]


@pytest.mark.asyncio
async def test_publish_agent_passes_platform_to_adapter(tmp_path):
    from app.jobs.models import AutoPublishRequest
    from app.jobs.store import JobStore
    from app.publishing.agent import PublishAgent

    calls = []

    class CookieStore:
        def has_valid_cookie(self, platform, user_id):
            return True

        def load_storage_state_text(self, platform, user_id):
            return "{}"

    class Adapter:
        async def publish(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, "article_url": "https://m.sohu.com/a/1_2?sec=wd"}

    agent = PublishAgent(
        job_store=JobStore(redis_url=""),
        cookie_store=CookieStore(),
        publish_adapter=Adapter(),
        log_dir=tmp_path,
    )
    request = AutoPublishRequest(user_id="user1", platform="sohu", title="title", content="content")
    job = agent.job_store.create(request.model_dump())

    await agent._publish_with_cookie(job.job_id, request)

    assert calls[0]["platform"] == "sohu"


def test_publish_service_build_task_uses_overridden_platform():
    from app.publishing.service import PublishService

    class Platform:
        def get_agent_prompt(self, title, content, cover_instruction, body_image_instruction, is_markdown, rich_html=None):
            return f"custom platform prompt: {title} {content}"

    class CustomPublishService(PublishService):
        def _platform(self):
            return Platform()

    task = CustomPublishService()._build_publish_task(
        title="title",
        content="content",
        cover_path=None,
        logger=None,
    )

    assert task == "custom platform prompt: title content"
