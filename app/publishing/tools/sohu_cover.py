"""Browser-use tool for setting Sohu article cover."""

from __future__ import annotations

import time
from typing import Any

from app.publishing.tools.body_writer import normalize_evaluate_result


def make_sohu_cover_success(*, source: str, detail: str = "") -> dict:
    return {
        "ok": True,
        "source": source or "",
        "selected": True,
        "confirmed": True,
        "cover_applied": True,
        "reason": "",
        "detail": detail or "",
    }


def make_sohu_cover_failure(
    reason: str,
    *,
    source: str = "",
    selected: bool = False,
    confirmed: bool = False,
    cover_applied: bool = False,
    detail: str = "",
) -> dict:
    return {
        "ok": False,
        "source": source or "",
        "selected": bool(selected),
        "confirmed": bool(confirmed),
        "cover_applied": bool(cover_applied),
        "reason": reason or "unknown",
        "detail": detail or "",
    }


def build_sohu_cover_js() -> str:
    return r"""
(...args) => {
  return (async () => {
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const visible = (node) => {
      if (!node) return false;
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect.width > 0
        && rect.height > 0;
    };
    const textOf = (node) => ((node && (node.innerText || node.textContent || node.value)) || '').trim();
    const allVisible = (selector, root = document) =>
      Array.from(root.querySelectorAll(selector)).filter(visible);
    const hasText = (node, text) => textOf(node).includes(text);
    const isDisabled = (node) =>
      !!(node && (node.disabled || node.getAttribute('aria-disabled') === 'true'
        || /\bdisabled\b/.test(node.className || '')));
    const isUploadLike = (node) => {
      if (!node) return false;
      if (node.matches && node.matches('input[type="file"]')) return true;
      const ownText = textOf(node);
      const host = node.closest ? node.closest('button,a,label,div,span,li') : null;
      const hostText = textOf(host);
      if ((ownText + hostText).includes('本地上传')) return true;
      if ((ownText + hostText).includes('上传图片')) return true;
      if (host && host.querySelector && host.querySelector('input[type="file"]')) return true;
      return false;
    };
    const clickNode = async (node) => {
      node.scrollIntoView({ block: 'center', inline: 'center' });
      node.click();
      await sleep(350);
    };
    const findByText = (texts, root = document, selector = 'button,a,span,div,li,p') => {
      const targets = Array.isArray(texts) ? texts : [texts];
      const nodes = allVisible(selector, root);
      return nodes.find((node) => targets.some((text) => hasText(node, text)) && !isDisabled(node) && !isUploadLike(node));
    };
    const rootHasCoverTabs = (node) => {
      const text = textOf(node);
      return text.includes('正文图片') && (text.includes('素材库') || text.includes('本地上传'));
    };
    const findDialogRoot = () => {
      const dialogSelectors = [
        '[role="dialog"]',
        '.ant-modal',
        '.semi-modal',
        '.el-dialog',
        '.modal',
        '.cover-dialog',
        '.cover-modal'
      ];
      for (const selector of dialogSelectors) {
        const found = allVisible(selector).find(rootHasCoverTabs);
        if (found) return found;
      }
      const tabNode = findByText('正文图片', document, 'button,a,span,div,li,p');
      if (!tabNode) return null;
      let cursor = tabNode;
      for (let depth = 0; cursor && depth < 8; depth += 1) {
        if (rootHasCoverTabs(cursor)) return cursor;
        cursor = cursor.parentElement;
      }
      return document.body && rootHasCoverTabs(document.body) ? document.body : null;
    };
    const openCoverDialog = async () => {
      let root = findDialogRoot();
      if (root) return root;
      const direct = findByText(
        ['设置封面', '选择封面', '添加封面', '封面图片'],
        document,
        'button,a,span,div,p'
      );
      if (direct) {
        await clickNode(direct);
        await sleep(700);
        root = findDialogRoot();
        if (root) return root;
      }
      const candidates = allVisible('button,a,div,span,p').filter((node) => {
        const text = textOf(node);
        const aria = node.getAttribute('aria-label') || '';
        const title = node.getAttribute('title') || '';
        const className = String(node.className || '');
        const marker = `${text} ${aria} ${title} ${className}`;
        return marker.includes('封面') && !isUploadLike(node) && !isDisabled(node);
      });
      for (const node of candidates.slice(0, 5)) {
        await clickNode(node);
        await sleep(700);
        root = findDialogRoot();
        if (root) return root;
      }
      return null;
    };
    const clickTab = async (root, label) => {
      const tab = findByText(label, root, 'button,a,span,div,li,p');
      if (!tab) return false;
      await clickNode(tab);
      return true;
    };
    const activePanelText = (root) => textOf(root);
    const selectableImages = (root) => {
      return allVisible('img', root).filter((img) => {
        const src = img.currentSrc || img.src || img.getAttribute('src') || '';
        const alt = img.getAttribute('alt') || '';
        const title = img.getAttribute('title') || '';
        if (!src || src.startsWith('data:image/svg')) return false;
        if ((alt + title).includes('上传')) return false;
        if (isUploadLike(img)) return false;
        const box = img.getBoundingClientRect();
        return box.width >= 20 && box.height >= 20;
      });
    };
    const selectFirstImage = async (root) => {
      const images = selectableImages(root);
      if (!images.length) return { ok: false, reason: 'image_not_found' };
      const img = images[0];
      const clickable = img.closest('li,button,a,label,div') || img;
      if (isUploadLike(clickable)) return { ok: false, reason: 'image_is_upload_like' };
      await clickNode(clickable);
      return { ok: true, count: images.length };
    };
    const clickConfirm = async (root) => {
      const confirm = findByText(['确定', '确认'], root, 'button,a,span,div');
      if (!confirm) return false;
      await clickNode(confirm);
      await sleep(800);
      return true;
    };
    const coverApplied = () => {
      const root = findDialogRoot();
      if (root && visible(root)) return false;
      const text = textOf(document.body);
      if (text.includes('正文图片') && text.includes('素材库') && text.includes('本地上传')) return false;
      return true;
    };

    let root = await openCoverDialog();
    if (!root) {
      return {
        ok: false,
        source: '',
        selected: false,
        confirmed: false,
        cover_applied: false,
        reason: 'cover_trigger_not_found',
        detail: 'cover dialog was not opened'
      };
    }

    const bodyTabReady = await clickTab(root, '正文图片');
    if (!bodyTabReady) {
      return {
        ok: false,
        source: '',
        selected: false,
        confirmed: false,
        cover_applied: false,
        reason: 'body_image_check_failed',
        detail: 'body image tab not found'
      };
    }
    root = findDialogRoot() || root;
    let source = 'body_image';
    let selected = await selectFirstImage(root);

    if (!selected.ok) {
      const bodyDetail = `${selected.reason}; ${activePanelText(root).slice(0, 120)}`;
      const materialTabReady = await clickTab(root, '素材库');
      if (!materialTabReady) {
        return {
          ok: false,
          source: 'body_image',
          selected: false,
          confirmed: false,
          cover_applied: false,
          reason: 'material_tab_not_found',
          detail: bodyDetail
        };
      }
      root = findDialogRoot() || root;
      source = 'material_library';
      selected = await selectFirstImage(root);
      if (!selected.ok) {
        return {
          ok: false,
          source,
          selected: false,
          confirmed: false,
          cover_applied: false,
          reason: 'material_image_not_found',
          detail: selected.reason
        };
      }
    }

    const confirmed = await clickConfirm(root);
    if (!confirmed) {
      return {
        ok: false,
        source,
        selected: true,
        confirmed: false,
        cover_applied: false,
        reason: 'confirm_button_not_found',
        detail: `selected_count=${selected.count || 0}`
      };
    }

    const applied = coverApplied();
    if (!applied) {
      return {
        ok: false,
        source,
        selected: true,
        confirmed: true,
        cover_applied: false,
        reason: 'cover_apply_not_verified',
        detail: `selected_count=${selected.count || 0}`
      };
    }
    return {
      ok: true,
      source,
      selected: true,
      confirmed: true,
      cover_applied: true,
      reason: '',
      detail: `selected_count=${selected.count || 0}`
    };
  })();
}
"""


class SohuCoverSetter:
    def __init__(self, logger=None):
        self.logger = logger

    async def set_cover(self, browser_session) -> dict:
        started = time.perf_counter()
        self._info("[SohuCoverTool] name=set_sohu_cover start")
        if browser_session is None:
            return make_sohu_cover_failure("page_unavailable")

        try:
            if hasattr(browser_session, "must_get_current_page"):
                page = await browser_session.must_get_current_page()
            else:
                page = await browser_session.get_current_page()
            if page is None:
                return make_sohu_cover_failure("page_unavailable")

            raw_result = await page.evaluate(build_sohu_cover_js(), {})
            result = normalize_evaluate_result(raw_result)
            if result is None:
                self._warning(
                    "[SohuCoverTool] failed reason=cover_result_invalid raw_type=%s raw_preview=%r",
                    type(raw_result).__name__,
                    str(raw_result)[:160],
                )
                return make_sohu_cover_failure("cover_result_invalid")

            stable = self._stable_result(result)
            level = self._info if stable.get("ok") else self._warning
            level(
                "[SohuCoverTool] result ok=%s source=%s selected=%s confirmed=%s "
                "cover_applied=%s reason=%r detail=%r duration=%.2fs",
                stable.get("ok"),
                stable.get("source", ""),
                stable.get("selected"),
                stable.get("confirmed"),
                stable.get("cover_applied"),
                stable.get("reason", ""),
                stable.get("detail", ""),
                time.perf_counter() - started,
            )
            return stable
        except Exception as exc:
            reason = f"exception:{str(exc)[:120]}"
            self._warning("[SohuCoverTool] failed reason=%s", reason)
            return make_sohu_cover_failure(reason)

    @staticmethod
    def _stable_result(result: dict) -> dict:
        if result.get("ok"):
            return make_sohu_cover_success(
                source=str(result.get("source") or ""),
                detail=str(result.get("detail") or ""),
            )
        return make_sohu_cover_failure(
            str(result.get("reason") or "unknown"),
            source=str(result.get("source") or ""),
            selected=bool(result.get("selected")),
            confirmed=bool(result.get("confirmed")),
            cover_applied=bool(result.get("cover_applied")),
            detail=str(result.get("detail") or ""),
        )

    def _info(self, message: str, *args: Any) -> None:
        if self.logger:
            self.logger.info(message, *args)

    def _warning(self, message: str, *args: Any) -> None:
        if self.logger:
            self.logger.warning(message, *args)
