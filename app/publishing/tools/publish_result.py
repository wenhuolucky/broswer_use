"""发布结果观察工具的纯 helper。

这些 helper 只依赖浏览器页面内标准 JS 与 Python 数据整理，适合在
Docker/Linux 环境中运行，不依赖宿主机桌面、剪贴板或本地临时文件。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


MAX_TEXT_LENGTH = 500
MAX_LIST_ITEMS = 8


PUBLISH_OBSERVER_SCRIPT = r"""() => {
  const now = Date.now();
  const globalKey = '__publishResultSignals';

  const normalizeText = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const signalWords = [
    '发布', '提交', '成功', '失败', '审核', '上限', '限制', '必填',
    '不能为空', '请选择', '验证码', '风控', '权限', '登录', '重试',
    'toast', 'message', 'modal', 'alert', 'error'
  ];

  const looksRelevant = (text, node) => {
    const normalized = normalizeText(text);
    if (!normalized || normalized.length < 2) return false;
    const lowered = normalized.toLowerCase();
    const className = normalizeText(node && node.className);
    const role = normalizeText(node && node.getAttribute && node.getAttribute('role'));
    const ariaLive = normalizeText(node && node.getAttribute && node.getAttribute('aria-live'));
    const nodeHint = `${className} ${role} ${ariaLive}`.toLowerCase();
    return signalWords.some((word) => lowered.includes(word.toLowerCase()) || nodeHint.includes(word.toLowerCase()));
  };

  const pushSignal = (text, node, kind = 'toast_or_message') => {
    const normalized = normalizeText(text);
    if (!looksRelevant(normalized, node)) return;
    const store = window[globalKey];
    const duplicate = store.signals.some((item) => item.text === normalized && Date.now() - item.time < 1500);
    if (duplicate) return;
    store.signals.push({
      text: normalized.slice(0, 500),
      kind,
      url: window.location.href,
      selector_hint: node && node.nodeType === 1
        ? `${node.tagName.toLowerCase()}${node.id ? '#' + node.id : ''}${node.className ? '.' + String(node.className).trim().replace(/\s+/g, '.') : ''}`.slice(0, 200)
        : '',
      time: Date.now()
    });
    if (store.signals.length > 50) {
      store.signals.splice(0, store.signals.length - 50);
    }
  };

  window.__publishResultSignals = window.__publishResultSignals || window[globalKey];

  if (window.__publishResultSignals && window.__publishResultSignals.observerInstalled) {
    return {
      ok: true,
      observer_installed: true,
      already_installed: true,
      signal_count: window.__publishResultSignals.signals.length
    };
  }

  window.__publishResultSignals = {
    installed_at: now,
    observerInstalled: true,
    signals: [],
    pushSignal
  };
  window[globalKey] = window.__publishResultSignals;

  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'characterData') {
        pushSignal(mutation.target && mutation.target.textContent, mutation.target && mutation.target.parentElement);
      }
      for (const node of Array.from(mutation.addedNodes || [])) {
        if (!node) continue;
        const text = node.innerText || node.textContent || '';
        pushSignal(text, node);
        if (node.querySelectorAll) {
          for (const child of Array.from(node.querySelectorAll('[role="alert"],[aria-live],.toast,.message,.modal,.error,.ant-message,.ant-modal,.semi-toast,.semi-modal,.byte-message,.byte-modal'))) {
            pushSignal(child.innerText || child.textContent || '', child);
          }
        }
      }
    }
  });

  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    window.__publishResultSignals.observer = observer;
  }

  return {
    ok: true,
    observer_installed: true,
    already_installed: false,
    signal_count: window.__publishResultSignals.signals.length
  };
}"""


PUBLISH_OBSERVATION_SCRIPT = r"""(articleTitle) => {
  const normalizeText = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const safeText = (node) => normalizeText(node && (node.innerText || node.textContent || ''));
  const limitItems = (items, limit = 8) => {
    const seen = new Set();
    const result = [];
    for (const item of items) {
      const text = normalizeText(item);
      if (!text || seen.has(text)) continue;
      seen.add(text);
      result.push(text.slice(0, 500));
      if (result.length >= limit) break;
    }
    return result;
  };

  const selectTexts = (selector) => {
    try {
      return Array.from(document.querySelectorAll(selector)).map(safeText).filter(Boolean);
    } catch (error) {
      return [];
    }
  };

  const store = window.__publishResultSignals || { signals: [] };
  const now = Date.now();
  const capturedSignals = Array.from(store.signals || []).map((item) => ({
    text: normalizeText(item.text).slice(0, 500),
    kind: item.kind || 'toast_or_message',
    url: item.url || window.location.href,
    age_seconds: Math.max(0, Number(((now - Number(item.time || now)) / 1000).toFixed(2))),
    selector_hint: item.selector_hint || ''
  })).filter((item) => item.text);

  const dialogSelectors = [
    '[role="dialog"]',
    '[role="alertdialog"]',
    '.modal',
    '.ant-modal',
    '.semi-modal',
    '.byte-modal',
    '.dialog'
  ].join(',');
  const toastSelectors = [
    '[role="alert"]',
    '[aria-live]',
    '.toast',
    '.message',
    '.ant-message',
    '.semi-toast',
    '.byte-message',
    '.notice',
    '.notification'
  ].join(',');
  const errorSelectors = [
    '.error',
    '.form-item-explain',
    '.ant-form-item-explain-error',
    '.semi-form-field-error-message',
    '.byte-form-item-error',
    '[class*="error"]',
    '[class*="Error"]'
  ].join(',');
  const buttonAreaSelectors = [
    'button',
    '[role="button"]',
    '.footer',
    '.submit',
    '.publish',
    '[class*="publish"]',
    '[class*="submit"]'
  ].join(',');

  const bodyText = safeText(document.body);
  const url = window.location.href;
  const title = document.title || '';
  const managementText = `${url} ${title} ${bodyText}`.toLowerCase();
  const managementPageHint = /contentmanagement|manage|article|works|material|作品|内容管理|文章管理/.test(managementText);
  const articleTitleVisible = !!(articleTitle && bodyText.includes(articleTitle));

  return {
    url,
    title,
    visible_text_excerpt: bodyText.slice(0, 1200),
    captured_signals: capturedSignals.slice(-20),
    dialogs: limitItems(selectTexts(dialogSelectors)),
    toasts: limitItems(selectTexts(toastSelectors)),
    form_errors: limitItems(selectTexts(errorSelectors)),
    button_area_text: limitItems(selectTexts(buttonAreaSelectors), 12).join(' | ').slice(0, 1200),
    management_page_hint: managementPageHint,
    article_title_visible: articleTitleVisible
  };
}"""


def build_terminal_failure_payload(reason: str, evidence: str = "") -> str:
    """构造不可恢复失败的最终 JSON。"""

    return json.dumps(
        {
            "success": False,
            "article_url": "",
            "account": "",
            "failure_reason": _truncate_text(reason) or "发布失败",
            "publish_signal": "agent_terminal_failure",
            "evidence": _truncate_text(evidence, limit=1200),
        },
        ensure_ascii=False,
    )


def normalize_observed_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    wait_seconds: float,
    article_title: str = "",
) -> dict[str, Any]:
    """整理页面观察结果，限制体积并保留关键提示。"""

    raw = snapshot if isinstance(snapshot, dict) else {}
    captured_signals = _normalize_signal_list(raw.get("captured_signals"))
    dialogs = _normalize_text_list(raw.get("dialogs"))
    toasts = _normalize_text_list(raw.get("toasts"))
    form_errors = _normalize_text_list(raw.get("form_errors"))
    visible_text = _truncate_text(raw.get("visible_text_excerpt"), limit=1200)
    button_area = _truncate_text(raw.get("button_area_text"), limit=1200)

    signals_found = bool(captured_signals or dialogs or toasts or form_errors)
    return {
        "url": _truncate_text(raw.get("url"), limit=1000),
        "title": _truncate_text(raw.get("title"), limit=300),
        "visible_text_excerpt": visible_text,
        "captured_signals": captured_signals,
        "dialogs": dialogs,
        "toasts": toasts,
        "form_errors": form_errors,
        "button_area_text": button_area,
        "management_page_hint": bool(raw.get("management_page_hint")),
        "article_title_visible": bool(raw.get("article_title_visible"))
        or bool(article_title and article_title in visible_text),
        "signals_found": signals_found,
        "wait_seconds": float(wait_seconds),
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def _normalize_signal_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        text = _truncate_text(item.get("text"))
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(
            {
                "text": text,
                "kind": _truncate_text(item.get("kind"), limit=80) or "toast_or_message",
                "url": _truncate_text(item.get("url"), limit=1000),
                "age_seconds": _safe_float(item.get("age_seconds")),
                "selector_hint": _truncate_text(item.get("selector_hint"), limit=200),
            }
        )
        if len(normalized) >= 20:
            break
    return normalized


def _normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _truncate_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
        if len(normalized) >= MAX_LIST_ITEMS:
            break
    return normalized


def _truncate_text(value: Any, *, limit: int = MAX_TEXT_LENGTH) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
