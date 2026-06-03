from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from api.publish_docker.app.server import app


def test_health_exposes_worker_and_queue_state():
    client = TestClient(app)
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["worker_state"] in {"idle", "busy"}
    assert isinstance(payload["queue_size"], int)
    assert "version" in payload


def test_submit_job_returns_202_and_job_id():
    client = TestClient(app)
    response = client.post(
        "/api/v1/jobs/publish",
        data={
            "title": "Test title",
            "content": "Test body",
            "cookie_text": '{"cookies":[{"name":"uid_tt","value":"1","domain":".toutiao.com","path":"/"}]}',
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["code"] == 202
    assert payload["status"] == "queued"
    assert payload["job_id"]


def test_submit_job_rejects_missing_cookie_inputs():
    client = TestClient(app)
    response = client.post(
        "/api/v1/jobs/publish",
        data={"title": "Test title", "content": "Test body"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == 400
    assert "cookie" in payload["message"].lower()


def test_query_unknown_job_returns_404():
    client = TestClient(app)
    response = client.get("/api/v1/jobs/unknown-job-id")

    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == 404


def test_submitted_job_can_be_queried_immediately():
    client = TestClient(app)
    response = client.post(
        "/api/v1/jobs/publish",
        data={
            "title": "Queued title",
            "content": "Queued body",
            "cookie_text": '{"cookies":[{"name":"uid_tt","value":"1","domain":".toutiao.com","path":"/"}]}',
        },
    )
    job_id = response.json()["job_id"]

    query = client.get(f"/api/v1/jobs/{job_id}")

    assert query.status_code == 200
    payload = query.json()
    assert payload["job_id"] == job_id
    assert payload["status"] in {"queued", "running", "succeeded", "failed"}


def test_worker_marks_job_succeeded(monkeypatch):
    import asyncio

    from publish_docker.app.queue_store import store
    from publish_docker.app.worker import process_single_job

    job = store.create_job(
        title="Title",
        content="Body",
        cookie_text='{"cookies":[{"name":"uid_tt","value":"1","domain":".toutiao.com","path":"/"}]}',
    )

    class FakePublisher:
        async def publish(self, **kwargs):
            return {"success": True, "article_url": "https://example.com/a", "article_title": kwargs["title"]}

    monkeypatch.setattr("publish_docker.app.worker.PublishService", lambda: FakePublisher())

    asyncio.run(process_single_job(job.job_id))

    updated = store.get_job(job.job_id)
    assert updated is not None
    assert updated.status == "succeeded"
    assert updated.result["success"] is True


def test_worker_forwards_cover_image_url(monkeypatch):
    import asyncio

    from publish_docker.app.queue_store import store
    from publish_docker.app.worker import process_single_job

    captured = {}

    class FakePublisher:
        async def publish(self, **kwargs):
            captured.update(kwargs)
            return {"success": True, "article_url": "", "article_title": kwargs["title"]}

    job = store.create_job(
        title="Title",
        content="Body",
        cookie_text='{"cookies":[{"name":"uid_tt","value":"1","domain":".toutiao.com","path":"/"}]}',
        cover_image_url="https://example.com/cover.jpg",
    )

    monkeypatch.setattr("publish_docker.app.worker.PublishService", lambda: FakePublisher())

    asyncio.run(process_single_job(job.job_id))

    assert captured["cover_image_url"] == "https://example.com/cover.jpg"


def test_submit_job_accepts_cookie_file():
    client = TestClient(app)
    response = client.post(
        "/api/v1/jobs/publish",
        data={"title": "Test", "content": "Body"},
        files={
            "cookie_file": (
                "auth.json",
                BytesIO(b'{"cookies":[{"name":"uid_tt","value":"1","domain":".toutiao.com","path":"/"}]}'),
                "application/json",
            )
        },
    )

    assert response.status_code == 202


def test_submit_job_accepts_cover_image_file():
    client = TestClient(app)
    response = client.post(
        "/api/v1/jobs/publish",
        data={
            "title": "Test",
            "content": "Body",
            "cookie_text": '{"cookies":[{"name":"uid_tt","value":"1","domain":".toutiao.com","path":"/"}]}',
        },
        files={"cover_image": ("cover.jpg", BytesIO(b"fake-image"), "image/jpeg")},
    )

    assert response.status_code == 202


def test_queue_limit_returns_503_when_full(monkeypatch):
    from publish_docker.app.queue_store import store

    monkeypatch.setattr(store, "can_accept", lambda: False)
    client = TestClient(app)
    response = client.post(
        "/api/v1/jobs/publish",
        data={
            "title": "Test",
            "content": "Body",
            "cookie_text": '{"cookies":[{"name":"uid_tt","value":"1","domain":".toutiao.com","path":"/"}]}',
        },
    )

    assert response.status_code == 503


def test_publish_docker_files_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "publish_docker" / "Dockerfile").exists()
    assert (root / "publish_docker" / "docker-compose.yml").exists()
    assert (root / "publish_docker" / "entrypoint.sh").exists()


def test_publish_docker_env_example_mentions_deepseek_key():
    root = Path(__file__).resolve().parents[1]
    env_example = (root / "publish_docker" / ".env.example").read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=" in env_example


def test_publish_docker_compose_uses_configurable_host_port():
    root = Path(__file__).resolve().parents[1]
    compose_text = (root / "publish_docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${HOST_SERVICE_PORT:-18000}:${SERVICE_PORT:-8000}" in compose_text
