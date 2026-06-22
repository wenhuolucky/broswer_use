"""Browser-use tools for writing article body content."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BodyWritePayload:
    """Content held server-side so the LLM does not pass large body arguments."""

    plain_text: str
    rich_html: str
    body_probe: str
    platform_name: str = ""

    @property
    def expected_text_length(self) -> int:
        return len(self.plain_text or "")


def make_body_write_success(
    *,
    mode: str,
    method: str,
    editor_text_length: int,
    probe_found: bool,
    expected_text_length: int = 0,
    length_ratio: float = 0.0,
    probe_start_found: bool | None = None,
    probe_middle_found: bool | None = None,
    probe_end_found: bool | None = None,
) -> dict:
    result = {
        "ok": True,
        "mode": mode,
        "method": method,
        "editor_text_length": int(editor_text_length or 0),
        "probe_found": bool(probe_found),
        "reason": "",
    }
    if expected_text_length:
        result["expected_text_length"] = int(expected_text_length)
        result["length_ratio"] = round(float(length_ratio or 0.0), 4)
    if probe_start_found is not None:
        result["probe_start_found"] = bool(probe_start_found)
        result["probe_middle_found"] = bool(probe_middle_found)
        result["probe_end_found"] = bool(probe_end_found)
    return result


def make_body_write_failure(
    *,
    mode: str,
    reason: str,
    method: str = "",
    editor_text_length: int = 0,
    probe_found: bool = False,
    expected_text_length: int = 0,
    length_ratio: float = 0.0,
    probe_start_found: bool | None = None,
    probe_middle_found: bool | None = None,
    probe_end_found: bool | None = None,
) -> dict:
    result = {
        "ok": False,
        "mode": mode,
        "method": method or "",
        "editor_text_length": int(editor_text_length or 0),
        "probe_found": bool(probe_found),
        "reason": reason or "unknown",
    }
    if expected_text_length:
        result["expected_text_length"] = int(expected_text_length)
        result["length_ratio"] = round(float(length_ratio or 0.0), 4)
    if probe_start_found is not None:
        result["probe_start_found"] = bool(probe_start_found)
        result["probe_middle_found"] = bool(probe_middle_found)
        result["probe_end_found"] = bool(probe_end_found)
    return result


def compact_text(text: str) -> str:
    return "".join((text or "").split())


def body_integrity_probes(text: str, probe_size: int = 30) -> dict:
    compacted = compact_text(text)
    if not compacted:
        return {"start": "", "middle": "", "end": ""}
    size = min(probe_size, len(compacted))
    middle_start = max((len(compacted) - size) // 2, 0)
    return {
        "start": compacted[:size],
        "middle": compacted[middle_start : middle_start + size],
        "end": compacted[-size:],
    }


def evaluate_body_integrity(editor_text: str, expected_text: str) -> dict:
    normalized_editor = compact_text(editor_text)
    normalized_expected = compact_text(expected_text)
    expected_len = len(normalized_expected)
    actual_len = len(normalized_editor)
    probes = body_integrity_probes(normalized_expected)
    start_found = bool(probes["start"]) and probes["start"] in normalized_editor
    middle_found = bool(probes["middle"]) and probes["middle"] in normalized_editor
    end_found = bool(probes["end"]) and probes["end"] in normalized_editor
    found_count = sum(1 for value in (start_found, middle_found, end_found) if value)
    length_ratio = (actual_len / expected_len) if expected_len else 0.0
    min_ratio = 0.9 if expected_len >= 80 else 0.8
    return {
        "ok": bool(expected_len) and actual_len >= int(expected_len * min_ratio) and found_count >= 2,
        "editor_text_length": actual_len,
        "expected_text_length": expected_len,
        "length_ratio": length_ratio,
        "probe_found": start_found,
        "probe_start_found": start_found,
        "probe_middle_found": middle_found,
        "probe_end_found": end_found,
    }


def normalize_evaluate_result(value: Any) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def build_clipboard_text_js() -> str:
    return r"""
(...args) => {
  const bodyText = args[0] || '';
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(bodyText).then(
        () => ({ ok: true, method: 'navigator.clipboard.writeText' }),
        () => ({ ok: false, method: 'navigator.clipboard.writeText' })
      );
    }
  } catch (error) {}
  try {
    const textarea = document.createElement('textarea');
    textarea.value = bodyText;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return { ok: !!ok, method: 'execCommand' };
  } catch (error) {
    return { ok: false, method: '', error: String(error) };
  }
}
"""


def build_clipboard_html_js() -> str:
    return r"""
(...args) => {
  const payload = args[0] || {};
  const htmlContent = payload.html || '';
  const plainText = payload.text || '';
  try {
    if (navigator.clipboard && window.ClipboardItem) {
      const item = new ClipboardItem({
        'text/html': new Blob([htmlContent], { type: 'text/html' }),
        'text/plain': new Blob([plainText], { type: 'text/plain' })
      });
      return navigator.clipboard.write([item]).then(
        () => ({ ok: true, method: 'ClipboardItem' }),
        () => ({ ok: false, method: 'ClipboardItem' })
      );
    }
  } catch (error) {}
  try {
    const textarea = document.createElement('textarea');
    textarea.value = plainText;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return { ok: !!ok, method: 'execCommand' };
  } catch (error) {
    return { ok: false, method: '', error: String(error) };
  }
}
"""


FOCUS_EDITOR_JS = r"""
() => {
  const visible = (node) => {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.visibility !== 'hidden'
      && style.display !== 'none'
      && rect.width > 0
      && rect.height > 0;
  };
  let best = null;
  const candidates = Array.from(document.querySelectorAll('[contenteditable="true"], textarea'));
  for (const node of candidates) {
    if (!visible(node)) continue;
    const text = (node.innerText || node.textContent || node.value || '').trim();
    const rect = node.getBoundingClientRect();
    const score = text.length + Math.round(rect.width * rect.height / 10000);
    if (!best || score > best.score) {
      best = { node, score };
    }
  }
  if (!best) return { ok: false, reason: 'editor_not_found' };
  best.node.scrollIntoView({ block: 'center', inline: 'center' });
  best.node.focus();
  best.node.click();
  return { ok: true, reason: '', tag: best.node.tagName, contenteditable: best.node.getAttribute('contenteditable') || '' };
}
"""


READ_EDITOR_STATE_JS = r"""
(...args) => {
  const probe = args[0] || '';
  let best = null;
  const editables = Array.from(document.querySelectorAll('[contenteditable="true"], textarea'));
  for (const node of editables) {
    const text = (node.innerText || node.textContent || node.value || '').trim();
    const len = text.length;
    if (!best || len > best.len) {
      best = { text, len, source: node.tagName === 'TEXTAREA' ? 'textarea' : 'contenteditable' };
    }
  }
  const normalized = (best ? best.text : '').replace(/\s+/g, '');
  const normalizedProbe = probe.replace(/\s+/g, '');
  return {
    editor_text_length: best ? best.len : 0,
    editor_source: best ? best.source : 'none',
    probe_found: !!normalizedProbe && normalized.includes(normalizedProbe),
    preview: best ? best.text.slice(0, 120) : '',
    text: best ? best.text : ''
  };
}
"""


class BodyWriter:
    def __init__(self, payload: BodyWritePayload, logger=None):
        self.payload = payload
        self.logger = logger

    async def paste_plain_text_body(self, browser_session) -> dict:
        return await self._paste_with_session(
            mode="plain_text",
            browser_session=browser_session,
            text=self.payload.plain_text,
            html="",
        )

    async def paste_rich_html_body(self, browser_session) -> dict:
        return await self._paste_with_session(
            mode="rich_html",
            browser_session=browser_session,
            text=self.payload.plain_text,
            html=self.payload.rich_html,
        )

    async def paste(self, *, mode: str, browser_session) -> dict:
        if mode == "rich_html":
            return await self._paste_with_session(
                mode="rich_html",
                browser_session=browser_session,
                text=self.payload.plain_text,
                html=self.payload.rich_html,
            )
        return await self._paste_with_session(
            mode="plain_text",
            browser_session=browser_session,
            text=self.payload.plain_text,
            html="",
        )

    async def _paste_with_session(self, *, mode: str, browser_session, text: str, html: str = "") -> dict:
        started = time.perf_counter()
        method = ""
        content_len = len(html or text or "")
        self._info(
            "[BodyTool] name=paste_%s_body start platform=%s content_len=%s probe_len=%s",
            mode,
            self.payload.platform_name or "unknown",
            content_len,
            len(self.payload.body_probe or ""),
        )
        if browser_session is None:
            return make_body_write_failure(mode=mode, reason="page_unavailable")

        try:
            if hasattr(browser_session, "must_get_current_page"):
                page = await browser_session.must_get_current_page()
            else:
                page = await browser_session.get_current_page()
            if page is None:
                return make_body_write_failure(mode=mode, reason="page_unavailable")

            raw_focus = await page.evaluate(FOCUS_EDITOR_JS, None)
            focus = normalize_evaluate_result(raw_focus)
            if focus is None:
                self._warning(
                    "[BodyTool] failed mode=%s reason=focus_result_invalid raw_type=%s raw_preview=%r",
                    mode,
                    type(raw_focus).__name__,
                    str(raw_focus)[:120],
                )
                return make_body_write_failure(mode=mode, reason="focus_result_invalid")
            if not focus or not focus.get("ok"):
                reason = (focus or {}).get("reason") or "editor_not_found"
                self._warning("[BodyTool] failed mode=%s reason=%s", mode, reason)
                return make_body_write_failure(mode=mode, reason=reason)

            if mode == "rich_html" and html:
                raw_clipboard = await page.evaluate(
                    build_clipboard_html_js(),
                    {"html": html, "text": text or ""},
                )
            else:
                raw_clipboard = await page.evaluate(build_clipboard_text_js(), text or "")
            clipboard = normalize_evaluate_result(raw_clipboard)
            if clipboard is None:
                self._warning(
                    "[BodyTool] failed mode=%s reason=clipboard_result_invalid raw_type=%s raw_preview=%r",
                    mode,
                    type(raw_clipboard).__name__,
                    str(raw_clipboard)[:120],
                )
                return make_body_write_failure(mode=mode, reason="clipboard_result_invalid")
            method = str((clipboard or {}).get("method") or "")
            if not clipboard or not clipboard.get("ok"):
                self._warning(
                    "[BodyTool] failed mode=%s reason=clipboard_write_failed method=%s error=%s",
                    mode,
                    method,
                    (clipboard or {}).get("error", ""),
                )
                return make_body_write_failure(
                    mode=mode,
                    reason="clipboard_write_failed",
                    method=method,
                )
            self._info("[BodyTool] clipboard_written mode=%s method=%s", mode, method)

            await page.press("Control+V")
            self._info("[BodyTool] paste_sent mode=%s keys=Control+V", mode)
            await asyncio.sleep(1.0 if mode == "rich_html" else 0.5)

            raw_state = await page.evaluate(READ_EDITOR_STATE_JS, self.payload.body_probe or "")
            state = normalize_evaluate_result(raw_state)
            if state is None:
                self._warning(
                    "[BodyTool] failed mode=%s reason=state_result_invalid raw_type=%s raw_preview=%r",
                    mode,
                    type(raw_state).__name__,
                    str(raw_state)[:120],
                )
                return make_body_write_failure(mode=mode, reason="state_result_invalid", method=method)
            integrity = evaluate_body_integrity(
                str(state.get("text", "") or ""),
                self.payload.plain_text or "",
            )
            editor_len = int(integrity.get("editor_text_length") or state.get("editor_text_length") or 0)
            probe_found = bool(integrity.get("probe_found"))
            if integrity["ok"]:
                result = make_body_write_success(
                    mode=mode,
                    method=method,
                    editor_text_length=editor_len,
                    probe_found=probe_found,
                    expected_text_length=integrity["expected_text_length"],
                    length_ratio=integrity["length_ratio"],
                    probe_start_found=integrity["probe_start_found"],
                    probe_middle_found=integrity["probe_middle_found"],
                    probe_end_found=integrity["probe_end_found"],
                )
                self._info(
                    "[BodyTool] verify_result ok=true mode=%s method=%s probe_found=%s "
                    "editor_len=%s expected_len=%s length_ratio=%.2f probes=%s/%s/%s duration=%.2fs",
                    mode,
                    method,
                    probe_found,
                    editor_len,
                    integrity["expected_text_length"],
                    integrity["length_ratio"],
                    integrity["probe_start_found"],
                    integrity["probe_middle_found"],
                    integrity["probe_end_found"],
                    time.perf_counter() - started,
                )
                return result

            self._warning(
                "[BodyTool] failed mode=%s reason=body_incomplete method=%s probe_found=%s "
                "editor_len=%s expected_len=%s length_ratio=%.2f probes=%s/%s/%s preview=%r",
                mode,
                method,
                probe_found,
                editor_len,
                integrity["expected_text_length"],
                integrity["length_ratio"],
                integrity["probe_start_found"],
                integrity["probe_middle_found"],
                integrity["probe_end_found"],
                str((state or {}).get("preview", ""))[:120],
            )
            return make_body_write_failure(
                mode=mode,
                reason="body_incomplete",
                method=method,
                editor_text_length=editor_len,
                probe_found=probe_found,
                expected_text_length=integrity["expected_text_length"],
                length_ratio=integrity["length_ratio"],
                probe_start_found=integrity["probe_start_found"],
                probe_middle_found=integrity["probe_middle_found"],
                probe_end_found=integrity["probe_end_found"],
            )
        except Exception as exc:
            reason = f"exception:{str(exc)[:120]}"
            self._warning("[BodyTool] failed mode=%s reason=%s", mode, reason)
            return make_body_write_failure(mode=mode, reason=reason, method=method)

    def _info(self, message: str, *args: Any) -> None:
        if self.logger:
            self.logger.info(message, *args)

    def _warning(self, message: str, *args: Any) -> None:
        if self.logger:
            self.logger.warning(message, *args)
