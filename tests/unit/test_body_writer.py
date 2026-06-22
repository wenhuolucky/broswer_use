"""Body writer tool helper tests."""

from __future__ import annotations

import json

import pytest

from app.publishing.tools.body_writer import (
    BodyWritePayload,
    BodyWriter,
    build_clipboard_html_js,
    build_clipboard_text_js,
    make_body_write_failure,
    make_body_write_success,
)


def test_plain_text_clipboard_js_uses_clipboard_without_dom_body_injection() -> None:
    script = build_clipboard_text_js()

    assert "navigator.clipboard.writeText" in script
    assert "document.execCommand('copy')" in script
    assert ".innerHTML" not in script
    assert "insertHTML" not in script


def test_rich_html_clipboard_js_uses_clipboard_item_without_editor_injection() -> None:
    script = build_clipboard_html_js()

    assert "ClipboardItem" in script
    assert "'text/html'" in script
    assert "'text/plain'" in script
    assert ".innerHTML" not in script
    assert "insertHTML" not in script


def test_body_write_success_result_has_stable_shape() -> None:
    result = make_body_write_success(
        mode="plain_text",
        method="navigator.clipboard.writeText",
        editor_text_length=123,
        probe_found=True,
    )

    assert result == {
        "ok": True,
        "mode": "plain_text",
        "method": "navigator.clipboard.writeText",
        "editor_text_length": 123,
        "probe_found": True,
        "reason": "",
    }


def test_body_write_failure_result_has_reason() -> None:
    result = make_body_write_failure(
        mode="rich_html",
        reason="paste_probe_not_found",
        method="ClipboardItem",
        editor_text_length=20,
        probe_found=False,
    )

    assert result == {
        "ok": False,
        "mode": "rich_html",
        "method": "ClipboardItem",
        "editor_text_length": 20,
        "probe_found": False,
        "reason": "paste_probe_not_found",
    }


@pytest.mark.asyncio
async def test_body_writer_returns_invalid_focus_result_when_evaluate_returns_string() -> None:
    class FakePage:
        keyboard = None

        async def evaluate(self, *_args):
            return "not-a-dict"

    class FakeSession:
        async def get_current_page(self):
            return FakePage()

    writer = BodyWriter(
        BodyWritePayload(
            plain_text="完整正文内容" * 20,
            rich_html="",
            body_probe="完整正文内容",
            platform_name="toutiao",
        )
    )

    result = await writer.paste_plain_text_body(FakeSession())

    assert result["ok"] is False
    assert result["reason"] == "focus_result_invalid"
    assert result["probe_found"] is False


@pytest.mark.asyncio
async def test_body_writer_accepts_json_string_evaluate_results() -> None:
    expected_text = "complete body text " * 20

    class FakeKeyboard:
        async def press(self, _keys):
            return None

    class FakePage:
        keyboard = FakeKeyboard()

        def __init__(self):
            self.calls = 0

        async def evaluate(self, *_args):
            self.calls += 1
            if self.calls == 1:
                return json.dumps({"ok": True, "reason": ""})
            if self.calls == 2:
                return json.dumps({"ok": True, "method": "navigator.clipboard.writeText"})
            return json.dumps(
                {
                    "editor_text_length": len(expected_text),
                    "editor_source": "contenteditable",
                    "probe_found": True,
                    "preview": expected_text[:120],
                    "text": expected_text,
                }
            )

    class FakeSession:
        async def get_current_page(self):
            return FakePage()

    writer = BodyWriter(
        BodyWritePayload(
            plain_text=expected_text,
            rich_html="",
            body_probe=expected_text[:30],
            platform_name="toutiao",
        )
    )

    result = await writer.paste_plain_text_body(FakeSession())

    assert result["ok"] is True
    assert result["method"] == "navigator.clipboard.writeText"
    assert result["editor_text_length"] == len(expected_text.replace(" ", ""))


@pytest.mark.asyncio
async def test_body_writer_rejects_probe_only_partial_body() -> None:
    expected_text = "很多人一提到养生，就会想到复杂的食谱、昂贵的补品、严格的作息。" * 10
    probe_only = "很多人一提到养生，就会想到复杂的食谱、昂贵的补品、严格的作息"

    class FakeKeyboard:
        async def press(self, _keys):
            return None

    class FakePage:
        keyboard = FakeKeyboard()

        def __init__(self):
            self.calls = 0

        async def evaluate(self, *_args):
            self.calls += 1
            if self.calls == 1:
                return {"ok": True}
            if self.calls == 2:
                return {"ok": True, "method": "navigator.clipboard.writeText"}
            return {
                "editor_text_length": len(probe_only),
                "expected_text_length": len(expected_text),
                "length_ratio": len(probe_only) / len(expected_text),
                "probe_found": True,
                "probe_start_found": True,
                "probe_middle_found": False,
                "probe_end_found": False,
                "preview": probe_only,
            }

    class FakeSession:
        async def get_current_page(self):
            return FakePage()

    writer = BodyWriter(
        BodyWritePayload(
            plain_text=expected_text,
            rich_html="",
            body_probe=probe_only,
            platform_name="toutiao",
        )
    )

    result = await writer.paste_plain_text_body(FakeSession())

    assert result["ok"] is False
    assert result["reason"] == "body_incomplete"
    assert result["editor_text_length"] == len(probe_only)
    assert result["expected_text_length"] == len(expected_text)
