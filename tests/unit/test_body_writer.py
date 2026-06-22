"""Body writer tool helper tests."""

from __future__ import annotations

from app.publishing.tools.body_writer import (
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
