"""Browser-use tool for setting Sohu article title."""

from __future__ import annotations

import time
from typing import Any

from app.publishing.tools.body_writer import normalize_evaluate_result


def make_sohu_title_success(*, expected_title: str, actual_title: str, method: str, detail: str = "") -> dict:
    return {
        "ok": True,
        "expected_title": expected_title or "",
        "actual_title": actual_title or "",
        "verified": True,
        "method": method or "",
        "reason": "",
        "detail": detail or "",
    }


def make_sohu_title_failure(
    reason: str,
    *,
    expected_title: str = "",
    actual_title: str = "",
    method: str = "",
    detail: str = "",
) -> dict:
    return {
        "ok": False,
        "expected_title": expected_title or "",
        "actual_title": actual_title or "",
        "verified": False,
        "method": method or "",
        "reason": reason or "unknown",
        "detail": detail or "",
    }


def build_sohu_title_js() -> str:
    return r"""
(...args) => {
  const payload = args[0] || {};
  const expectedTitle = String(payload.title || '').trim();
  const visible = (node) => {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.visibility !== 'hidden'
      && style.display !== 'none'
      && rect.width > 0
      && rect.height > 0;
  };
  const walk = (root, acc = []) => {
    if (!root) return acc;
    const children = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
    for (const node of children) {
      acc.push(node);
      if (node.shadowRoot) walk(node.shadowRoot, acc);
    }
    return acc;
  };
  const textOf = (node) => ((node && (node.placeholder || node.getAttribute('aria-label') || node.innerText || node.textContent || '')) || '').trim();
  const nodes = walk(document);
  const inputs = nodes.filter((node) => {
    const tag = (node.tagName || '').toLowerCase();
    if (!['input', 'textarea'].includes(tag) && node.getAttribute('contenteditable') !== 'true') return false;
    if (!visible(node)) return false;
    const marker = `${textOf(node)} ${node.className || ''}`;
    return marker.includes('标题') || marker.includes('请输入') || marker.includes('title');
  });
  let target = inputs.find((node) => textOf(node).includes('标题')) || inputs[0];
  if (!target) {
    return {
      ok: false,
      expected_title: expectedTitle,
      actual_title: '',
      verified: false,
      method: '',
      reason: 'title_input_not_found',
      detail: 'no visible title input candidate'
    };
  }
  target.scrollIntoView({ block: 'center', inline: 'center' });
  target.focus();
  target.click();
  const tag = (target.tagName || '').toLowerCase();
  const method = 'dom_input_events';
  if (tag === 'input' || tag === 'textarea') {
    const proto = tag === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(target, '');
    target.dispatchEvent(new Event('input', { bubbles: true }));
    setter.call(target, expectedTitle);
    target.dispatchEvent(new Event('input', { bubbles: true }));
    target.dispatchEvent(new Event('change', { bubbles: true }));
  } else {
    target.textContent = '';
    target.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContentBackward' }));
    target.textContent = expectedTitle;
    target.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: expectedTitle }));
  }
  const actualTitle = String(tag === 'input' || tag === 'textarea' ? target.value : target.innerText || target.textContent || '').trim();
  const verified = actualTitle === expectedTitle;
  return {
    ok: verified,
    expected_title: expectedTitle,
    actual_title: actualTitle,
    verified,
    method,
    reason: verified ? '' : 'title_mismatch',
    detail: `actual_len=${actualTitle.length}; expected_len=${expectedTitle.length}`
  };
}
"""


class SohuTitleSetter:
    def __init__(self, title: str, logger=None):
        self.title = title or ""
        self.logger = logger

    async def set_title(self, browser_session) -> dict:
        started = time.perf_counter()
        self._info("[SohuTitleTool] name=set_sohu_title start title_len=%s", len(self.title))
        if browser_session is None:
            return make_sohu_title_failure("page_unavailable", expected_title=self.title)

        try:
            if hasattr(browser_session, "must_get_current_page"):
                page = await browser_session.must_get_current_page()
            else:
                page = await browser_session.get_current_page()
            if page is None:
                return make_sohu_title_failure("page_unavailable", expected_title=self.title)

            raw_result = await page.evaluate(build_sohu_title_js(), {"title": self.title})
            result = normalize_evaluate_result(raw_result)
            if result is None:
                self._warning(
                    "[SohuTitleTool] failed reason=title_result_invalid raw_type=%s raw_preview=%r",
                    type(raw_result).__name__,
                    str(raw_result)[:160],
                )
                return make_sohu_title_failure("title_result_invalid", expected_title=self.title)

            stable = self._stable_result(result)
            level = self._info if stable.get("ok") else self._warning
            level(
                "[SohuTitleTool] result ok=%s verified=%s method=%s actual_len=%s reason=%r detail=%r duration=%.2fs",
                stable.get("ok"),
                stable.get("verified"),
                stable.get("method", ""),
                len(stable.get("actual_title", "")),
                stable.get("reason", ""),
                stable.get("detail", ""),
                time.perf_counter() - started,
            )
            return stable
        except Exception as exc:
            reason = f"exception:{str(exc)[:120]}"
            self._warning("[SohuTitleTool] failed reason=%s", reason)
            return make_sohu_title_failure(reason, expected_title=self.title)

    def _stable_result(self, result: dict) -> dict:
        expected_title = str(result.get("expected_title") or self.title)
        actual_title = str(result.get("actual_title") or "")
        method = str(result.get("method") or "")
        detail = str(result.get("detail") or "")
        if result.get("ok") and result.get("verified"):
            return make_sohu_title_success(
                expected_title=expected_title,
                actual_title=actual_title,
                method=method,
                detail=detail,
            )
        return make_sohu_title_failure(
            str(result.get("reason") or "title_mismatch"),
            expected_title=expected_title,
            actual_title=actual_title,
            method=method,
            detail=detail,
        )

    def _info(self, message: str, *args: Any) -> None:
        if self.logger:
            self.logger.info(message, *args)

    def _warning(self, message: str, *args: Any) -> None:
        if self.logger:
            self.logger.warning(message, *args)
