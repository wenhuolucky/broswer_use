from __future__ import annotations

import os


# 登录流的 Chrome 窗口尺寸与 REMOTE_VNC_SCREEN（默认 1920x1080）保持一致，
# 让浏览器铺满 KasmVNC 显示、避免黑边；并与 a2a-browser 的 1920x1080 对齐。
DEFAULT_REMOTE_BROWSER_WINDOW_WIDTH = 1920
DEFAULT_REMOTE_BROWSER_WINDOW_HEIGHT = 1080
DEFAULT_REMOTE_VIEWER_MAX_WIDTH = 1440
DEFAULT_REMOTE_VIEWER_MAX_HEIGHT = 900
DEFAULT_REMOTE_VIEWER_JPEG_QUALITY = 85

MIN_REMOTE_DISPLAY_WIDTH = 800
MIN_REMOTE_DISPLAY_HEIGHT = 600
MAX_REMOTE_DISPLAY_WIDTH = 3840
MAX_REMOTE_DISPLAY_HEIGHT = 2160
MIN_REMOTE_VIEWER_JPEG_QUALITY = 40
MAX_REMOTE_VIEWER_JPEG_QUALITY = 95


def _read_int_env(name: str, default: int, min_value: int, max_value: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))


def get_remote_browser_window_size() -> tuple[int, int]:
    width = _read_int_env(
        "REMOTE_BROWSER_WINDOW_WIDTH",
        DEFAULT_REMOTE_BROWSER_WINDOW_WIDTH,
        MIN_REMOTE_DISPLAY_WIDTH,
        MAX_REMOTE_DISPLAY_WIDTH,
    )
    height = _read_int_env(
        "REMOTE_BROWSER_WINDOW_HEIGHT",
        DEFAULT_REMOTE_BROWSER_WINDOW_HEIGHT,
        MIN_REMOTE_DISPLAY_HEIGHT,
        MAX_REMOTE_DISPLAY_HEIGHT,
    )
    return width, height


def get_remote_viewer_screencast_options() -> dict:
    return {
        "format": "jpeg",
        "quality": _read_int_env(
            "REMOTE_VIEWER_JPEG_QUALITY",
            DEFAULT_REMOTE_VIEWER_JPEG_QUALITY,
            MIN_REMOTE_VIEWER_JPEG_QUALITY,
            MAX_REMOTE_VIEWER_JPEG_QUALITY,
        ),
        "maxWidth": _read_int_env(
            "REMOTE_VIEWER_MAX_WIDTH",
            DEFAULT_REMOTE_VIEWER_MAX_WIDTH,
            MIN_REMOTE_DISPLAY_WIDTH,
            MAX_REMOTE_DISPLAY_WIDTH,
        ),
        "maxHeight": _read_int_env(
            "REMOTE_VIEWER_MAX_HEIGHT",
            DEFAULT_REMOTE_VIEWER_MAX_HEIGHT,
            MIN_REMOTE_DISPLAY_HEIGHT,
            MAX_REMOTE_DISPLAY_HEIGHT,
        ),
    }
