import pytest
import sys
from pathlib import Path

from auto.adapters.publish_service import PublishServiceAdapter
from auto.adapters.toutiao_publish_service import AutoToutiaoPublishService
from auto.job_store import JobStore
from auto.models import STATUS_QUEUED


def test_job_store_creates_and_updates_job():
    store = JobStore()

    job = store.create({"title": "t"})

    assert job.job_id
    assert job.status == STATUS_QUEUED
    assert store.get(job.job_id).payload == {"title": "t"}

    store.update(job.job_id, status="succeeded", result={"ok": True})
    updated = store.get(job.job_id)
    assert updated.status == "succeeded"
    assert updated.result == {"ok": True}


@pytest.mark.asyncio
async def test_publish_adapter_forwards_existing_service_arguments():
    calls = []

    class DummyPublisher:
        async def publish(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, "article_url": "https://example.com/a"}

    adapter = PublishServiceAdapter(publisher_factory=lambda: DummyPublisher())

    result = await adapter.publish(
        title="title",
        content="content",
        cookie='{"cookies":[]}',
        request_id="rid",
        cover_image_url="https://example.com/cover.jpg",
    )

    assert result["success"] is True
    assert calls == [{
        "title": "title",
        "content": "content",
        "cookie": '{"cookies":[]}',
        "request_id": "rid",
        "cover_image_path": None,
        "cover_image_url": "https://example.com/cover.jpg",
    }]


def test_publish_adapter_makes_legacy_platforms_import_available():
    project_root = Path(__file__).resolve().parents[2]
    src_path = str(project_root / "src")
    original_path = list(sys.path)
    sys.path = [item for item in sys.path if item != src_path]
    sys.modules.pop("platforms", None)
    sys.modules.pop("platforms.toutiao", None)
    try:
        adapter = PublishServiceAdapter(publisher_factory=lambda: object())
        adapter._publisher()

        from platforms.toutiao import ToutiaoPlatform

        assert ToutiaoPlatform().name == "toutiao"
    finally:
        sys.path = original_path
        sys.modules.pop("platforms", None)
        sys.modules.pop("platforms.toutiao", None)


def test_publish_adapter_uses_auto_toutiao_publish_service_by_default():
    adapter = PublishServiceAdapter()

    publisher = adapter._publisher()

    assert isinstance(publisher, AutoToutiaoPublishService)
