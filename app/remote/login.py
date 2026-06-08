from __future__ import annotations

import asyncio
import glob
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import uuid
import urllib.request
from dataclasses import dataclass
from logging import LoggerAdapter
from pathlib import Path
from typing import Awaitable, Callable

from app.core.config import REMOTE_LOGIN_TIMEOUT_SECONDS, REMOTE_PROFILE_DIR
from app.remote.display_config import get_remote_browser_window_size

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PROJECT_ROOT / "tools"
if TOOLS_DIR.exists() and str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


@dataclass
class RemoteLoginSession:
    session_id: str
    platform: str
    user_id: str
    login_url: str
    status: str = "waiting_cookie"
    cdp_port: int = 0
    viewer_port: int = 0
    chrome_proc: subprocess.Popen | None = None
    tunnel_proc: subprocess.Popen | None = None
    viewer_runner: object | None = None
    playwright: object | None = None
    browser: object | None = None
    login_event: asyncio.Event | None = None
    disconnect_event: asyncio.Event | None = None
    task: asyncio.Task | None = None


class RemoteLoginRunner:
    """Isolated facade for remote cookie acquisition."""

    def __init__(
        self,
        starter: Callable[[str, str, str], Awaitable[str]] | None = None,
        save_cookie: Callable[[str, str, list[dict]], None] | None = None,
        on_cookie_ready: Callable[[str, list[dict]], Awaitable[None]] | None = None,
        logger_factory: Callable[[str], LoggerAdapter] | None = None,
        cookie_extractor: Callable[[int], Awaitable[list[dict]]] | None = None,
        poll_interval_seconds: float = 2.0,
        timeout_seconds: int = REMOTE_LOGIN_TIMEOUT_SECONDS,
    ):
        self._starter = starter
        self._save_cookie = save_cookie
        self._on_cookie_ready = on_cookie_ready
        self._logger_factory = logger_factory
        self._cookie_extractor = cookie_extractor or _extract_cookies
        self._poll_interval_seconds = poll_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._sessions: dict[str, RemoteLoginSession] = {}

    async def start(self, platform: str, user_id: str) -> RemoteLoginSession:
        session_id = str(uuid.uuid4())
        if self._starter:
            login_url = await self._starter(platform, user_id, session_id)
            session = RemoteLoginSession(
                session_id=session_id,
                platform=platform,
                user_id=user_id,
                login_url=login_url,
            )
            self._sessions[session_id] = session
            return session

        session = await self._start_real_session(platform, user_id, session_id)
        self._sessions[session_id] = session
        session.task = asyncio.create_task(self._watch_login(session))
        return session

    def get(self, session_id: str) -> RemoteLoginSession | None:
        return self._sessions.get(session_id)

    async def complete_with_cookies(self, session_id: str, cookies: list[dict]) -> bool:
        session = self._sessions[session_id]
        if session.status == "completed":
            return False
        if self._save_cookie:
            self._save_cookie(session.platform, session.user_id, cookies)
        session.status = "completed"
        if self._on_cookie_ready:
            await self._on_cookie_ready(session_id, cookies)
        return True

    async def save_session_cookies(self, session_id: str, notify: bool = True) -> list[dict]:
        session = self._sessions[session_id]
        if session.status == "completed":
            return []
        cookies = await self._cookie_extractor(session.cdp_port)
        if not _has_platform_cookie(session.platform, cookies):
            session.status = "failed"
            raise RuntimeError("remote cookie invalid")
        if notify:
            await self.complete_with_cookies(session_id, cookies)
        else:
            if self._save_cookie:
                self._save_cookie(session.platform, session.user_id, cookies)
            session.status = "completed"
        await self.cleanup(session_id)
        return cookies

    async def _start_real_session(self, platform: str, user_id: str, session_id: str) -> RemoteLoginSession:
        cdp_port = _find_free_port()
        viewer_port = _find_free_port()
        chrome_proc = await _launch_chrome(cdp_port, session_id)

        from app.remote.viewer import run_viewer_server

        login_url = _login_url_for(platform)
        login_event = asyncio.Event()
        disconnect_event = asyncio.Event()
        viewer_runner, pw, browser, _cdp, _page = await run_viewer_server(
            cdp_port,
            viewer_port,
            login_url,
            login_detect_url=login_url,
            login_event=login_event,
            disconnect_event=disconnect_event,
        )
        tunnel_proc, tunnel_url = await _start_cloudflared_tunnel(viewer_port)

        return RemoteLoginSession(
            session_id=session_id,
            platform=platform,
            user_id=user_id,
            login_url=tunnel_url,
            cdp_port=cdp_port,
            viewer_port=viewer_port,
            chrome_proc=chrome_proc,
            tunnel_proc=tunnel_proc,
            viewer_runner=viewer_runner,
            playwright=pw,
            browser=browser,
            login_event=login_event,
            disconnect_event=disconnect_event,
        )

    async def _watch_login(self, session: RemoteLoginSession) -> None:
        logger = self._logger(session.session_id)
        try:
            if session.disconnect_event is None:
                session.status = "failed"
                logger.error("remote login watcher missing disconnect event session_id=%s", session.session_id)
                return
            logger.info(
                "远程登录 watcher 开始等待远程查看器关闭 session_id=%s cdp_port=%s viewer_port=%s timeout_seconds=%s",
                session.session_id,
                session.cdp_port,
                session.viewer_port,
                self._timeout_seconds,
            )
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(session.disconnect_event.wait()),
                    asyncio.create_task(asyncio.sleep(self._timeout_seconds)),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if session.status == "completed":
                logger.info(
                    "remote login watcher skipped cookie extraction because session already completed session_id=%s",
                    session.session_id,
                )
                return
            if not session.disconnect_event.is_set():
                session.status = "timeout"
                logger.warning("远程登录 watcher 等待远程查看器关闭超时 session_id=%s", session.session_id)
                return
            logger.info("远程查看器已断开，开始提取 Cookie session_id=%s", session.session_id)
            cookies = await self._cookie_extractor(session.cdp_port)
            logger.info(
                "远程登录 watcher 已提取 Cookie cookie_count=%s session_id=%s",
                len(cookies),
                session.session_id,
            )
            if not _has_platform_cookie(session.platform, cookies):
                session.status = "failed"
                logger.error(
                    "远程登录 watcher 未检测到登录态 Cookie session_id=%s cookie_names=%s",
                    session.session_id,
                    [cookie.get("name", "") for cookie in cookies],
                )
                return
            await self.complete_with_cookies(session.session_id, cookies)
            logger.info("远程登录 watcher 已完成 Cookie 回调 session_id=%s", session.session_id)
        except Exception as exc:
            session.status = "failed"
            logger.exception("远程登录 watcher 异常 session_id=%s error=%s", session.session_id, exc)
        finally:
            logger.info("远程登录 watcher 开始清理资源 session_id=%s", session.session_id)
            await self.cleanup(session.session_id)
            logger.info("远程登录 watcher 资源清理完成 session_id=%s", session.session_id)

    async def cleanup(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if not session:
            return
        if session.viewer_runner:
            try:
                await session.viewer_runner.cleanup()
            except Exception:
                pass
        if session.browser:
            try:
                await session.browser.close()
            except Exception:
                pass
        if session.playwright:
            try:
                await session.playwright.stop()
            except Exception:
                pass
        if session.tunnel_proc and session.tunnel_proc.poll() is None:
            _stop_process(session.tunnel_proc)
        if session.chrome_proc and session.chrome_proc.poll() is None:
            _stop_process(session.chrome_proc)
        profile_dir = REMOTE_PROFILE_DIR / session_id
        if profile_dir.exists():
            shutil.rmtree(profile_dir, ignore_errors=True)

    def _logger(self, session_id: str):
        if self._logger_factory:
            return self._logger_factory(session_id)

        class NullLogger:
            def info(self, *args, **kwargs): pass
            def warning(self, *args, **kwargs): pass
            def error(self, *args, **kwargs): pass
            def exception(self, *args, **kwargs): pass

        return NullLogger()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _find_browser_path() -> str:
    configured = os.getenv("BROWSER_EXECUTABLE_PATH")
    if configured and os.path.exists(configured):
        return configured
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    playwright_bundles = sorted(
        glob.glob("/ms-playwright/chromium-*/chrome-linux/chrome")
        + glob.glob("/ms-playwright/chromium-*/chrome-linux64/chrome")
        + glob.glob("/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome")
        + glob.glob("/root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")
    )
    for path in playwright_bundles:
        if os.path.exists(path):
            return path
    found = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chrome")
        or shutil.which("msedge")
    )
    if found:
        return found
    raise RuntimeError("未找到 Chrome 或 Edge 浏览器")


def _find_cloudflared_path() -> str:
    configured = os.getenv("CLOUDFLARED_PATH")
    if configured and os.path.exists(configured):
        return configured
    candidates = [
        r"C:\Program Files\Cloudflare\cloudflared.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\cloudflared\cloudflared.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\local\bin\cloudflared.exe"),
        "/usr/local/bin/cloudflared",
        "/usr/bin/cloudflared",
    ]
    winget_dir = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    if os.path.exists(winget_dir):
        for item in os.listdir(winget_dir):
            if "cloudflare" in item.lower():
                candidate = os.path.join(winget_dir, item, "cloudflared.exe")
                if os.path.exists(candidate):
                    return candidate
    for path in candidates:
        if os.path.exists(path):
            return path
    found = shutil.which("cloudflared")
    if found:
        return found
    raise RuntimeError("未找到 cloudflared，请先安装 Cloudflare.cloudflared")


async def _launch_chrome(cdp_port: int, session_id: str) -> subprocess.Popen:
    profile_dir = REMOTE_PROFILE_DIR / session_id
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    window_width, window_height = get_remote_browser_window_size()
    proc = subprocess.Popen(
        [
            _find_browser_path(),
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={profile_dir}",
            f"--window-size={window_width},{window_height}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-default-apps",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--new-window",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    await _wait_for_cdp(cdp_port, proc)
    return proc


async def _start_cloudflared_tunnel(local_port: int) -> tuple[subprocess.Popen, str]:
    proc = subprocess.Popen(
        [_find_cloudflared_path(), "tunnel", "--url", f"http://localhost:{local_port}", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"cloudflared 异常退出 (code={proc.poll()})")
            line = proc.stdout.readline()
            if not line:
                await asyncio.sleep(0.1)
                continue
            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if match:
                return proc, match.group(0)
    except Exception:
        _stop_process(proc)
        raise


async def _extract_cookies(cdp_port: int) -> list[dict]:
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        browser = await asyncio.wait_for(
            pw.chromium.connect_over_cdp(_cdp_http_url(cdp_port)),
            timeout=15,
        )
        context = browser.contexts[0]
        cookies = await context.cookies()
        await browser.close()
        return cookies
    finally:
        await pw.stop()


def _login_url_for(platform: str) -> str:
    if platform == "toutiao":
        return "https://mp.toutiao.com/auth/page/login"
    if platform == "sohu":
        return "https://mp.sohu.com"
    raise ValueError(f"未知平台: {platform}")


def _cdp_http_url(cdp_port: int) -> str:
    return f"http://127.0.0.1:{cdp_port}"


async def _wait_for_cdp(cdp_port: int, proc: subprocess.Popen, timeout_seconds: float = 10.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    url = f"{_cdp_http_url(cdp_port)}/json/version"
    last_error: Exception | None = None
    while asyncio.get_event_loop().time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Chrome exited before CDP became ready (code={proc.poll()})")
        try:
            await asyncio.to_thread(lambda: urllib.request.urlopen(url, timeout=1).close())
            return
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.2)
    raise RuntimeError(f"Chrome CDP did not become ready at {_cdp_http_url(cdp_port)}: {last_error}")


def _has_platform_cookie(platform: str, cookies: list[dict]) -> bool:
    domains = {
        "toutiao": (".toutiao.com", "mp.toutiao.com", "www.toutiao.com"),
        "sohu": (".sohu.com", "mp.sohu.com", "www.sohu.com"),
    }.get(platform, ())
    login_cookie_names = {
        "toutiao": {
            "uid_tt",
            "uid_tt_ss",
            "sessionid",
            "sessionid_ss",
            "sid_tt",
            "sid_guard",
            "sid_ucp_v1",
            "ssid_ucp_v1",
        },
        "sohu": set(),
    }.get(platform, set())
    for cookie in cookies:
        domain = str(cookie.get("domain", ""))
        if login_cookie_names and cookie.get("name") not in login_cookie_names:
            continue
        if any(auth_domain in domain or domain in auth_domain for auth_domain in domains):
            return True
    return False


def _stop_process(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except Exception:
            proc.kill()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
