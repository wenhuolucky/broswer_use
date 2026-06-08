from app.remote.display_config import (
    DEFAULT_REMOTE_BROWSER_WINDOW_HEIGHT,
    DEFAULT_REMOTE_BROWSER_WINDOW_WIDTH,
    DEFAULT_REMOTE_VIEWER_JPEG_QUALITY,
    DEFAULT_REMOTE_VIEWER_MAX_HEIGHT,
    DEFAULT_REMOTE_VIEWER_MAX_WIDTH,
    get_remote_browser_window_size,
    get_remote_viewer_screencast_options,
)


def test_remote_display_defaults_are_high_enough_for_qr_login(monkeypatch):
    monkeypatch.delenv("REMOTE_BROWSER_WINDOW_WIDTH", raising=False)
    monkeypatch.delenv("REMOTE_BROWSER_WINDOW_HEIGHT", raising=False)
    monkeypatch.delenv("REMOTE_VIEWER_MAX_WIDTH", raising=False)
    monkeypatch.delenv("REMOTE_VIEWER_MAX_HEIGHT", raising=False)
    monkeypatch.delenv("REMOTE_VIEWER_JPEG_QUALITY", raising=False)

    assert get_remote_browser_window_size() == (
        DEFAULT_REMOTE_BROWSER_WINDOW_WIDTH,
        DEFAULT_REMOTE_BROWSER_WINDOW_HEIGHT,
    )
    assert get_remote_viewer_screencast_options() == {
        "format": "jpeg",
        "quality": DEFAULT_REMOTE_VIEWER_JPEG_QUALITY,
        "maxWidth": DEFAULT_REMOTE_VIEWER_MAX_WIDTH,
        "maxHeight": DEFAULT_REMOTE_VIEWER_MAX_HEIGHT,
    }


def test_remote_display_env_values_are_clamped(monkeypatch):
    monkeypatch.setenv("REMOTE_BROWSER_WINDOW_WIDTH", "300")
    monkeypatch.setenv("REMOTE_BROWSER_WINDOW_HEIGHT", "4000")
    monkeypatch.setenv("REMOTE_VIEWER_MAX_WIDTH", "bad")
    monkeypatch.setenv("REMOTE_VIEWER_MAX_HEIGHT", "5000")
    monkeypatch.setenv("REMOTE_VIEWER_JPEG_QUALITY", "120")

    assert get_remote_browser_window_size() == (800, 2160)
    assert get_remote_viewer_screencast_options() == {
        "format": "jpeg",
        "quality": 95,
        "maxWidth": DEFAULT_REMOTE_VIEWER_MAX_WIDTH,
        "maxHeight": 2160,
    }
