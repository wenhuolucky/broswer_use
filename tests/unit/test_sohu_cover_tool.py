"""Sohu cover selection tool tests."""

from __future__ import annotations

import json

import pytest

from app.publishing.tools.sohu_cover import (
    SohuCoverSetter,
    build_sohu_cover_js,
    make_sohu_cover_failure,
    make_sohu_cover_success,
)


def test_sohu_cover_success_result_has_stable_shape() -> None:
    result = make_sohu_cover_success(source="material_library", detail="selected first material image")

    assert result == {
        "ok": True,
        "source": "material_library",
        "selected": True,
        "confirmed": True,
        "cover_applied": True,
        "reason": "",
        "detail": "selected first material image",
    }


def test_sohu_cover_failure_result_has_stable_shape() -> None:
    result = make_sohu_cover_failure("material_image_not_found", source="material_library", detail="empty")

    assert result == {
        "ok": False,
        "source": "material_library",
        "selected": False,
        "confirmed": False,
        "cover_applied": False,
        "reason": "material_image_not_found",
        "detail": "empty",
    }


def test_sohu_cover_js_is_browser_use_compatible_and_avoids_file_upload() -> None:
    script = build_sohu_cover_js()

    assert script.strip().startswith("(...args) =>")
    assert "body_image" in script
    assert "material_library" in script
    assert "input[type=\"file\"]" in script
    assert "本地上传" in script
    assert "上传图片" in script
    assert "isCoverTriggerBlocked" in script
    assert "candidate_texts" in script
    assert ".click()" in script


@pytest.mark.asyncio
async def test_sohu_cover_setter_accepts_json_string_evaluate_result() -> None:
    class FakePage:
        def __init__(self):
            self.calls = []

        async def evaluate(self, script, arg=None):
            self.calls.append((script, arg))
            if arg is not None and not script.strip().startswith("(...args) =>"):
                raise ValueError("JavaScript code must start with (...args) =>")
            return json.dumps(
                {
                    "ok": True,
                    "source": "body_image",
                    "selected": True,
                    "confirmed": True,
                    "cover_applied": True,
                    "reason": "",
                    "detail": "selected first body image",
                }
            )

    class FakeSession:
        def __init__(self):
            self.page = FakePage()

        async def must_get_current_page(self):
            return self.page

    setter = SohuCoverSetter()
    session = FakeSession()

    result = await setter.set_cover(session)

    assert result["ok"] is True
    assert result["source"] == "body_image"
    assert result["cover_applied"] is True
    assert session.page.calls[0][1] == {}


@pytest.mark.asyncio
async def test_sohu_cover_setter_reports_invalid_evaluate_result() -> None:
    class FakePage:
        async def evaluate(self, *_args):
            return "not-json"

    class FakeSession:
        async def get_current_page(self):
            return FakePage()

    result = await SohuCoverSetter().set_cover(FakeSession())

    assert result["ok"] is False
    assert result["reason"] == "cover_result_invalid"


@pytest.mark.asyncio
async def test_sohu_cover_setter_reports_page_unavailable() -> None:
    result = await SohuCoverSetter().set_cover(None)

    assert result["ok"] is False
    assert result["reason"] == "page_unavailable"
