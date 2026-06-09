from __future__ import annotations

import asyncio
import inspect
from pathlib import Path


def test_build_vnc_url_encodes_websockify_path():
    from app.remote.login import _build_vnc_url

    url = _build_vnc_url("https://unit.trycloudflare.com", "session-1", "token-1")

    assert url == (
        "https://unit.trycloudflare.com/vnc/session-1/"
        "?token=token-1&path=vnc%2Fsession-1%2Fwebsockify"
    )


def test_remote_login_runner_starts_kasmvnc_cloudflared_session(monkeypatch, tmp_path):
    import app.remote.login as login
    from app.streaming.display_pool import Slot

    events = []

    class PoolStub:
        async def acquire(self):
            events.append("acquire")
            return Slot(index=0, display=":100", web_port=6900)

        async def release(self, slot):
            events.append(("release", slot.display))

    class StreamStub:
        async def stop(self):
            events.append("stream_stop")

    class BrowserStub:
        contexts = [object()]

        async def close(self):
            events.append("browser_close")

    class PlaywrightStub:
        async def stop(self):
            events.append("playwright_stop")

    class PageStub:
        async def goto(self, url, timeout, wait_until):
            events.append(("goto", url, wait_until))

    class TunnelStub:
        def poll(self):
            return None

    class ChromeStub:
        def poll(self):
            return None

    async def fake_start_stream(slot, *, kasmvnc_bin, screen, www_dir):
        events.append(("stream", slot.display, slot.web_port, kasmvnc_bin, screen, www_dir))
        return StreamStub()

    async def fake_launch_browser(cdp_port, session_id, display):
        events.append(("launch", cdp_port, session_id, display))
        return ChromeStub()

    async def fake_attach(cdp_port, login_url):
        events.append(("attach", cdp_port, login_url))
        return PlaywrightStub(), BrowserStub(), PageStub()

    async def fake_tunnel(port):
        events.append(("tunnel", port))
        return TunnelStub(), "https://unit.trycloudflare.com"

    monkeypatch.setenv("APP_REMOTE_PROFILE_DIR", str(tmp_path / "remote_profiles"))
    monkeypatch.setattr(login, "_remote_display_pool", PoolStub())
    monkeypatch.setattr(login, "_find_free_port", lambda: 9222)
    monkeypatch.setattr(login.kasmvnc, "start_stream", fake_start_stream)
    monkeypatch.setattr(login, "_launch_chrome", fake_launch_browser)
    monkeypatch.setattr(login, "_attach_browser", fake_attach)
    monkeypatch.setattr(login, "_start_cloudflared_tunnel", fake_tunnel)
    monkeypatch.setattr(login, "_stop_process", lambda proc: events.append("tunnel_stop"))
    monkeypatch.setattr(login.uuid, "uuid4", lambda: "session-1")

    runner = login.RemoteLoginRunner()

    async def run():
        session = await runner.start("toutiao", "user1")
        await runner.cleanup(session.session_id)
        return session

    session = asyncio.run(run())

    assert session.login_url == (
        "https://unit.trycloudflare.com/vnc/session-1/"
        "?token=session-1&path=vnc%2Fsession-1%2Fwebsockify"
    )
    assert session.viewer_port == 6900
    assert ("stream", ":100", 6900, "Xvnc", "1440x900x24", "") in events
    assert ("launch", 9222, "session-1", ":100") in events
    assert ("tunnel", 19000) in events
    assert ("release", ":100") in events


def test_save_session_cookies_extracts_from_browser_context_and_cleans_up(monkeypatch):
    import app.remote.login as login
    from app.streaming.display_pool import Slot

    saved = []
    events = []
    cookies = [{"name": "sessionid", "value": "v", "domain": ".toutiao.com", "path": "/"}]

    class ContextStub:
        async def cookies(self):
            events.append("context_cookies")
            return cookies

    class BrowserStub:
        contexts = [ContextStub()]

        async def close(self):
            events.append("browser_close")

    class StreamStub:
        async def stop(self):
            events.append("stream_stop")

    class TunnelStub:
        def poll(self):
            return None

    class PoolStub:
        async def release(self, slot):
            events.append(("release", slot.display))

    session = login.RemoteLoginSession(
        session_id="session-1",
        platform="toutiao",
        user_id="user1",
        login_url="https://unit",
        viewer_port=6900,
        browser=BrowserStub(),
        tunnel_proc=TunnelStub(),
        stream=StreamStub(),
        slot=Slot(index=0, display=":100", web_port=6900),
    )
    runner = login.RemoteLoginRunner(save_cookie=lambda p, u, c: saved.append((p, u, c)))
    runner._sessions[session.session_id] = session
    monkeypatch.setattr(login, "_remote_display_pool", PoolStub())
    monkeypatch.setattr(login, "_stop_process", lambda proc: events.append("tunnel_stop"))

    result = asyncio.run(runner.save_session_cookies("session-1", notify=False))

    assert result == cookies
    assert saved == [("toutiao", "user1", cookies)]
    assert "context_cookies" in events
    assert "browser_close" in events
    assert "stream_stop" in events
    assert "tunnel_stop" in events
    assert ("release", ":100") in events


def test_remote_login_config_defaults_are_kasmvnc_cloudflared():
    import app.remote.login as login

    assert login.REMOTE_LOGIN_SERVICE_PORT == 19000
    assert login.REMOTE_LOGIN_MAX_SESSIONS == 4
    assert login.REMOTE_LOGIN_DISPLAY_BASE == 100
    assert login.REMOTE_LOGIN_KASMVNC_PORT_BASE == 6900
    assert login.REMOTE_LOGIN_KASMVNC_BIN == "Xvnc"
    assert login.REMOTE_LOGIN_VNC_SCREEN == "1440x900x24"


def test_remote_login_module_no_longer_exposes_legacy_viewer_path():
    import app.remote.login as login

    assert not hasattr(login.RemoteLoginRunner, "_start_legacy_session")
    assert not hasattr(login.RemoteLoginRunner, "_watch_login")
    assert not hasattr(login, "_extract_cookies")
    assert "viewer_runner" not in login.RemoteLoginSession.__dataclass_fields__
    assert "login_event" not in login.RemoteLoginSession.__dataclass_fields__
    assert "disconnect_event" not in login.RemoteLoginSession.__dataclass_fields__
    params = inspect.signature(login.RemoteLoginRunner).parameters
    assert "cookie_extractor" not in params
    assert "poll_interval_seconds" not in params
    assert "timeout_seconds" not in params
