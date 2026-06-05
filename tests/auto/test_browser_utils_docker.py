from __future__ import annotations

import os

from src import browser_utils


def test_browser_utils_prefers_browser_executable_path(monkeypatch):
    monkeypatch.setenv("BROWSER_EXECUTABLE_PATH", "/ms-playwright/chromium-1223/chrome-linux64/chrome")
    monkeypatch.setattr(
        browser_utils.Path,
        "exists",
        lambda self: self.as_posix() == "/ms-playwright/chromium-1223/chrome-linux64/chrome",
    )

    assert browser_utils.get_browser_path() == "/ms-playwright/chromium-1223/chrome-linux64/chrome"


def test_browser_utils_finds_playwright_chromium_bundle(monkeypatch):
    paths = ["/ms-playwright/chromium-1223/chrome-linux64/chrome"]
    monkeypatch.delenv("BROWSER_EXECUTABLE_PATH", raising=False)
    monkeypatch.setattr(browser_utils.Path, "exists", lambda self: self.as_posix() in paths)
    monkeypatch.setattr(browser_utils.glob, "glob", lambda _pattern: paths)
    monkeypatch.setattr(browser_utils.shutil, "which", lambda _name: None)
    monkeypatch.setattr(os, "environ", {})

    assert browser_utils.get_browser_path() == "/ms-playwright/chromium-1223/chrome-linux64/chrome"
