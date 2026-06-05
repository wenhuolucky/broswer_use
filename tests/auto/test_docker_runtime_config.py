from __future__ import annotations

import importlib
from pathlib import Path


def test_auto_settings_allow_runtime_directories_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUTO_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("AUTO_REMOTE_PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("REMOTE_LOGIN_TIMEOUT_SECONDS", "123")

    import auto.settings as settings

    reloaded = importlib.reload(settings)

    assert reloaded.DATA_DIR == tmp_path / "data"
    assert reloaded.COOKIE_DIR == tmp_path / "data" / "cookies"
    assert reloaded.JOB_DIR == tmp_path / "data" / "jobs"
    assert reloaded.LOG_DIR == tmp_path / "logs"
    assert reloaded.REMOTE_PROFILE_DIR == tmp_path / "profiles"
    assert reloaded.REMOTE_LOGIN_TIMEOUT_SECONDS == 123

    monkeypatch.delenv("AUTO_DATA_DIR")
    monkeypatch.delenv("AUTO_LOG_DIR")
    monkeypatch.delenv("AUTO_REMOTE_PROFILE_DIR")
    monkeypatch.delenv("REMOTE_LOGIN_TIMEOUT_SECONDS")
    importlib.reload(settings)


def test_auto_dockerfile_uses_python_311_compatible_playwright_image():
    dockerfile = Path("Dockerfile.auto").read_text(encoding="utf-8")

    assert "mcr.microsoft.com/playwright/python:v1.50.0-noble" in dockerfile
    assert "v1.50.0-jammy" not in dockerfile
