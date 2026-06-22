"""Browser-use tools for writing article body content."""

from __future__ import annotations

import asyncio
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


def make_body_write_success(
    *,
    mode: str,
    method: str,
    editor_text_length: int,
    probe_found: bool,
) -> dict:
    return {
        "ok": True,
        "mode": mode,
        "method": method,
        "editor_text_length": int(editor_text_length or 0),
        "probe_found": bool(probe_found),
        "reason": "",
    }


def make_body_write_failure(
    *,
    mode: str,
    reason: str,
    method: str = "",
    editor_text_length: int = 0,
    probe_found: bool = False,
) -> dict:
    return {
        "ok": False,
        "mode": mode,
        "method": method or "",
        "editor_text_length": int(editor_text_length or 0),
        "probe_found": bool(probe_found),
        "reason": reason or "unknown",
    }


def build_clipboard_text_js() -> str:
    return r"""
async (bodyText) => {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(bodyText);
      return { ok: true, method: 'navigator.clipboard.writeText' };
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
async (payload) => {
  const htmlContent = payload.html || '';
  const plainText = payload.text || '';
  try {
    if (navigator.clipboard && window.ClipboardItem) {
      const item = new ClipboardItem({
        'text/html': new Blob([htmlContent], { type: 'text/html' }),
        'text/plain': new Blob([plainText], { type: 'text/plain' })
      });
      await navigator.clipboard.write([item]);
      return { ok: true, method: 'ClipboardItem' };
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
(contentProbe) => {
  const probe = contentProbe || '';
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
    preview: best ? best.text.slice(0, 120) : ''
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
            page = await browser_session.get_current_page()
            if page is None:
                return make_body_write_failure(mode=mode, reason="page_unavailable")

            focus = await page.evaluate(FOCUS_EDITOR_JS)
            if not focus or not focus.get("ok"):
                reason = (focus or {}).get("reason") or "editor_not_found"
                self._warning("[BodyTool] failed mode=%s reason=%s", mode, reason)
                return make_body_write_failure(mode=mode, reason=reason)

            if mode == "rich_html" and html:
                clipboard = await page.evaluate(
                    build_clipboard_html_js(),
                    {"html": html, "text": text or ""},
                )
            else:
                clipboard = await page.evaluate(build_clipboard_text_js(), text or "")
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

            await page.keyboard.press("Control+V")
            self._info("[BodyTool] paste_sent mode=%s keys=Control+V", mode)
            await asyncio.sleep(1.0 if mode == "rich_html" else 0.5)

            state = await page.evaluate(READ_EDITOR_STATE_JS, self.payload.body_probe or "")
            editor_len = int((state or {}).get("editor_text_length") or 0)
            probe_found = bool((state or {}).get("probe_found"))
            if editor_len > 0 and (probe_found or not self.payload.body_probe):
                result = make_body_write_success(
                    mode=mode,
                    method=method,
                    editor_text_length=editor_len,
                    probe_found=probe_found,
                )
                self._info(
                    "[BodyTool] verify_result ok=true mode=%s method=%s probe_found=%s editor_len=%s duration=%.2fs",
                    mode,
                    method,
                    probe_found,
                    editor_len,
                    time.perf_counter() - started,
                )
                return result

            self._warning(
                "[BodyTool] failed mode=%s reason=paste_probe_not_found method=%s probe_found=%s editor_len=%s preview=%r",
                mode,
                method,
                probe_found,
                editor_len,
                str((state or {}).get("preview", ""))[:120],
            )
            return make_body_write_failure(
                mode=mode,
                reason="paste_probe_not_found",
                method=method,
                editor_text_length=editor_len,
                probe_found=probe_found,
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
