"""Browser-use tools for writing article body content."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
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


def is_transient_evaluate_exception(exc: Exception) -> bool:
    message = str(exc)
    transient_markers = (
        "Runtime.evaluate",
        "CDP method",
        "TimeoutError",
        "timed out",
        "timeout",
    )
    return isinstance(exc, TimeoutError) or any(marker in message for marker in transient_markers)


def build_clipboard_text_js() -> str:
    return r"""
(...args) => {
  const bodyText = args[0] || '';
  const clipboardWriteTimeoutMs = 1800;
  const copyWithExecCommand = () => {
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
  };
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      const timeout = new Promise((resolve) => setTimeout(
        () => resolve({ ok: false, method: 'navigator.clipboard.writeText', error: 'clipboard_write_timeout' }),
        clipboardWriteTimeoutMs
      ));
      const write = navigator.clipboard.writeText(bodyText).then(
        () => ({ ok: true, method: 'navigator.clipboard.writeText' }),
        (error) => ({ ok: false, method: 'navigator.clipboard.writeText', error: String(error) })
      );
      return Promise.race([write, timeout]).then((result) => {
        if (result && result.ok) return result;
        const fallback = copyWithExecCommand();
        if (fallback.ok) {
          return { ok: true, method: 'execCommand', fallback_from: result.method, fallback_reason: result.error || '' };
        }
        return { ok: false, method: result.method || '', error: result.error || 'clipboard_write_failed' };
      });
    }
  } catch (error) {}
  try {
    return copyWithExecCommand();
  } catch (error) {
    return { ok: false, method: '', error: String(error) };
  }
}
"""


def build_clipboard_api_js() -> str:
    """[降级方案] 使用 ClipboardItem API 直接写入 text/html 和 text/plain。

    注意：此方法虽然成功写入剪贴板，但缺少 Fragment 标记，会导致头条编辑器无法正确
    渲染有序列表（显示为 1,1,1）、引用块和分隔线。仅在 iframe 方法失败时使用。

    需要浏览器已授予 clipboard-write 权限（通过 grant_permissions）。
    """
    return r"""
(...args) => {
  const payload = args[0] || {};
  const htmlContent = payload.html || '';
  const plainText = payload.text || '';

  // 首选：ClipboardItem 直接写入 HTML 字节流。
  // 精确控制剪贴板中的 text/html 和 text/plain，不依赖浏览器 DOM 序列化。
  // 需要浏览器已授予 clipboard-write 权限。
  try {
    if (navigator.clipboard && window.ClipboardItem) {
      const item = new ClipboardItem({
        'text/html': new Blob([htmlContent], { type: 'text/html' }),
        'text/plain': new Blob([plainText], { type: 'text/plain' })
      });
      return navigator.clipboard.write([item]).then(
        () => ({ ok: true, method: 'clipboard_api' }),
        (error) => ({ ok: false, method: 'clipboard_api', error: String(error) })
      );
    }
  } catch (error) {
    return { ok: false, method: 'clipboard_api', error: String(error) };
  }

  return { ok: false, method: 'clipboard_api', error: 'ClipboardItem not available' };
}
"""


def build_clipboard_html_via_iframe_js() -> str:
    """[首选方案] 通过隐藏 iframe 渲染 HTML 并全选复制，模拟手动复制的完整流程。

    这是最接近手动复制的方式。iframe 中的 HTML 会被浏览器完整解析和渲染，
    然后通过 execCommand('copy') 触发浏览器的序列化逻辑，生成标准的 text/html 格式。

    关键改进：在 HTML 内容前后手动添加 Fragment 标记（<!--StartFragment--> 和 <!--EndFragment-->），
    这些标记是 Windows CF_HTML 剪贴板格式的核心元数据，用于标识实际内容的边界。
    头条编辑器依赖这些标记正确识别有序列表、引用块、分隔线等富文本元素。

    如果失败，会降级到 clipboard_api 方式。
    """
    return r"""
(...args) => {
  const payload = args[0] || {};
  const htmlContent = payload.html || '';

  // 步骤 1：创建隐藏 iframe
  const iframe = document.createElement('iframe');
  iframe.style.cssText = 'position:fixed;left:-9999px;top:0;width:800px;height:600px;'
    + 'opacity:0;pointer-events:none;border:none;';
  document.body.appendChild(iframe);

  // 步骤 2：写入完整的 HTML 文档，手动添加 Fragment 标记
  const doc = iframe.contentDocument;
  doc.open();
  doc.write('<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
    + '<body><!--StartFragment-->' + htmlContent + '<!--EndFragment--></body></html>');
  doc.close();

  // 步骤 3：等待渲染后全选复制
  return new Promise((resolve) => {
    setTimeout(() => {
      try {
        const body = doc.body;
        body.focus();

        const range = doc.createRange();
        range.selectNodeContents(body);

        const selection = iframe.contentWindow.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);

        const ok = doc.execCommand('copy');

        selection.removeAllRanges();
        try { document.body.removeChild(iframe); } catch (_) {}

        resolve({
          ok: !!ok,
          method: ok ? 'iframe_execCommand' : 'iframe_execCommand_failed'
        });
      } catch (error) {
        try { document.body.removeChild(iframe); } catch (_) {}
        resolve({ ok: false, method: 'iframe_execCommand', error: String(error) });
      }
    }, 150);
  });
}
"""


def build_clipboard_html_js() -> str:
    return r"""
(...args) => {
  const payload = args[0] || {};
  const htmlContent = payload.html || '';
  const plainText = payload.text || '';

  // 首选：ClipboardItem 直接写入 HTML 字节流。
  // 精确控制剪贴板中的 text/html 和 text/plain，不依赖浏览器 DOM 序列化。
  // execCommand('copy') 在 page.evaluate() 中没有用户手势，Chromium 可能
  // 返回 true 但只写入 text/plain，导致 <blockquote>/<hr> 丢失。
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

  // 次选：浏览器内渲染后复制。
  // ClipboardItem 不可用时，在 hidden contenteditable div 里渲染 HTML，
  // 让浏览器把 DOM 序列化后写入剪贴板。
  if (htmlContent) {
    let renderDiv = null;
    const prevActive = document.activeElement;
    try {
      renderDiv = document.createElement('div');
      renderDiv.contentEditable = 'true';
      renderDiv.tabIndex = -1;
      renderDiv.innerHTML = htmlContent;
      renderDiv.style.position = 'fixed';
      renderDiv.style.left = '-9999px';
      renderDiv.style.top = '0';
      renderDiv.style.opacity = '0';
      renderDiv.style.pointerEvents = 'none';
      renderDiv.style.whiteSpace = 'pre-wrap';
      renderDiv.style.wordBreak = 'break-word';
      renderDiv.style.width = '600px';
      document.body.appendChild(renderDiv);

      renderDiv.focus();
      const renderRange = document.createRange();
      renderRange.selectNodeContents(renderDiv);
      const renderSelection = window.getSelection();
      renderSelection.removeAllRanges();
      renderSelection.addRange(renderRange);

      const renderOk = document.execCommand('copy');
      renderSelection.removeAllRanges();
      if (renderOk) {
        return Promise.resolve({ ok: true, method: 'rendered_div_execCommand' });
      }
    } catch (error) {
      // 渲染后复制失败，继续尝试后续路径。
    } finally {
      if (renderDiv && renderDiv.parentNode) {
        renderDiv.parentNode.removeChild(renderDiv);
      }
      try {
        if (prevActive && typeof prevActive.focus === "function") {
          prevActive.focus();
        }
      } catch (_) {}
    }
  }

  // 兜底：textarea 纯文本复制，丢失所有格式。
  try {
    const textarea = document.createElement('textarea');
    textarea.value = plainText || htmlContent;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return { ok: !!ok, method: 'execCommand_textarea' };
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
    def __init__(self, payload: BodyWritePayload, logger=None, evaluate_timeout_seconds: float = 10.0):
        self.payload = payload
        self.logger = logger
        self.evaluate_timeout_seconds = float(evaluate_timeout_seconds or 10.0)

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

            last_exception: Exception | None = None
            for attempt in range(1, 3):
                try:
                    return await self._paste_once(
                        mode=mode,
                        page=page,
                        text=text,
                        html=html,
                        method_hint=method,
                        started=started,
                    )
                except Exception as exc:
                    last_exception = exc
                    if attempt >= 2 or not is_transient_evaluate_exception(exc):
                        break
                    self._warning(
                        "[BodyTool] retry_after_exception mode=%s attempt=%s reason=%s",
                        mode,
                        attempt,
                        str(exc)[:120],
                    )
                    await asyncio.sleep(0.5)

            reason = f"exception:{str(last_exception)[:120]}"
            self._warning("[BodyTool] failed mode=%s reason=%s", mode, reason)
            return make_body_write_failure(mode=mode, reason=reason, method=method)
        except Exception as exc:
            reason = f"exception:{str(exc)[:120]}"
            self._warning("[BodyTool] failed mode=%s reason=%s", mode, reason)
            return make_body_write_failure(mode=mode, reason=reason, method=method)

    async def _paste_once(
        self,
        *,
        mode: str,
        page,
        text: str,
        html: str,
        method_hint: str,
        started: float,
    ) -> dict:
        method = method_hint
        raw_focus = await self._evaluate_with_timeout(page, FOCUS_EDITOR_JS, None, label="focus_editor")
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
            # 首选方案：真实浏览器渲染复制
            # 完全复刻手动测试流程：打开 HTML 文件 → 渲染 → Ctrl+A → Ctrl+C
            # Playwright keyboard.press() 产生真实的键盘事件，浏览器识别为用户手势
            # 允许完整的剪贴板写入（包含 Fragment 标记、CF_HTML 头部）
            real_browser_result = await self._paste_via_real_browser(page, html)
            clipboard = {
                "ok": real_browser_result["ok"],
                "method": real_browser_result["method"],
                "error": real_browser_result.get("error", ""),
            }

            if real_browser_result["ok"]:
                self._info(
                    "[BodyTool] real_browser_copy_success method=%s html_len=%s duration=%.2fs",
                    real_browser_result["method"],
                    real_browser_result.get("html_length", 0),
                    real_browser_result.get("duration", 0),
                )
            else:
                self._info(
                    "[BodyTool] real_browser_copy_failed error=%s duration=%.2fs, fallback_to_iframe",
                    real_browser_result.get("error", "unknown"),
                    real_browser_result.get("duration", 0),
                )

            # 如果真实浏览器复制失败，降级到 iframe 方法
            if not real_browser_result["ok"]:
                self._info("[BodyTool] fallback_to_iframe_method")
                iframe_start = time.perf_counter()
                try:
                    raw_clipboard = await self._evaluate_with_timeout(
                        page,
                        build_clipboard_html_via_iframe_js(),
                        {"html": html, "text": text or ""},
                        label="clipboard_html_iframe_fallback",
                    )
                    iframe_duration = time.perf_counter() - iframe_start
                    clipboard = normalize_evaluate_result(raw_clipboard)

                    if clipboard is None or not clipboard.get("ok"):
                        self._warning(
                            "[BodyTool] iframe_fallback_failed method=%s error=%s duration=%.2fs",
                            (clipboard or {}).get("method", "unknown"),
                            (clipboard or {}).get("error", "unknown"),
                            iframe_duration,
                        )
                    else:
                        self._info(
                            "[BodyTool] iframe_fallback_success method=%s duration=%.2fs",
                            clipboard.get("method", "unknown"),
                            iframe_duration,
                        )
                except Exception as exc:
                    iframe_duration = time.perf_counter() - iframe_start
                    self._warning(
                        "[BodyTool] iframe_fallback_exception error=%s duration=%.2fs",
                        str(exc)[:120],
                        iframe_duration,
                    )
                    clipboard = {"ok": False, "method": "iframe_exception", "error": str(exc)[:120]}

            # 如果 iframe 也失败，最终降级到 clipboard_api
            if clipboard is None or not clipboard.get("ok"):
                self._info("[BodyTool] fallback_to_clipboard_api_method")
                clipboard_api_start = time.perf_counter()
                try:
                    raw_clipboard = await self._evaluate_with_timeout(
                        page,
                        build_clipboard_api_js(),
                        {"html": html, "text": text or ""},
                        label="clipboard_api_fallback",
                    )
                    clipboard_api_duration = time.perf_counter() - clipboard_api_start
                    clipboard = normalize_evaluate_result(raw_clipboard)

                    if clipboard is None or not clipboard.get("ok"):
                        self._warning(
                            "[BodyTool] clipboard_api_fallback_failed method=%s error=%s duration=%.2fs",
                            (clipboard or {}).get("method", "unknown"),
                            (clipboard or {}).get("error", "unknown"),
                            clipboard_api_duration,
                        )
                    else:
                        self._info(
                            "[BodyTool] clipboard_api_fallback_success method=%s duration=%.2fs",
                            clipboard.get("method", "unknown"),
                            clipboard_api_duration,
                        )
                except Exception as exc:
                    clipboard_api_duration = time.perf_counter() - clipboard_api_start
                    self._warning(
                        "[BodyTool] clipboard_api_fallback_exception error=%s duration=%.2fs",
                        str(exc)[:120],
                        clipboard_api_duration,
                    )
                    clipboard = {"ok": False, "method": "clipboard_api_exception", "error": str(exc)[:120]}
        else:
            # 纯文本模式使用 navigator.clipboard.writeText
            text_start = time.perf_counter()
            try:
                raw_clipboard = await self._evaluate_with_timeout(
                    page,
                    build_clipboard_text_js(),
                    text or "",
                    label="clipboard_text",
                )
                text_duration = time.perf_counter() - text_start
                clipboard = normalize_evaluate_result(raw_clipboard)

                if clipboard is None:
                    self._warning(
                        "[BodyTool] text_result_invalid raw_type=%s raw_preview=%r duration=%.2fs",
                        type(raw_clipboard).__name__,
                        str(raw_clipboard)[:120],
                        text_duration,
                    )
                elif clipboard.get("ok"):
                    self._info(
                        "[BodyTool] text_success method=%s duration=%.2fs",
                        clipboard.get("method", "unknown"),
                        text_duration,
                    )
                else:
                    self._warning(
                        "[BodyTool] text_failed method=%s error=%s duration=%.2fs",
                        clipboard.get("method", "unknown"),
                        clipboard.get("error", "unknown"),
                        text_duration,
                    )
            except Exception as exc:
                text_duration = time.perf_counter() - text_start
                self._warning(
                    "[BodyTool] text_exception error=%s duration=%.2fs",
                    str(exc)[:120],
                    text_duration,
                )
                clipboard = {"ok": False, "method": "text_exception", "error": str(exc)[:120]}
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

        raw_state = await self._evaluate_with_timeout(
            page,
            READ_EDITOR_STATE_JS,
            self.payload.body_probe or "",
            label="read_editor_state",
        )
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

    async def _paste_via_real_browser(self, page, html_content: str) -> dict:
        """真实浏览器渲染复制方案。

        完全复刻手动测试流程：
        1. 生成临时 HTML 文件
        2. 在浏览器新标签页中打开 file:// URL（真实渲染上下文）
        3. 通过 Playwright keyboard.press() 模拟 Ctrl+A、Ctrl+C（真实键盘事件 → 用户手势）
        4. 浏览器识别为用户手势，执行完整的剪贴板写入（包含 Fragment 标记、CF_HTML 头部）
        5. 清理临时标签页和文件
        6. 回到头条编辑器页面粘贴 Ctrl+V

        这是最接近手动复制的方式，剪贴板内容与手动测试完全一致。
        """
        temp_file = None
        new_page = None
        started = time.perf_counter()

        try:
            # 1. 构建完整 HTML 并写入临时文件
            html_doc = self._build_full_html(html_content)
            temp_file = self._create_temp_html_file(html_doc)
            self._info(
                "[BodyTool] temp_html_created path=%s size=%s",
                temp_file.name,
                len(html_doc),
            )

            # 2. 打开新标签页渲染 HTML
            context = page.context
            new_page = await context.new_page()
            await new_page.goto(f"file://{temp_file.name}")
            await new_page.wait_for_load_state("networkidle")
            await asyncio.sleep(0.5)  # 额外等待渲染完成
            self._info(
                "[BodyTool] render_page_opened url=file://%s",
                temp_file.name,
            )

            # 3. 授予 file:// 协议的剪贴板写入权限
            try:
                await context.grant_permissions(
                    ["clipboard-write"],
                    origin=f"file://{temp_file.name}",
                )
                self._info("[BodyTool] clipboard_permission_granted origin=file://")
            except Exception as exc:
                self._warning("[BodyTool] clipboard_permission_grant_failed error=%s", str(exc)[:120])

            # 4. 读取浏览器渲染后的 DOM（浏览器已解析和规范化 HTML 结构）
            #    然后用 ClipboardItem API 写入剪贴板（已验证能成功写入 text/html）
            rendered_html = await new_page.evaluate("() => document.body.innerHTML")
            self._info(
                "[BodyTool] rendered_html_read html_len=%s",
                len(rendered_html),
            )

            # 5. 使用 ClipboardItem API 写入剪贴板（不依赖 isTrusted 复制事件）
            clipboard_result = await new_page.evaluate(
                r"""(html) => {
                    const plainText = html.replace(/<[^>]+>/g, '');
                    try {
                        if (navigator.clipboard && window.ClipboardItem) {
                            const item = new ClipboardItem({
                                'text/html': new Blob([html], { type: 'text/html' }),
                                'text/plain': new Blob([plainText], { type: 'text/plain' })
                            });
                            return navigator.clipboard.write([item]).then(
                                () => ({ ok: true, method: 'rendered_clipboard_api' }),
                                (error) => ({ ok: false, method: 'rendered_clipboard_api', error: String(error) })
                            );
                        }
                    } catch (e) {
                        return { ok: false, method: 'rendered_clipboard_api', error: String(e) };
                    }
                    return { ok: false, method: 'rendered_clipboard_api', error: 'ClipboardItem unavailable' };
                }""",
                rendered_html,
            )

            duration = time.perf_counter() - started
            if clipboard_result and clipboard_result.get("ok"):
                self._info(
                    "[BodyTool] real_browser_copy_success method=%s html_len=%s duration=%.2fs",
                    clipboard_result.get("method"),
                    len(rendered_html),
                    duration,
                )
                return {
                    "ok": True,
                    "method": clipboard_result.get("method", "rendered_clipboard_api"),
                    "html_length": len(rendered_html),
                    "duration": duration,
                    "temp_file": temp_file.name,
                }
            else:
                self._warning(
                    "[BodyTool] rendered_clipboard_api_failed error=%s duration=%.2fs",
                    (clipboard_result or {}).get("error", "unknown"),
                    duration,
                )
                return {
                    "ok": False,
                    "method": "rendered_clipboard_api_failed",
                    "error": (clipboard_result or {}).get("error", "unknown"),
                    "duration": duration,
                }

        except Exception as exc:
            duration = time.perf_counter() - started
            self._warning(
                "[BodyTool] real_browser_copy_exception error=%s duration=%.2fs",
                str(exc)[:120],
                duration,
            )
            return {
                "ok": False,
                "method": "real_browser_copy_exception",
                "error": str(exc)[:120],
                "duration": duration,
            }

        finally:
            # 5. 清理资源：关闭临时标签页，删除临时文件，恢复焦点
            if new_page:
                try:
                    await new_page.close()
                    self._info("[BodyTool] render_page_closed")
                except Exception as exc:
                    self._warning("[BodyTool] close_render_page_failed error=%s", str(exc)[:120])
            # 确保原始页面回到前台并聚焦编辑器
            try:
                await page.bring_to_front()
                await page.evaluate(FOCUS_EDITOR_JS)
                self._info("[BodyTool] original_page_focused")
            except Exception as exc:
                self._warning("[BodyTool] focus_original_page_failed error=%s", str(exc)[:120])
            if temp_file and os.path.exists(temp_file.name):
                try:
                    os.unlink(temp_file.name)
                    self._info("[BodyTool] temp_file_removed path=%s", temp_file.name)
                except Exception as exc:
                    self._warning("[BodyTool] remove_temp_file_failed error=%s", str(exc)[:120])

    @staticmethod
    def _build_full_html(html_content: str) -> str:
        """构建完整的 HTML 文档"""
        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>富文本预览</title>
</head>
<body>
{html_content}
</body>
</html>"""

    @staticmethod
    def _create_temp_html_file(html_doc: str) -> tempfile.NamedTemporaryFile:
        """创建临时 HTML 文件"""
        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".html",
            encoding="utf-8",
            delete=False,
        )
        temp_file.write(html_doc)
        temp_file.close()
        return temp_file

    async def _evaluate_with_timeout(self, page, script: str, arg: Any, *, label: str) -> Any:
        try:
            return await asyncio.wait_for(
                page.evaluate(script, arg),
                timeout=self.evaluate_timeout_seconds,
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"{label} evaluate timed out after {self.evaluate_timeout_seconds:.1f}s"
            ) from exc

    def _info(self, message: str, *args: Any) -> None:
        if self.logger:
            self.logger.info(message, *args)

    def _warning(self, message: str, *args: Any) -> None:
        if self.logger:
            self.logger.warning(message, *args)
