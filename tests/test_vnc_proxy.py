from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


class RunnerStub:
    def __init__(self):
        self.sessions = {
            "session-1": type(
                "Session",
                (),
                {"viewer_port": 6900, "vnc_token": "token-1"},
            )()
        }

    def get(self, session_id):
        return self.sessions.get(session_id)


def _client(monkeypatch):
    import app.api.vnc_proxy as vnc_proxy

    requested = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html", "server": "drop-me"}

        async def aiter_raw(self):
            yield b"proxied"

        async def aclose(self):
            requested["response_closed"] = True

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            requested["timeout"] = timeout

        def build_request(self, method, url, headers, params, content):
            requested["method"] = method
            requested["url"] = url
            requested["params"] = params
            requested["content"] = content
            return object()

        async def send(self, req, stream):
            requested["stream"] = stream
            return FakeResponse()

        async def aclose(self):
            requested["client_closed"] = True

    monkeypatch.setattr(vnc_proxy.httpx, "AsyncClient", FakeAsyncClient)
    app = FastAPI()
    app.state.remote_login_runner = RunnerStub()
    app.include_router(vnc_proxy.router)
    return TestClient(app), requested


def test_vnc_proxy_rejects_missing_or_bad_token(monkeypatch):
    client, _ = _client(monkeypatch)

    missing = client.get("/vnc/session-1/")
    bad = client.get("/vnc/session-1/?token=bad")

    assert missing.status_code == 401
    assert bad.status_code == 401


def test_vnc_proxy_forwards_authorized_http_request(monkeypatch):
    client, requested = _client(monkeypatch)

    response = client.get("/vnc/session-1/index.html?token=token-1&path=abc")

    assert response.status_code == 200
    assert response.content == b"proxied"
    assert requested["url"] == "http://127.0.0.1:6900/index.html"
    assert requested["params"]["token"] == "token-1"
    assert requested["stream"] is True
    assert response.cookies.get("vncauth_session-1") == "token-1"
