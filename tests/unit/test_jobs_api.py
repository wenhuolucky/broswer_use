from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1 import jobs as jobs_route
from app.domain.job import AutoTaskCreateResponse, STATUS_FAILED


class FakeAgent:
    async def submit(self, request):
        return AutoTaskCreateResponse(
            code=422,
            message="标题长度不符合要求",
            data={
                "job_id": "",
                "channel_id": request.channel_id,
                "status": STATUS_FAILED,
                "live_url": "",
                "error_detail": "标题长度不符合要求",
            },
        )


def make_app(fake_agent: FakeAgent, monkeypatch) -> FastAPI:
    monkeypatch.setattr(deps, "agent", fake_agent)
    monkeypatch.setattr(jobs_route, "agent", fake_agent)
    app = FastAPI()
    app.include_router(jobs_route.router)
    return app


def test_create_job_returns_422_when_agent_rejects_title_length(monkeypatch):
    client = TestClient(make_app(FakeAgent(), monkeypatch))

    response = client.post(
        "/jobs",
        json={
            "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
            "title": "短",
            "content": "正文",
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "标题长度不符合要求"}
