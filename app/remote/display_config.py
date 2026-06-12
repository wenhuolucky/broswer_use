from __future__ import annotations

import os


DEFAULT_REMOTE_BROWSER_WINDOW_WIDTH = 1440
DEFAULT_REMOTE_BROWSER_WINDOW_HEIGHT = 900
DEFAULT_REMOTE_VIEWER_MAX_WIDTH = 1440
DEFAULT_REMOTE_VIEWER_MAX_HEIGHT = 900
DEFAULT_REMOTE_VIEWER_JPEG_QUALITY = 85

MIN_REMOTE_DISPLAY_WIDTH = 800
MIN_REMOTE_DISPLAY_HEIGHT = 600
MAX_REMOTE_DISPLAY_WIDTH = 3840
MAX_REMOTE_DISPLAY_HEIGHT = 2160
MIN_REMOTE_VIEWER_JPEG_QUALITY = 40
MAX_REMOTE_VIEWER_JPEG_QUALITY = 95

# 远程登录 KasmVNC 串流画质。注意：KasmVNC 的质量刻度是 0-9，与上面
# REMOTE_VIEWER_JPEG_QUALITY（0-100，走 CDP screencast 的发布预览）是两套
# 独立机制，互不影响。
DEFAULT_REMOTE_LOGIN_KASMVNC_QUALITY_MIN = 7
DEFAULT_REMOTE_LOGIN_KASMVNC_QUALITY_MAX = 9
DEFAULT_REMOTE_LOGIN_KASMVNC_VIDEO_QUALITY = 7
MIN_KASMVNC_QUALITY = 0
MAX_KASMVNC_QUALITY = 9


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


def get_remote_login_kasmvnc_options() -> dict:
    """远程登录 KasmVNC 串流的画质参数。

    KasmVNC 默认会在画面持续变化时（如登录页的动态背景视频）自动切到
    "视频模式"并大幅压低 JPEG/WebP 质量，导致画面"刚连上清晰、几秒后变糊"。
    这里把动态质量下限和视频模式质量都抬高，保证登录页（验证码/二维码）
    始终清晰。质量刻度 0-9（越大越清晰）。
    """
    quality_min = _read_int_env(
        "REMOTE_LOGIN_KASMVNC_QUALITY_MIN",
        DEFAULT_REMOTE_LOGIN_KASMVNC_QUALITY_MIN,
        MIN_KASMVNC_QUALITY,
        MAX_KASMVNC_QUALITY,
    )
    quality_max = _read_int_env(
        "REMOTE_LOGIN_KASMVNC_QUALITY_MAX",
        DEFAULT_REMOTE_LOGIN_KASMVNC_QUALITY_MAX,
        MIN_KASMVNC_QUALITY,
        MAX_KASMVNC_QUALITY,
    )
    # 下限不能高于上限，否则 KasmVNC 行为未定义
    quality_min = min(quality_min, quality_max)
    video_quality = _read_int_env(
        "REMOTE_LOGIN_KASMVNC_VIDEO_QUALITY",
        DEFAULT_REMOTE_LOGIN_KASMVNC_VIDEO_QUALITY,
        MIN_KASMVNC_QUALITY,
        MAX_KASMVNC_QUALITY,
    )
    return {
        "quality_min": quality_min,
        "quality_max": quality_max,
        "video_quality": video_quality,
    }
