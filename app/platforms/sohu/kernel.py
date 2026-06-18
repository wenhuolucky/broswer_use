from __future__ import annotations

import asyncio
import json

from app.platforms.sohu.config import SohuPlatform
from app.publishing.kernel import PublishService
from app.utils.urls import normalize_sohu_article_url


class AutoSohuPublishService(PublishService):
    """Sohu publishing service."""

    article_lookup_attempts = 3
    article_lookup_retry_delay_seconds = 5

    def __init__(self, account_id: str = ""):
        super().__init__()
        # 搜狐号数字 id（拼文章 URL 用），由 orchestrator 从该渠道的 metadata
        # 解析后注入（见 SohuPlatform.article_url_account_id）。
        self.account_id = account_id
        self._sohu_platform = SohuPlatform()

    def _platform(self):
        return self._sohu_platform

    def _account_id(self) -> str:
        return self.account_id

    def _did_execute_confirm_publish(self, history_item) -> bool:
        if super()._did_execute_confirm_publish(history_item):
            return True
        for text in self._iter_history_result_texts(history_item):
            normalized_text = text or ""
            if not normalized_text or not self._looks_like_click_result(normalized_text):
                continue
            if (
                "button '发布'" in normalized_text
                or 'button "发布"' in normalized_text
                or "按钮“发布”" in normalized_text
                or "按钮'发布'" in normalized_text
            ):
                return True
        return False

    async def _open_latest_article_from_articles_page(self, session, title: str, logger) -> dict:
        page = await session.get_current_page()
        if page is None:
            return {"matched": False, "article_url": "", "detail_title": ""}

        articles_url = "https://mp.sohu.com/mpfe/v4/contentManagement/first/page"
        expected_title = (title or "").strip()

        for attempt in range(1, self.article_lookup_attempts + 1):
            if logger:
                logger.info(
                    "[PublishGuard] navigating to sohu content management: "
                    f"{articles_url} | attempt={attempt}/{self.article_lookup_attempts}"
                )
            await page.goto(articles_url)
            await asyncio.sleep(3)

            items = await self._extract_article_candidates(page, logger)
            self._log_article_candidates(items, logger, attempt)
            selected = self._select_candidate_article(items, expected_title)
            if selected.get("article_url"):
                if logger:
                    logger.info(
                        "[PublishGuard] selected sohu title-matched article: "
                        f"url={selected.get('article_url', '')} | text={selected.get('detail_title', '')}"
                    )
                return {
                    "matched": True,
                    "article_url": selected.get("article_url", ""),
                    "detail_title": selected.get("detail_title", ""),
                }

            if logger:
                logger.info(
                    "[PublishGuard] no sohu title-matched article candidate found; "
                    f"expected={expected_title} | attempt={attempt}/{self.article_lookup_attempts}"
                )
            if attempt < self.article_lookup_attempts:
                await asyncio.sleep(self.article_lookup_retry_delay_seconds)

        return {"matched": False, "article_url": "", "detail_title": ""}

    async def _extract_article_candidates(self, page, logger) -> list:
        items = await page.evaluate(
            """() => {
                const pickText = (anchor) => {
                    const containers = [
                        anchor.closest('tr'),
                        anchor.closest('[class*="row"]'),
                        anchor.closest('[class*="item"]'),
                        anchor.closest('[class*="card"]'),
                        anchor.parentElement
                    ].filter(Boolean);
                    for (const container of containers) {
                        const text = (container.innerText || container.textContent || '').trim();
                        if (text) return text;
                    }
                    return (anchor.innerText || anchor.textContent || '').trim();
                };

                const anchors = Array.from(document.querySelectorAll('a[href]'));
                const items = [];
                for (const anchor of anchors) {
                    const href = anchor.href || '';
                    if (!href) continue;
                    const isPreview =
                        href.includes('/news/articlepreview') ||
                        href.includes('/h5/v2/newsPreview') ||
                        href.includes('sohu.com/a/');
                    if (!isPreview) continue;
                    items.push({ href, text: pickText(anchor) });
                }
                return items;
            }"""
        )

        if isinstance(items, str):
            try:
                parsed_items = json.loads(items)
            except json.JSONDecodeError:
                parsed_items = None
            if isinstance(parsed_items, list):
                items = parsed_items

        if isinstance(items, list):
            return items
        if logger:
            logger.warning(
                "[PublishGuard] unexpected sohu article candidates payload: "
                f"type={type(items).__name__} value={items!r}"
            )
        return []

    def _select_candidate_article(self, items: list, expected_title: str) -> dict:
        dict_items = [item for item in items if isinstance(item, dict)]
        for item in dict_items:
            item_text = str(item.get("text", "") or "").strip()
            item_href = str(item.get("href", "") or "").strip()
            if not item_href or not self._looks_like_sohu_article_url(item_href):
                continue
            if self._platform().title_matches(expected_title, item_text):
                return {
                    "article_url": normalize_sohu_article_url(item_href, self._account_id()),
                    "detail_title": item_text,
                }
        return {}

    def _log_article_candidates(self, items: list, logger, attempt: int) -> None:
        if not logger:
            return
        dict_items = [item for item in items if isinstance(item, dict)]
        logger.info(
            "[PublishGuard] sohu article candidates extracted: "
            f"count={len(dict_items)} | attempt={attempt}"
        )
        if not dict_items:
            logger.warning("[PublishGuard] no sohu preview/article links found on content management page")
            return
        for index, item in enumerate(dict_items[:5], start=1):
            href = str(item.get("href", "") or "").strip()
            text = self._compact_log_text(str(item.get("text", "") or ""))
            logger.info(
                "[PublishGuard] sohu article candidate "
                f"#{index}: href={href} | text={text}"
            )

    @staticmethod
    def _compact_log_text(text: str, limit: int = 180) -> str:
        compacted = " ".join((text or "").split())
        if len(compacted) <= limit:
            return compacted
        return compacted[: limit - 3] + "..."

    def _extract_article_url_from_text(self, text: str) -> str:
        import re

        urls = re.findall(r"https?://[^\s<>\"'，。；、)）\]]+", text or "")
        for url in urls:
            cleaned_url = url.rstrip(".,;:!?")
            if self._looks_like_sohu_article_url(cleaned_url):
                return normalize_sohu_article_url(cleaned_url, self._account_id())
        return ""

    def _parse_agent_outcome(self, history, tracker, logger, detected_failure: dict | None = None):
        result = super()._parse_agent_outcome(history, tracker, logger, detected_failure=detected_failure)
        result["article_url"] = normalize_sohu_article_url(str(result.get("article_url", "") or ""), self._account_id())
        if result.get("success") and not result.get("article_url"):
            result["success"] = False
            result["failure_reason"] = "搜狐号提交审核成功，但未获取到文章 URL"
        if result.get("success") and result.get("article_url") and not result.get("publish_signal"):
            result["publish_signal"] = "submitted_for_review"
        return result

    @staticmethod
    def _looks_like_sohu_article_url(url: str) -> bool:
        if not url:
            return False
        # 官方最终 URL（包含旧 m.sohu.com 和新 www.sohu.com 两种）
        if "sohu.com/a/" in url and ("m.sohu.com" in url or "www.sohu.com" in url):
            return True
        if "mp.sohu.com/h5/v2/newsPreview" in url and "id=" in url:
            return True
        # 后台管理列表的预览 URL（含 /mpfe/v4/.../news/articlepreview）
        if "mp.sohu.com" in url and "/news/articlepreview" in url and "id=" in url:
            return True
        return False
