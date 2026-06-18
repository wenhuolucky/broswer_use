from __future__ import annotations

import asyncio
import json

from app.publishing.kernel import PublishService


class AutoToutiaoPublishService(PublishService):
    """Auto-layer Toutiao publish service overrides URL lookup only."""

    article_lookup_attempts = 3
    article_lookup_retry_delay_seconds = 5

    def __init__(self):
        super().__init__()
        self._url_lookup_retry_delays = (0,)

    async def _open_latest_article_from_articles_page(self, session, title: str, logger) -> dict:
        page = await session.get_current_page()
        if page is None:
            return {"matched": False, "article_url": "", "detail_title": ""}

        articles_url = "https://mp.toutiao.com/profile_v4/graphic/articles"
        normalized_expected = (title or "").strip()

        for attempt in range(1, self.article_lookup_attempts + 1):
            if logger:
                logger.info(
                    "[PublishGuard] navigating to articles page: "
                    f"{articles_url} | attempt={attempt}/{self.article_lookup_attempts}"
                )
            await page.goto(articles_url)
            await asyncio.sleep(3)

            article_count = await self._wait_for_article_entries(page, logger, attempt)
            items = await self._extract_article_candidates(page, logger)
            self._log_article_candidates(items, logger, attempt, article_count)

            selected = self._select_candidate_article(items, normalized_expected)
            selected_href = str(selected.get("href", "") or "").strip()
            selected_text = str(selected.get("text", "") or "").strip()
            if not selected_href:
                if logger:
                    logger.info(
                        "[PublishGuard] no title-matched article candidate found; "
                        f"expected={normalized_expected} | attempt={attempt}/{self.article_lookup_attempts}"
                    )
                if attempt < self.article_lookup_attempts:
                    await asyncio.sleep(self.article_lookup_retry_delay_seconds)
                continue

            if logger:
                logger.info(
                    "[PublishGuard] selected title-matched article candidate: "
                    f"href={selected_href} | text={selected_text}"
                )

            if self._is_preview_article_url(selected_href):
                return {
                    "matched": True,
                    "article_url": selected_href,
                    "detail_title": selected_text,
                }

            await page.goto(selected_href)
            await asyncio.sleep(2)
            detail_url = await session.get_current_page_url()
            if "/item/" not in detail_url and "/item/" in selected_href:
                detail_url = selected_href
            detail_title = await self._read_article_detail_title(page, logger)

            normalized_actual = (detail_title or selected_text or "").strip()
            matched = self._title_matches(normalized_expected, normalized_actual)
            if logger:
                logger.info(
                    "[PublishGuard] article detail verification: "
                    f"expected={normalized_expected} | actual={normalized_actual} | matched={matched} | url={detail_url}"
                )

            if matched:
                return {
                    "matched": True,
                    "article_url": detail_url,
                    "detail_title": normalized_actual,
                }
            if attempt < self.article_lookup_attempts:
                await asyncio.sleep(self.article_lookup_retry_delay_seconds)

        if logger:
            logger.warning(
                "[PublishGuard] title-matched article URL not found after retries: "
                f"expected={normalized_expected} | attempts={self.article_lookup_attempts}"
            )
        return {"matched": False, "article_url": "", "detail_title": ""}

    async def _wait_for_article_entries(self, page, logger, attempt: int) -> int:
        if logger:
            logger.info(
                "[PublishGuard] waiting for .article-card, /item/, or preview links to render... "
                f"attempt={attempt}"
            )
        for _ in range(20):
            article_count = await page.evaluate(
                """() => {
                    const cards = document.querySelectorAll('.article-card').length;
                    const itemLinks = document.querySelectorAll('a[href*="toutiao.com/item/"], a[href*="/item/"]').length;
                    const previewLinks = document.querySelectorAll('a[href*="/profile_v4/graphic/preview"][href*="pgc_id="]').length;
                    return Math.max(cards, itemLinks, previewLinks);
                }"""
            )
            if isinstance(article_count, int) and article_count > 0:
                if logger:
                    logger.info(
                        "[PublishGuard] work manage rendered: "
                        f"found {article_count} article entries | attempt={attempt}"
                    )
                return article_count
            await asyncio.sleep(1)
        if logger:
            logger.warning(
                "[PublishGuard] work manage did not render .article-card, /item/, or preview links within 20s "
                f"| attempt={attempt}"
            )
        return 0

    async def _extract_article_candidates(self, page, logger) -> list:
        items = await page.evaluate(
            """() => {
                const pickText = (card) => {
                    const candidates = [
                        '.article-card-bone',
                        '.title',
                        '[class*="title"]',
                        '.article-card-wrap'
                    ];
                    for (const selector of candidates) {
                        const node = card.querySelector(selector);
                        const text = (node?.innerText || node?.textContent || '').trim();
                        if (text) return text;
                    }
                    return (card.innerText || card.textContent || '').trim();
                };

                const items = [];
                const cards = Array.from(document.querySelectorAll('.article-card'));
                for (const card of cards) {
                    const link =
                        card.querySelector('a.title[href*="/profile_v4/graphic/preview"][href*="pgc_id="]') ||
                        card.querySelector('a[href*="/profile_v4/graphic/preview"][href*="pgc_id="]') ||
                        card.querySelector('a.image[href*="toutiao.com/item/"]') ||
                        card.querySelector('a[href*="toutiao.com/item/"]') ||
                        card.querySelector('a[href*="/item/"]');
                    const href = link?.href || '';
                    if (href) items.push({ href, text: pickText(card) });
                }
                if (items.length) return items;

                const anchors = Array.from(document.querySelectorAll(
                    'a[href*="/profile_v4/graphic/preview"][href*="pgc_id="], a[href*="toutiao.com/item/"], a[href*="/item/"]'
                ));
                for (const anchor of anchors) {
                    const href = anchor.href || '';
                    const text = (anchor.innerText || anchor.textContent || '').trim();
                    if (href) items.push({ href, text });
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
                "[PublishGuard] unexpected article candidates payload: "
                f"type={type(items).__name__} value={items!r}"
            )
        return []

    def _log_article_candidates(self, items: list, logger, attempt: int, rendered_count: int) -> None:
        if not logger:
            return
        dict_items = [item for item in items if isinstance(item, dict)]
        logger.info(
            "[PublishGuard] article candidates extracted: "
            f"count={len(dict_items)} | rendered_count={rendered_count} | attempt={attempt}"
        )
        if not dict_items:
            logger.warning(
                "[PublishGuard] no toutiao article or preview links found on work manage page "
                f"| attempt={attempt}"
            )
            return
        for index, item in enumerate(dict_items[:5], start=1):
            href = str(item.get("href", "") or "").strip()
            text = self._compact_log_text(str(item.get("text", "") or ""))
            logger.info(
                "[PublishGuard] article candidate "
                f"#{index}: href={href} | text={text}"
            )

    def _compact_log_text(self, text: str, limit: int = 180) -> str:
        compacted = " ".join((text or "").split())
        if len(compacted) <= limit:
            return compacted
        return compacted[: limit - 3] + "..."

    async def _read_article_detail_title(self, page, logger) -> str:
        detail_title = await page.evaluate(
            """() => {
                const candidates = [
                    'h1',
                    '[data-testid="article-title"]',
                    '.article-title',
                    '.title',
                    '[class*="title"]'
                ];
                for (const selector of candidates) {
                    const node = document.querySelector(selector);
                    const text = (node?.innerText || node?.textContent || '').trim();
                    if (text) return text;
                }
                return document.title || '';
            }"""
        )
        if isinstance(detail_title, str):
            return detail_title
        if logger:
            logger.warning(
                "[PublishGuard] unexpected detail_title payload: "
                f"type={type(detail_title).__name__} value={detail_title!r}"
            )
        return ""

    def _select_candidate_article(self, items: list, normalized_title: str) -> dict:
        dict_items = [item for item in items if isinstance(item, dict)]
        for item in dict_items:
            if str(item.get("text", "") or "").strip() == normalized_title:
                return item
        for item in dict_items:
            item_text = str(item.get("text", "") or "").strip()
            if self._title_matches(normalized_title, item_text):
                return item
        return {}

    def _is_preview_article_url(self, url: str) -> bool:
        return "/profile_v4/graphic/preview" in (url or "") and "pgc_id=" in (url or "")

    def _title_matches(self, expected: str, actual: str) -> bool:
        return self._platform().title_matches(expected, actual)
