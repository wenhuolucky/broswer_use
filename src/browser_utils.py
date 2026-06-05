"""
浏览器工具模块 — Edge / Chrome 路径检测 + CDP 调试端口连接
"""

import json
import glob
import os
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Optional


def get_edge_path() -> str:
    """
    获取 Edge 浏览器可执行文件路径。
    优先级：.env 配置 > 常见安装路径。
    """
    configured = _get_configured_browser_path()
    if configured:
        return configured

    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in candidates:
        if Path(p).exists():
            return p

    raise FileNotFoundError(
        "找不到 Edge 浏览器。请在 .env 中设置 BROWSER_EXECUTABLE_PATH，"
        "或安装 Microsoft Edge 到默认路径。"
    )


def _get_configured_browser_path() -> str | None:
    env_path = os.getenv("BROWSER_EXECUTABLE_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    return None


def _get_playwright_browser_path() -> str | None:
    candidates = sorted(
        glob.glob("/ms-playwright/chromium-*/chrome-linux/chrome")
        + glob.glob("/ms-playwright/chromium-*/chrome-linux64/chrome")
        + glob.glob("/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome")
        + glob.glob("/root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")
    )
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def get_chrome_path() -> str:
    """
    获取 Chrome 浏览器可执行文件路径。
    优先级：.env 配置 > 常见安装路径。
    """
    configured = _get_configured_browser_path()
    if configured:
        return configured

    playwright_path = _get_playwright_browser_path()
    if playwright_path:
        return playwright_path

    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for p in candidates:
        if Path(p).exists():
            return str(p)
    found = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chrome")
    )
    if found:
        return found

    raise FileNotFoundError(
        "找不到 Chrome 浏览器。请在 .env 中设置 BROWSER_EXECUTABLE_PATH，"
        "或安装 Google Chrome 到默认路径。"
    )


def get_browser_path(browser: str = "auto") -> str:
    """
    获取浏览器路径，支持 Edge 和 Chrome。

    Args:
        browser: "auto"（自动检测）/ "edge" / "chrome"
                 auto 时优先检测 Edge，其次 Chrome
    """
    # 如果 .env 显式指定了路径，优先使用
    env_path = os.getenv("BROWSER_EXECUTABLE_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    # 从 .env 或命令行参数获取浏览器类型
    env_browser = os.getenv("BROWSER_TYPE", "").strip().lower()

    if browser == "auto":
        browser = env_browser if env_browser in ("edge", "chrome") else "auto"

    if browser == "chrome" or env_browser == "chrome":
        return get_chrome_path()

    if browser == "edge" or env_browser == "edge":
        return get_edge_path()

    # auto 模式：优先 Chrome，其次 Edge
    try:
        return get_chrome_path()
    except FileNotFoundError:
        return get_edge_path()


def get_cdp_url(cdp_port: int, max_retries: int = 10) -> str:
    """
    通过 CDP 调试端口获取浏览器的 WebSocket URL。
    适用于任何 Chromium 浏览器（Edge、Chrome 等）。
    """
    ws_url = None
    for _ in range(max_retries):
        try:
            resp = urllib.request.urlopen(f"http://localhost:{cdp_port}/json", timeout=3)
            pages = json.loads(resp.read())
            if pages:
                ws_url = pages[0].get("webSocketDebuggerUrl")
                if ws_url:
                    break
        except Exception:
            pass
        time.sleep(0.5)

    if not ws_url:
        raise RuntimeError(f"无法从浏览器 (localhost:{cdp_port}) 获取 CDP URL")

    return ws_url
