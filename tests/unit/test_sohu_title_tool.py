"""Sohu title setter tool tests."""

from __future__ import annotations

import json

import pytest

from app.publishing.tools.sohu_title import (
    SohuTitleSetter,
    build_sohu_title_js,
    make_sohu_title_failure,
    make_sohu_title_success,
)


def test_sohu_title_success_result_has_stable_shape() -> None:
    result = make_sohu_title_success(
        expected_title="expected",
        actual_title="expected",
        method="dom_input_events",
    )

    assert result == {
        "ok": True,
        "expected_title": "expected",
        "actual_title": "expected",
        "verified": True,
        "method": "dom_input_events",
        "reason": "",
        "detail": "",
    }


def test_sohu_title_failure_result_has_stable_shape() -> None:
    result = make_sohu_title_failure(
        "title_mismatch",
        expected_title="expected",
        actual_title="actual",
        method="dom_input_events",
        detail="length mismatch",
    )

    assert result == {
        "ok": False,
        "expected_title": "expected",
        "actual_title": "actual",
        "verified": False,
        "method": "dom_input_events",
        "reason": "title_mismatch",
        "detail": "length mismatch",
    }


def test_sohu_title_js_is_browser_use_compatible_and_traverses_shadow_dom() -> None:
    script = build_sohu_title_js()

    assert script.strip().startswith("(...args) =>")
    assert "shadowRoot" in script
    assert "HTMLInputElement.prototype" in script
    assert "dispatchEvent" in script
    assert "title_input_not_found" in script


@pytest.mark.asyncio
async def test_sohu_title_setter_accepts_json_string_evaluate_result() -> None:
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
                    "expected_title": arg["title"],
                    "actual_title": arg["title"],
                    "verified": True,
                    "method": "dom_input_events",
                    "reason": "",
                    "detail": "",
                }
            )

    class FakeSession:
        def __init__(self):
            self.page = FakePage()

        async def must_get_current_page(self):
            return self.page

    session = FakeSession()
    result = await SohuTitleSetter("expected title").set_title(session)

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["actual_title"] == "expected title"
    assert session.page.calls[0][1] == {"title": "expected title"}


@pytest.mark.asyncio
async def test_sohu_title_setter_reports_invalid_evaluate_result() -> None:
    class FakePage:
        async def evaluate(self, *_args):
            return "not-json"

    class FakeSession:
        async def get_current_page(self):
            return FakePage()

    result = await SohuTitleSetter("expected").set_title(FakeSession())

    assert result["ok"] is False
    assert result["reason"] == "title_result_invalid"
