"""Sohu publishing kernel regressions."""

from __future__ import annotations

from app.platforms.sohu.kernel import AutoSohuPublishService


class TestSohuArticleLookup:
    def test_selects_candidate_when_platform_inserts_title_whitespace(self) -> None:
        service = AutoSohuPublishService(account_id="999")

        selected = service._select_candidate_article(
            [
                {
                    "href": "https://mp.sohu.com/mpfe/v4/contentManagement/news/articlepreview?id=666&accountId=777",
                    "text": "把养生过成日常：真正有效的健康 001 已发布 预览 修改",
                },
                {
                    "href": "https://mp.sohu.com/mpfe/v4/contentManagement/news/articlepreview?id=111&accountId=777",
                    "text": "完全不同的文章标题 已发布 预览 修改",
                },
            ],
            "把养生过成日常：真正有效的健康001",
        )

        assert (
            selected["article_url"]
            == "https://www.sohu.com/a/666_999"
        )
        assert selected["detail_title"] == "把养生过成日常：真正有效的健康 001 已发布 预览 修改"

    def test_does_not_select_unrelated_candidate(self) -> None:
        service = AutoSohuPublishService(account_id="999")

        selected = service._select_candidate_article(
            [
                {
                    "href": "https://mp.sohu.com/mpfe/v4/contentManagement/news/articlepreview?id=111&accountId=777",
                    "text": "完全不同的文章标题 已发布 预览 修改",
                }
            ],
            "把养生过成日常：真正有效的健康001",
        )

        assert selected == {}
