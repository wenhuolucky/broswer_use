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
from urllib.parse import quote, urlparse

from app.core.config import CLOUDFLARED_PROXY_KEEPALIVE_TIMEOUT, REMOTE_PROFILE_DIR
from app.remote.display_config import get_remote_browser_window_size
from app.streaming import kasmvnc
from app.streaming.display_pool import DisplayPool, Slot

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PROJECT_ROOT / "tools"
if TOOLS_DIR.exists() and str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

REMOTE_LOGIN_SERVICE_PORT = int(os.getenv("REMOTE_LOGIN_SERVICE_PORT", os.getenv("PORT", "19000")))
REMOTE_LOGIN_MAX_SESSIONS = int(os.getenv("MAX_REMOTE_LOGIN_SESSIONS", "4"))
REMOTE_LOGIN_DISPLAY_BASE = int(os.getenv("DISPLAY_BASE", "100"))
REMOTE_LOGIN_KASMVNC_PORT_BASE = int(os.getenv("KASMVNC_PORT_BASE", "6900"))
REMOTE_LOGIN_KASMVNC_BIN = os.getenv("KASMVNC_BIN", "Xvnc")
REMOTE_LOGIN_KASMVNC_WWW = os.getenv("KASMVNC_WWW", "")
REMOTE_LOGIN_VNC_SCREEN = os.getenv("REMOTE_VNC_SCREEN", "1440x900x24")

_remote_display_pool = DisplayPool(
    size=REMOTE_LOGIN_MAX_SESSIONS,
    display_base=REMOTE_LOGIN_DISPLAY_BASE,
    port_base=REMOTE_LOGIN_KASMVNC_PORT_BASE,
)

REMOTE_LOGIN_ALLOWED_HOSTS = {
    "toutiao": (
        "toutiao.com",
        # 头条登录过程中嵌入的第三方域名（验证码、SSO、CDN 等）
        "snssdk.com",
        "byteimg.com",
        "pstatp.com",
        "bytedance.com",
        "volces.com",
        "bytegoofy.com",
        "toutiaoapi.com",
        "amemv.com",
    ),
    "sohu": (
        "sohu.com",
        # 搜狐登录过程中嵌入的第三方域名（腾讯滑块验证码、CDN 等）
        "gtimg.com",  # turing.captcha.gtimg.com（腾讯滑块验证码）
        "gtimg.cn",
        "qq.com",
        "qpic.cn",
        "qlogo.cn",
        "smtcdns.net",
        "idqqimg.com",
    ),
}


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
    playwright: object | None = None
    browser: object | None = None
    stream: object | None = None
    slot: Slot | None = None
    vnc_token: str = ""


class RemoteLoginRunner:
    """Isolated facade for remote cookie acquisition."""

    def __init__(
        self,
        starter: Callable[[str, str, str], Awaitable[str]] | None = None,
        save_cookie: Callable[[str, str, list[dict]], None] | None = None,
        on_cookie_ready: Callable[[str, list[dict]], Awaitable[None]] | None = None,
        logger_factory: Callable[[str], LoggerAdapter] | None = None,
    ):
        self._starter = starter
        self._save_cookie = save_cookie
        self._on_cookie_ready = on_cookie_ready
        self._logger_factory = logger_factory
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
        cookies = await self._extract_session_cookies(session)
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

    async def _extract_session_cookies(self, session: RemoteLoginSession) -> list[dict]:
        if session.browser and getattr(session.browser, "contexts", None):
            return await session.browser.contexts[0].cookies()
        raise RuntimeError("remote browser context unavailable")

    async def _start_real_session(self, platform: str, user_id: str, session_id: str) -> RemoteLoginSession:
        slot = await _remote_display_pool.acquire()
        stream = None
        chrome_proc = None
        tunnel_proc = None
        pw = None
        browser = None
        try:
            cdp_port = _find_free_port()
            stream = await kasmvnc.start_stream(
                slot,
                kasmvnc_bin=REMOTE_LOGIN_KASMVNC_BIN,
                screen=REMOTE_LOGIN_VNC_SCREEN,
                www_dir=REMOTE_LOGIN_KASMVNC_WWW,
            )
            login_url = _login_url_for(platform)
            chrome_proc = await _launch_chrome(cdp_port, session_id, display=slot.display, app_url=login_url)
            pw, browser, page = await _attach_browser(cdp_port, login_url)
            _install_remote_navigation_guard(page, platform, login_url, self._logger(session_id))
            await page.goto(login_url, timeout=30000, wait_until="domcontentloaded")
            await _enforce_remote_navigation_guard(page, platform, login_url, self._logger(session_id))
            tunnel_proc, tunnel_base_url = await _start_cloudflared_tunnel(REMOTE_LOGIN_SERVICE_PORT)
            return RemoteLoginSession(
                session_id=session_id,
                platform=platform,
                user_id=user_id,
                login_url=_build_vnc_url(tunnel_base_url, session_id, session_id),
                cdp_port=cdp_port,
                viewer_port=slot.web_port,
                chrome_proc=chrome_proc,
                tunnel_proc=tunnel_proc,
                playwright=pw,
                browser=browser,
                stream=stream,
                slot=slot,
                vnc_token=session_id,
            )
        except Exception:
            if tunnel_proc and tunnel_proc.poll() is None:
                _stop_process(tunnel_proc)
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            if pw:
                try:
                    await pw.stop()
                except Exception:
                    pass
            if chrome_proc and chrome_proc.poll() is None:
                _stop_process(chrome_proc)
            if stream:
                try:
                    await stream.stop()
                except Exception:
                    pass
            await _remote_display_pool.release(slot)
            raise

    async def cleanup(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if not session:
            return
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
        if session.stream:
            try:
                await session.stream.stop()
            except Exception:
                pass
        if session.slot:
            try:
                await _remote_display_pool.release(session.slot)
            except Exception:
                pass
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


def _build_vnc_url(base_url: str, session_id: str, token: str) -> str:
    ws_path = quote(f"vnc/{session_id}/websockify", safe="")
    return f"{base_url.rstrip('/')}/vnc/{session_id}/?token={token}&path={ws_path}"


def _find_free_port() -> int:
    """在配置的端口范围内找一个空闲端口（默认 8000-9999）。

    服务器端口白名单通常限制在 8000-9999，OS 随机分配的端口（32768-60999）
    会被防火墙/白名单拦截，导致 Chrome bind() 失败、CDP 端口连不上。
    通过环境变量 CDP_PORT_RANGE 可配置范围，默认 8000-9999。
    """
    port_range = os.getenv("CDP_PORT_RANGE", "8000-9999")
    try:
        start, end = map(int, port_range.split("-"))
    except (ValueError, AttributeError):
        start, end = 8000, 9999

    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port in range {port_range}")


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


async def _launch_chrome(
    cdp_port: int,
    session_id: str,
    display: str | None = None,
    app_url: str = "about:blank",
) -> subprocess.Popen:
    profile_dir = REMOTE_PROFILE_DIR / session_id
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    window_width, window_height = get_remote_browser_window_size()
    env = os.environ.copy()
    if display:
        env["DISPLAY"] = display
    args = _build_chrome_args(
        browser_path=_find_browser_path(),
        cdp_port=cdp_port,
        profile_dir=profile_dir,
        window_size=(window_width, window_height),
        app_url=app_url,
    )
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    await _wait_for_cdp(cdp_port, proc)
    return proc


def _build_chrome_args(
    browser_path: str,
    cdp_port: int,
    profile_dir: Path,
    window_size: tuple[int, int],
    app_url: str,
) -> list[str]:
    window_width, window_height = window_size
    return [
        browser_path,
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
        f"--app={app_url}",
    ]


async def _attach_browser(cdp_port: int, login_url: str):
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        browser = await asyncio.wait_for(
            pw.chromium.connect_over_cdp(_cdp_http_url(cdp_port)),
            timeout=15,
        )
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        return pw, browser, page
    except Exception:
        await pw.stop()
        raise


def _install_remote_navigation_guard(page, platform: str, login_url: str, logger) -> None:
    """安装远程登录 URL 守卫，防止用户点击链接跳转到非登录相关页面。

    关键改进（2026-06-11 修白屏问题）：
    - 增加 grace_period 机制：初始加载阶段（安装 guard 后的 15 秒内）
      不触发拦截，避免 login_url 自身的重定向链被误拦截导致白屏。
    - 只在用户主动点击链接触发的后续导航才拦截。
    """
    grace_until = asyncio.get_event_loop().time() + 15.0  # 15 秒保护期
    guard_state = {"last_intercepted_url": "", "intercept_count": 0}

    def on_frame_navigated(frame):
        if getattr(frame, "parent_frame", None):
            return

        # grace period 内不拦截（让初始加载和重定向链跑完）
        now = asyncio.get_event_loop().time()
        if now < grace_until:
            return

        try:
            asyncio.create_task(
                _enforce_remote_navigation_guard(page, platform, login_url, logger, guard_state)
            )
        except RuntimeError:
            pass

    try:
        page.on("framenavigated", on_frame_navigated)
    except Exception as exc:
        if logger:
            logger.warning("远程登录 URL 守卫安装失败: %s", exc)


async def _enforce_remote_navigation_guard(
    page, platform: str, login_url: str, logger, guard_state: dict | None = None
) -> None:
    current_url = str(getattr(page, "url", "") or "")
    if _is_remote_login_url_allowed(platform, current_url):
        return

    # 防止同一 URL 被反复拦截（避免"拦截 → goto → 又触发 guard → 又拦截"的死循环）
    if guard_state is not None:
        if current_url == guard_state.get("last_intercepted_url"):
            return
        guard_state["last_intercepted_url"] = current_url
        guard_state["intercept_count"] = guard_state.get("intercept_count", 0) + 1
        # 同一 session 拦截超过 5 次，停止拦截，避免死循环把页面搞挂
        if guard_state["intercept_count"] > 5:
            if logger:
                logger.warning(
                    "远程登录 URL 守卫达到拦截上限，停止拦截 current_url=%s intercept_count=%s",
                    current_url,
                    guard_state["intercept_count"],
                )
            return

    if logger:
        logger.warning("远程登录 URL 被拦截 platform=%s url=%s", platform, current_url)
    try:
        await page.goto(login_url, timeout=30000, wait_until="domcontentloaded")
    except Exception as exc:
        if logger:
            logger.warning("远程登录 URL 守卫跳回 login_url 失败: %s", exc)


def _is_remote_login_url_allowed(platform: str, url: str) -> bool:
    if not url:
        return True
    if url.startswith(("about:", "chrome:", "devtools:")):
        return True
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return True
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    allowed_hosts = REMOTE_LOGIN_ALLOWED_HOSTS.get(platform, ())
    return any(host == allowed_host or host.endswith(f".{allowed_host}") for allowed_host in allowed_hosts)


async def _start_cloudflared_tunnel(local_port: int) -> tuple[subprocess.Popen, str]:
    # Layer 3: raise the cloudflared<->local-viewer idle-connection close
    # timeout above the default 1m30s so the hop is not reaped during quiet
    # periods. This is a hardening flag only; the primary keepalive lives in
    # vnc_proxy._bridge. Empty config value keeps cloudflared defaults.
    cmd = [_find_cloudflared_path(), "tunnel", "--url", f"http://127.0.0.1:{local_port}", "--no-autoupdate"]
    if CLOUDFLARED_PROXY_KEEPALIVE_TIMEOUT:
        cmd += ["--proxy-keepalive-timeout", CLOUDFLARED_PROXY_KEEPALIVE_TIMEOUT]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    recent_output: list[str] = []
    try:
        while True:
            if proc.poll() is not None:
                detail = "\n".join(recent_output[-10:])
                raise RuntimeError(f"cloudflared exited unexpectedly (code={proc.poll()}): {detail}".rstrip())
            line = proc.stdout.readline()
            if not line:
                await asyncio.sleep(0.1)
                continue
            recent_output.append(line.strip())
            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if match:
                return proc, match.group(0)
    except Exception:
        _stop_process(proc)
        raise


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
