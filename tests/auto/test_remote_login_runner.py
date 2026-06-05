import asyncio
import os
import subprocess
import sys

import pytest

from auto.logging_config import setup_job_logger
import auto.remote_cookie.remote_login_runner as remote_login_runner
from auto.remote_cookie.remote_login_runner import RemoteLoginRunner


@pytest.mark.asyncio
async def test_remote_login_runner_starts_and_completes_session(tmp_path):
    saved = []

    async def starter(platform, user_id, session_id):
        return f"https://login.example/{platform}/{user_id}/{session_id}"

    def save_cookie(platform, user_id, cookies):
        saved.append((platform, user_id, cookies))

    runner = RemoteLoginRunner(starter=starter, save_cookie=save_cookie)

    session = await runner.start("toutiao", "user1")

    assert session.login_url.startswith("https://login.example/toutiao/user1/")
    assert runner.get(session.session_id).status == "waiting_cookie"

    cookies = [{"name": "uid_tt", "value": "1", "domain": ".toutiao.com"}]
    await runner.complete_with_cookies(session.session_id, cookies)

    assert runner.get(session.session_id).status == "completed"
    assert saved == [("toutiao", "user1", cookies)]


@pytest.mark.asyncio
async def test_remote_login_runner_completes_session_only_once(tmp_path):
    ready = []

    async def starter(platform, user_id, session_id):
        return f"https://login.example/{platform}/{user_id}/{session_id}"

    runner = RemoteLoginRunner(
        starter=starter,
        on_cookie_ready=lambda session_id, cookies: _ready(ready, session_id, cookies),
    )
    session = await runner.start("toutiao", "user1")
    cookies = [{"name": "uid_tt", "value": "1", "domain": ".toutiao.com"}]

    first = await runner.complete_with_cookies(session.session_id, cookies)
    second = await runner.complete_with_cookies(session.session_id, cookies)

    assert first is True
    assert second is False
    assert ready == [(session.session_id, cookies)]


@pytest.mark.asyncio
async def test_remote_login_runner_manual_save_prevents_watcher_duplicate(tmp_path):
    logger, _log_path = setup_job_logger("job-manual-cookie-save", tmp_path)
    ready = []

    class Event:
        def __init__(self):
            self._set = False

        async def wait(self):
            return True

        def is_set(self):
            return self._set

        def set(self):
            self._set = True

    async def extractor(_cdp_port):
        return [{"name": "uid_tt", "value": "1", "domain": ".toutiao.com"}]

    runner = RemoteLoginRunner(
        starter=lambda platform, user_id, session_id: _url(platform, user_id, session_id),
        on_cookie_ready=lambda session_id, cookies: _ready(ready, session_id, cookies),
        logger_factory=lambda _session_id: logger,
        cookie_extractor=extractor,
    )
    session = await runner.start("toutiao", "user1")
    session.disconnect_event = Event()
    session.cdp_port = 9222

    cookies = await runner.save_session_cookies(session.session_id)
    await runner._watch_login(session)

    assert cookies == [{"name": "uid_tt", "value": "1", "domain": ".toutiao.com"}]
    assert session.status == "completed"
    assert ready == [(session.session_id, cookies)]


@pytest.mark.asyncio
async def test_remote_login_runner_writes_watcher_state_to_job_log(tmp_path):
    logger, log_path = setup_job_logger("job-1", tmp_path)
    cookies = [{"name": "uid_tt", "value": "1", "domain": ".toutiao.com"}]
    ready = []

    class Event:
        async def wait(self):
            return True

        def is_set(self):
            return True

    async def extractor(_cdp_port):
        return cookies

    runner = RemoteLoginRunner(
        starter=lambda platform, user_id, session_id: _url(platform, user_id, session_id),
        on_cookie_ready=lambda session_id, found: _ready(ready, session_id, found),
        logger_factory=lambda _session_id: logger,
        cookie_extractor=extractor,
    )
    session = await runner.start("toutiao", "user1")
    session.disconnect_event = Event()
    session.cdp_port = 9222

    await runner._watch_login(session)

    text = log_path.read_text(encoding="utf-8")
    assert "远程登录 watcher 开始等待远程查看器关闭" in text
    assert "远程查看器已断开，开始提取 Cookie" in text
    assert "远程登录 watcher 已提取 Cookie cookie_count=1" in text
    assert ready == [(session.session_id, cookies)]


async def _url(platform, user_id, session_id):
    return f"https://login.example/{platform}/{user_id}/{session_id}"


async def _ready(ready, session_id, cookies):
    ready.append((session_id, cookies))


@pytest.mark.asyncio
async def test_remote_login_runner_extracts_cookies_when_viewer_disconnects(tmp_path):
    logger, log_path = setup_job_logger("job-cookie-poll", tmp_path)
    found = []

    class Event:
        async def wait(self):
            return True

        def is_set(self):
            return True

    async def extractor(_cdp_port):
        return [{"name": "uid_tt", "value": "1", "domain": ".toutiao.com"}]

    runner = RemoteLoginRunner(
        starter=lambda platform, user_id, session_id: _url(platform, user_id, session_id),
        on_cookie_ready=lambda session_id, cookies: _ready(found, session_id, cookies),
        logger_factory=lambda _session_id: logger,
        cookie_extractor=extractor,
        timeout_seconds=1,
    )
    session = await runner.start("toutiao", "user1")
    session.disconnect_event = Event()
    session.cdp_port = 9222

    await runner._watch_login(session)

    text = log_path.read_text(encoding="utf-8")
    assert "远程查看器已断开，开始提取 Cookie" in text
    assert "远程登录 watcher 已提取 Cookie cookie_count=1" in text
    assert found == [(session.session_id, [{"name": "uid_tt", "value": "1", "domain": ".toutiao.com"}])]


@pytest.mark.asyncio
async def test_remote_login_runner_does_not_accept_toutiao_visitor_cookies(tmp_path):
    logger, log_path = setup_job_logger("job-cookie-poll-strict", tmp_path)
    calls = 0
    found = []

    class Event:
        async def wait(self):
            return True

        def is_set(self):
            return True

    async def extractor(_cdp_port):
        nonlocal calls
        calls += 1
        return [
            {"name": "s_v_web_id", "value": "verify_x", "domain": "mp.toutiao.com"},
            {"name": "passport_csrf_token", "value": "csrf", "domain": ".toutiao.com"},
            {"name": "ttwid", "value": "visitor", "domain": ".toutiao.com"},
        ]

    runner = RemoteLoginRunner(
        starter=lambda platform, user_id, session_id: _url(platform, user_id, session_id),
        on_cookie_ready=lambda session_id, cookies: _ready(found, session_id, cookies),
        logger_factory=lambda _session_id: logger,
        cookie_extractor=extractor,
        timeout_seconds=0.03,
    )
    session = await runner.start("toutiao", "user1")
    session.disconnect_event = Event()
    session.cdp_port = 9222

    await runner._watch_login(session)

    text = log_path.read_text(encoding="utf-8")
    assert "远程查看器已断开，开始提取 Cookie" in text
    assert "远程登录 watcher 未检测到登录态 Cookie" in text
    assert found == []
    assert calls >= 1


@pytest.mark.asyncio
async def test_remote_login_runner_fails_when_disconnect_event_is_missing(tmp_path):
    logger, log_path = setup_job_logger("job-missing-disconnect", tmp_path)

    runner = RemoteLoginRunner(
        starter=lambda platform, user_id, session_id: _url(platform, user_id, session_id),
        logger_factory=lambda _session_id: logger,
    )
    session = await runner.start("toutiao", "user1")

    await runner._watch_login(session)

    text = log_path.read_text(encoding="utf-8")
    assert session.status == "failed"
    assert "remote login watcher missing disconnect event" in text


def test_remote_login_runner_prefers_container_browser_path_from_environment(monkeypatch):
    monkeypatch.setenv("BROWSER_EXECUTABLE_PATH", "/usr/bin/chromium")
    monkeypatch.setattr(os.path, "exists", lambda path: path == "/usr/bin/chromium")

    assert remote_login_runner._find_browser_path() == "/usr/bin/chromium"


def test_remote_login_runner_finds_playwright_chromium_bundle(monkeypatch):
    paths = {
        "/ms-playwright/chromium-1000/chrome-linux/chrome",
    }
    monkeypatch.delenv("BROWSER_EXECUTABLE_PATH", raising=False)
    monkeypatch.setattr(os.path, "exists", lambda path: path in paths)
    monkeypatch.setattr(remote_login_runner.glob, "glob", lambda pattern: list(paths))
    monkeypatch.setattr(remote_login_runner.shutil, "which", lambda _name: None)

    assert remote_login_runner._find_browser_path() == "/ms-playwright/chromium-1000/chrome-linux/chrome"


def test_remote_login_runner_finds_cloudflared_from_environment(monkeypatch):
    monkeypatch.setenv("CLOUDFLARED_PATH", "/usr/local/bin/cloudflared")
    monkeypatch.setattr(os.path, "exists", lambda path: path == "/usr/local/bin/cloudflared")

    assert remote_login_runner._find_cloudflared_path() == "/usr/local/bin/cloudflared"


@pytest.mark.asyncio
async def test_launch_chrome_uses_container_safe_flags(monkeypatch, tmp_path):
    launched = {}

    class Proc:
        pass

    def popen(args, **kwargs):
        launched["args"] = args
        launched["kwargs"] = kwargs
        return Proc()

    monkeypatch.setattr(remote_login_runner, "REMOTE_PROFILE_DIR", tmp_path)
    monkeypatch.setattr(remote_login_runner, "_find_browser_path", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(asyncio, "sleep", lambda _seconds: _immediate())
    monkeypatch.setattr(sys, "platform", "linux")

    await remote_login_runner._launch_chrome(9222, "session-1")

    assert launched["args"][0] == "/usr/bin/chromium"
    assert "--no-sandbox" in launched["args"]
    assert "--disable-dev-shm-usage" in launched["args"]


async def _immediate():
    return None
