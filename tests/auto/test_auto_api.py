from fastapi.testclient import TestClient

import auto.api as auto_api
from auto.models import (
    STATUS_CHECKING_COOKIE,
    STATUS_FAILED,
    STATUS_PUBLISHING,
    STATUS_SUCCEEDED,
    STATUS_WAITING_COOKIE,
)
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


def test_auto_publish_returns_task_creation_contract():
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
                        "code": 200,
                        "message": "任务创建成功，需要用户登录",
                        "data": {
                            "job_id": "job-1",
                            "task_status": "login_required",
                            "query_url": "/api/v1/auto/jobs/job-1",
                            "login_url": "https://login.example",
                            "remote_session_id": "session-1",
                            "log_file_path": "auto/logs/jobs/job-1.log",
                        },
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
        assert payload == {
            "code": 200,
            "message": "任务创建成功，需要用户登录",
            "data": {
                "job_id": "job-1",
                "task_status": "login_required",
                "query_url": "/api/v1/auto/jobs/job-1",
                "login_url": "https://login.example",
                "remote_session_id": "session-1",
                "log_file_path": "auto/logs/jobs/job-1.log",
            },
        }
    finally:
        auto_api.agent = original_agent


def test_get_job_returns_login_required_contract():
    class Job:
        job_id = "job-1"
        status = STATUS_WAITING_COOKIE
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

    original_agent = auto_api.agent
    auto_api.agent = Agent()
    client = TestClient(app)
    try:
        response = client.get("/api/v1/auto/jobs/job-1")

        assert response.status_code == 200
        assert response.json() == {
            "code": 401,
            "task_status": "login_required",
            "message": "需要用户登录或 Cookie 已失效",
            "data": {
                "job_id": "job-1",
                "login_url": "https://login.example",
            },
        }
    finally:
        auto_api.agent = original_agent


def test_get_job_returns_running_contract():
    class Job:
        job_id = "job-1"
        status = STATUS_PUBLISHING
        payload = {}
        login_url = ""
        remote_session_id = ""
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

    original_agent = auto_api.agent
    auto_api.agent = Agent()
    client = TestClient(app)
    try:
        response = client.get("/api/v1/auto/jobs/job-1")

        assert response.status_code == 200
        assert response.json() == {
            "code": 202,
            "task_status": "running",
            "message": "发布任务正在后台执行",
            "data": {
                "job_id": "job-1",
            },
        }
    finally:
        auto_api.agent = original_agent


def test_get_job_returns_published_contract():
    class Job:
        job_id = "job-1"
        status = STATUS_SUCCEEDED
        payload = {
            "user_id": "user1",
            "platform": "toutiao",
            "title": "测试标题",
            "cover_image_url": "https://example.com/cover.jpg",
        }
        login_url = ""
        remote_session_id = ""
        log_file_path = "auto/logs/jobs/job-1.log"
        result = {
            "success": True,
            "article_url": "https://www.toutiao.com/item/1/",
            "account_name": "账号名",
            "user_id": "platform-user-1",
            "article_title": "测试标题",
            "publish_signal": "post_publish_verification",
            "operation_time": "2026-06-05 10:30:00",
            "debug_payload": "should not leak",
        }
        error = ""
        created_at = "now"
        updated_at = "now"

    class Store:
        def get(self, job_id):
            return Job()

    class Agent:
        job_store = Store()

    original_agent = auto_api.agent
    auto_api.agent = Agent()
    client = TestClient(app)
    try:
        response = client.get("/api/v1/auto/jobs/job-1")

        assert response.status_code == 200
        assert response.json() == {
            "code": 200,
            "task_status": "published",
            "message": "文章发布成功",
            "data": {
                "job_id": "job-1",
                "user_id": "user1",
                "platform": "toutiao",
                "title": "测试标题",
                "cover_image_url": "https://example.com/cover.jpg",
                "article_url": "https://www.toutiao.com/item/1/",
                "publish_result": {
                    "success": True,
                    "account_name": "账号名",
                    "platform_user_id": "platform-user-1",
                    "article_title": "测试标题",
                    "publish_signal": "post_publish_verification",
                    "operation_time": "2026-06-05 10:30:00",
                },
            },
        }
    finally:
        auto_api.agent = original_agent


def test_get_job_returns_published_contract_when_article_url_missing():
    class Job:
        job_id = "job-1"
        status = STATUS_SUCCEEDED
        payload = {
            "user_id": "user1",
            "platform": "toutiao",
            "title": "测试标题",
        }
        login_url = ""
        remote_session_id = ""
        log_file_path = "auto/logs/jobs/job-1.log"
        result = {
            "success": True,
            "article_title": "测试标题",
        }
        error = ""
        created_at = "now"
        updated_at = "now"

    class Store:
        def get(self, job_id):
            return Job()

    class Agent:
        job_store = Store()

    original_agent = auto_api.agent
    auto_api.agent = Agent()
    client = TestClient(app)
    try:
        response = client.get("/api/v1/auto/jobs/job-1")

        assert response.status_code == 200
        assert response.json() == {
            "code": 200,
            "task_status": "published",
            "message": "文章发布成功，但未获取到文章链接",
            "data": {
                "job_id": "job-1",
                "user_id": "user1",
                "platform": "toutiao",
                "title": "测试标题",
                "cover_image_url": "",
                "article_url": "",
                "publish_result": {
                    "success": True,
                    "account_name": "",
                    "platform_user_id": "",
                    "article_title": "测试标题",
                    "publish_signal": "",
                    "operation_time": "",
                },
            },
        }
    finally:
        auto_api.agent = original_agent


def test_get_job_normalizes_toutiao_preview_article_url_for_published_contract():
    class Job:
        job_id = "job-1"
        status = STATUS_SUCCEEDED
        payload = {
            "user_id": "user1",
            "platform": "toutiao",
            "title": "测试标题",
        }
        login_url = ""
        remote_session_id = ""
        log_file_path = "auto/logs/jobs/job-1.log"
        result = {
            "success": True,
            "article_url": "https://mp.toutiao.com/profile_v4/graphic/preview?pgc_id=7646246126252835364",
            "article_title": "测试标题",
        }
        error = ""
        created_at = "now"
        updated_at = "now"

    class Store:
        def get(self, job_id):
            return Job()

    class Agent:
        job_store = Store()

    original_agent = auto_api.agent
    auto_api.agent = Agent()
    client = TestClient(app)
    try:
        response = client.get("/api/v1/auto/jobs/job-1")

        assert response.status_code == 200
        assert (
            response.json()["data"]["article_url"]
            == "https://www.toutiao.com/article/7646246126252835364/?&source=m_redirect"
        )
    finally:
        auto_api.agent = original_agent


def test_get_job_returns_failed_contract():
    class Job:
        job_id = "job-1"
        status = STATUS_FAILED
        payload = {}
        login_url = ""
        remote_session_id = ""
        log_file_path = "auto/logs/jobs/job-1.log"
        result = {"failure_reason": "发布失败"}
        error = "浏览器异常"
        created_at = "now"
        updated_at = "now"

    class Store:
        def get(self, job_id):
            return Job()

    class Agent:
        job_store = Store()

    original_agent = auto_api.agent
    auto_api.agent = Agent()
    client = TestClient(app)
    try:
        response = client.get("/api/v1/auto/jobs/job-1")

        assert response.status_code == 200
        assert response.json() == {
            "code": 500,
            "task_status": "failed",
            "message": "发布失败",
            "data": {
                "job_id": "job-1",
                "reason": "浏览器异常",
            },
        }
    finally:
        auto_api.agent = original_agent


def test_get_job_returns_not_found_contract():
    class Store:
        def get(self, job_id):
            return None

    class Agent:
        job_store = Store()

    original_agent = auto_api.agent
    auto_api.agent = Agent()
    client = TestClient(app)
    try:
        response = client.get("/api/v1/auto/jobs/missing-job")

        assert response.status_code == 200
        assert response.json() == {
            "code": 404,
            "task_status": "not_found",
            "message": "任务不存在",
            "data": {
                "job_id": "missing-job",
            },
        }
    finally:
        auto_api.agent = original_agent


def test_get_job_reserves_expired_and_closed_remote_session_contracts():
    class Job:
        job_id = "job-1"
        status = "expired"
        payload = {}
        login_url = ""
        remote_session_id = "session-1"
        log_file_path = "auto/logs/jobs/job-1.log"
        result = {}
        error = ""
        created_at = "now"
        updated_at = "now"

    class Store:
        def __init__(self):
            self.status = "expired"

        def get(self, job_id):
            job = Job()
            job.status = self.status
            return job

    class Agent:
        job_store = Store()

    original_agent = auto_api.agent
    auto_api.agent = Agent()
    client = TestClient(app)
    try:
        expired = client.get("/api/v1/auto/jobs/job-1")
        assert expired.json() == {
            "code": 408,
            "task_status": "expired",
            "message": "浏览器 session 已过期",
            "data": {
                "job_id": "job-1",
            },
        }

        auto_api.agent.job_store.status = "closed"
        closed = client.get("/api/v1/auto/jobs/job-1")
        assert closed.json() == {
            "code": 410,
            "task_status": "closed",
            "message": "浏览器 session 已关闭",
            "data": {
                "job_id": "job-1",
            },
        }
    finally:
        auto_api.agent = original_agent


def test_get_job_returns_query_failed_contract():
    class Store:
        def get(self, job_id):
            raise RuntimeError("store down")

    class Agent:
        job_store = Store()

    original_agent = auto_api.agent
    auto_api.agent = Agent()
    client = TestClient(app)
    try:
        response = client.get("/api/v1/auto/jobs/job-1")

        assert response.status_code == 200
        assert response.json() == {
            "code": 503,
            "task_status": "query_failed",
            "message": "查询任务状态失败",
            "data": {
                "job_id": "job-1",
            },
        }
    finally:
        auto_api.agent = original_agent
