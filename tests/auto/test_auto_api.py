from fastapi.testclient import TestClient

import auto.api as auto_api
from auto.server import app


def test_auto_api_routes_exist():
    client = TestClient(app)

    health = client.get("/api/v1/auto/health")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"


def test_auto_publish_requires_user_id():
    client = TestClient(app)

    response = client.post("/api/v1/auto/publish", json={
        "platform": "toutiao",
        "title": "title",
        "content": "content",
    })

    assert response.status_code == 422


def test_auto_api_exposes_remote_cookie_completion_route():
    paths = app.openapi()["paths"]

    assert "/api/v1/auto/jobs/{job_id}/cookies" in paths


def test_job_response_contains_log_file_path():
    class Job:
        job_id = "job-1"
        status = "waiting_cookie"
        payload = {}
        login_url = "https://login.example"
        remote_session_id = "session-1"
        log_file_path = "auto/logs/jobs/job-1.log"
        result = {}
        error = ""
        created_at = "now"
        updated_at = "now"

    class Store:
        def get(self, job_id):
            return Job()

    class Agent:
        job_store = Store()

        async def submit(self, request):
            class Response:
                job_id = "job-1"

                def model_dump(self):
                    return {
                        "code": 202,
                        "job_id": "job-1",
                        "status": "waiting_cookie",
                        "message": "",
                        "login_url": "https://login.example",
                        "log_file_path": "auto/logs/jobs/job-1.log",
                        "result": {},
                    }

            return Response()

    original_agent = auto_api.agent
    auto_api.agent = Agent()
    client = TestClient(app)
    try:
        response = client.post("/api/v1/auto/publish", json={
            "user_id": "api-log-user",
            "platform": "toutiao",
            "title": "title",
            "content": "content",
        })

        assert response.status_code == 200
        payload = response.json()
        assert payload["log_file_path"].endswith(f"{payload['job_id']}.log")

        query = client.get(f"/api/v1/auto/jobs/{payload['job_id']}")
        assert query.status_code == 200
        assert query.json()["log_file_path"] == payload["log_file_path"]
    finally:
        auto_api.agent = original_agent
