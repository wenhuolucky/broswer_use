"""Browser-use tool for setting Sohu article cover."""

from __future__ import annotations

import time
from pathlib import Path
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
    const options = (args && args[0]) || {};
    const cover_path = String(options.cover_path || '').trim();
    const phase = String(options.phase || '').trim();
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
    const classOf = (node) => String((node && node.className) || '');
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
    const isCoverTriggerBlocked = (node) => {
      if (!node) return true;
      if (node.matches && node.matches('input[type="file"]')) return true;
      const ownText = textOf(node);
      const host = node.closest ? node.closest('button,a,label,div,span,li') : null;
      const hostText = textOf(host);
      if ((ownText + hostText).includes('本地上传')) return true;
      if (host && host.querySelector && host.querySelector('input[type="file"]')) return true;
      return false;
    };
    const isCoverPanelTrigger = (node) => {
      if (!node || isDisabled(node)) return false;
      const text = textOf(node);
      if (!text.includes('上传图片') && !text.includes('设置封面') && !text.includes('选择封面')) return false;
      let cursor = node;
      for (let depth = 0; cursor && depth < 5; depth += 1) {
        const panelText = textOf(cursor);
        if (panelText.includes('封面') && panelText.includes('上传图片')) {
          if (panelText.includes('本地上传') || (cursor.querySelector && cursor.querySelector('input[type="file"]'))) {
            return false;
          }
          return true;
        }
        cursor = cursor.parentElement;
      }
      return false;
    };
    const nodeInfo = (node) => {
      if (!node) return 'none';
      const text = textOf(node).replace(/\s+/g, ' ').slice(0, 40);
      return `clicked_trigger_tag=${node.tagName}; clicked_trigger_class=${classOf(node)}; clicked_trigger_text=${text}`;
    };
    const clickNode = async (node) => {
      node.scrollIntoView({ block: 'center', inline: 'center' });
      try { node.focus && node.focus(); } catch (_err) {}
      for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
        try {
          node.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
        } catch (_err) {}
      }
      node.click();
      await sleep(350);
    };
    const findByText = (texts, root = document, selector = 'button,a,span,div,li,p', options = {}) => {
      const targets = Array.isArray(texts) ? texts : [texts];
      const nodes = allVisible(selector, root);
      return nodes.find((node) => {
        if (!targets.some((text) => hasText(node, text)) || isDisabled(node)) return false;
        return options.allowUploadLike || !isUploadLike(node);
      });
    };
    const findCoverTriggerByText = (texts, root = document, selector = 'button,a,span,div,li,p') => {
      const targets = Array.isArray(texts) ? texts : [texts];
      const nodes = allVisible(selector, root);
      return nodes.find((node) => targets.some((text) => hasText(node, text)) && !isDisabled(node) && !isCoverTriggerBlocked(node));
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
    const hasCoverPanelAncestor = (node) => {
      let cursor = node;
      for (let depth = 0; cursor && depth < 8; depth += 1) {
        const panelText = textOf(cursor);
        if (panelText.includes('封面') && panelText.includes('上传图片')) return true;
        cursor = cursor.parentElement;
      }
      return false;
    };
    const findExactCoverUploadTrigger = () => {
      const exact = allVisible('.upload-file.mp-upload, .upload-file, .mp-upload')
        .find((node) => {
          const marker = `${textOf(node)} ${classOf(node)}`;
          return marker.includes('上传图片')
            && hasCoverPanelAncestor(node)
            && !isCoverTriggerBlocked(node)
            && !isDisabled(node);
        });
      if (exact) return exact;
      return allVisible('button,a,div,span,p').find((node) => isCoverPanelTrigger(node));
    };
    const openCoverDialog = async () => {
      let root = findDialogRoot();
      if (root) return root;
      const candidateTexts = [];
      let clickedInfo = '';
      const rememberCandidate = (node) => {
        const value = textOf(node).replace(/\s+/g, ' ').slice(0, 40);
        if (value && !candidateTexts.includes(value)) candidateTexts.push(value);
      };
      const coverUploadTrigger = findExactCoverUploadTrigger();
      if (coverUploadTrigger) {
        rememberCandidate(coverUploadTrigger);
        clickedInfo = nodeInfo(coverUploadTrigger);
        await clickNode(coverUploadTrigger);
        await sleep(700);
        root = findDialogRoot();
        if (root) return root;
        candidateTexts.push('cover_upload_trigger_clicked_without_dialog');
      }
      const direct = findCoverTriggerByText(
        ['上传图片', '设置封面', '选择封面', '添加封面', '封面图片'],
        document,
        'button,a,span,div,p'
      );
      if (direct) {
        rememberCandidate(direct);
        clickedInfo = nodeInfo(direct);
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
        const looksLikeCoverTrigger = marker.includes('封面') || marker.includes('上传图片');
        if (looksLikeCoverTrigger) rememberCandidate(node);
        return looksLikeCoverTrigger && !isCoverTriggerBlocked(node) && !isDisabled(node);
      });
      for (const node of candidates.slice(0, 5)) {
        clickedInfo = nodeInfo(node);
        await clickNode(node);
        await sleep(700);
        root = findDialogRoot();
        if (root) return root;
      }
      return { failed: true, candidate_texts: `${clickedInfo}; ${candidateTexts.slice(0, 8).join('|')}` };
    };
    const clickTab = async (root, label) => {
      const tab = findByText(label, root, 'button,a,span,div,li,p', { allowUploadLike: true });
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
    const findLocalUploadInput = (root) => {
      const roots = [root, document].filter(Boolean);
      for (const scope of roots) {
        const input = Array.from(scope.querySelectorAll('input[type="file"]'))
          .find((node) => !isDisabled(node));
        if (input) return input;
      }
      return null;
    };
    const clickLocalUploadEntry = async (root) => {
      const uploadNode = allVisible('.upload-file.mp-upload, .upload-file, .mp-upload, button,a,div,span', root)
        .find((node) => {
          const marker = `${textOf(node)} ${classOf(node)}`;
          return marker.includes('上传图片') && !isDisabled(node);
        });
      if (!uploadNode) return { ok: false, reason: 'local_upload_button_not_found' };
      await clickNode(uploadNode);
      return { ok: true, detail: nodeInfo(uploadNode) };
    };
    const coverApplied = () => {
      const root = findDialogRoot();
      if (root && visible(root)) return false;
      const text = textOf(document.body);
      if (text.includes('正文图片') && text.includes('素材库') && text.includes('本地上传')) return false;
      return true;
    };
    const confirmSelectedCover = async (root, source, selectedCount = 1) => {
      const confirmed = await clickConfirm(root);
      if (!confirmed) {
        return {
          ok: false,
          source,
          selected: true,
          confirmed: false,
          cover_applied: false,
          reason: 'confirm_button_not_found',
          detail: `selected_count=${selectedCount || 0}`
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
          detail: `selected_count=${selectedCount || 0}`
        };
      }
      return {
        ok: true,
        source,
        selected: true,
        confirmed: true,
        cover_applied: true,
        reason: '',
        detail: `selected_count=${selectedCount || 0}`
      };
    };

    let root = await openCoverDialog();
    const openFailureDetail = root && root.failed ? root.candidate_texts : '';
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
    if (root.failed) {
      return {
        ok: false,
        source: '',
        selected: false,
        confirmed: false,
        cover_applied: false,
        reason: 'cover_trigger_not_found',
        detail: `cover dialog was not opened; candidate_texts=${openFailureDetail}`
      };
    }

    if (phase === 'confirm_local_upload') {
      return await confirmSelectedCover(root, 'local_upload', 1);
    }

    if (cover_path) {
      const localTabReady = await clickTab(root, '本地上传');
      if (!localTabReady) {
        return {
          ok: false,
          source: 'local_upload',
          selected: false,
          confirmed: false,
          cover_applied: false,
          reason: 'local_upload_tab_not_found',
          detail: activePanelText(root).slice(0, 120)
        };
      }
      root = findDialogRoot() || root;
      const clickedUpload = await clickLocalUploadEntry(root);
      await sleep(500);
      const input = findLocalUploadInput(root);
      if (!input) {
        return {
          ok: false,
          source: 'local_upload',
          selected: false,
          confirmed: false,
          cover_applied: false,
          reason: 'local_upload_input_not_found',
          detail: clickedUpload.detail || clickedUpload.reason || activePanelText(root).slice(0, 120)
        };
      }
      return {
        ok: false,
        source: 'local_upload',
        selected: false,
        confirmed: false,
        cover_applied: false,
        reason: 'local_upload_ready',
        detail: `cover_path=${cover_path}; ${clickedUpload.detail || ''}`
      };
    }

    const bodyTabReady = await clickTab(root, '正文图片');
    let source = 'body_image';
    let selected = { ok: false, reason: 'body_image_tab_not_found', count: 0 };
    let bodyDetail = 'material_fallback_without_body_tab: body image tab not found';
    if (bodyTabReady) {
      root = findDialogRoot() || root;
      selected = await selectFirstImage(root);
      bodyDetail = `${selected.reason}; ${activePanelText(root).slice(0, 120)}`;
    }

    if (!selected.ok) {
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

    return await confirmSelectedCover(root, source, selected.count || 0);
  })();
}
"""


class SohuCoverSetter:
    def __init__(self, cover_path: str = "", logger=None):
        self.cover_path = cover_path or ""
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

            raw_result = await page.evaluate(
                build_sohu_cover_js(),
                {"cover_path": self.cover_path},
            )
            result = normalize_evaluate_result(raw_result)
            if result is None:
                self._warning(
                    "[SohuCoverTool] failed reason=cover_result_invalid raw_type=%s raw_preview=%r",
                    type(raw_result).__name__,
                    str(raw_result)[:160],
                )
                return make_sohu_cover_failure("cover_result_invalid")

            if result.get("reason") == "local_upload_ready":
                upload_result = await self._upload_local_cover(page)
                if not upload_result.get("ok"):
                    return make_sohu_cover_failure(
                        str(upload_result.get("reason") or "local_upload_failed"),
                        source="local_upload",
                        detail=str(upload_result.get("detail") or ""),
                    )
                raw_result = await page.evaluate(
                    build_sohu_cover_js(),
                    {"cover_path": self.cover_path, "phase": "confirm_local_upload"},
                )
                result = normalize_evaluate_result(raw_result)
                if result is None:
                    self._warning(
                        "[SohuCoverTool] failed reason=cover_result_invalid_after_upload "
                        "raw_type=%s raw_preview=%r",
                        type(raw_result).__name__,
                        str(raw_result)[:160],
                    )
                    return make_sohu_cover_failure(
                        "cover_result_invalid_after_upload",
                        source="local_upload",
                    )

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

    async def _upload_local_cover(self, page) -> dict:
        if not self.cover_path:
            return {"ok": False, "reason": "local_cover_path_empty", "detail": ""}
        cover_file = Path(self.cover_path)
        if not cover_file.exists():
            return {
                "ok": False,
                "reason": "local_cover_file_not_found",
                "detail": self.cover_path,
            }

        self._info("[SohuCoverTool] local_upload_ready path=%s", self.cover_path)
        try:
            file_input = page.locator('input[type="file"]').last
            await file_input.set_input_files(str(cover_file))
            self._info("[SohuCoverTool] local_upload_file_set path=%s", self.cover_path)
            await page.wait_for_timeout(1800)
            return {"ok": True, "reason": "", "detail": str(cover_file)}
        except Exception as exc:
            reason = f"local_upload_set_input_failed:{str(exc)[:120]}"
            self._warning("[SohuCoverTool] failed reason=%s", reason)
            return {"ok": False, "reason": reason, "detail": self.cover_path}

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
