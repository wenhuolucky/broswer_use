"""Sohu publishing kernel regressions."""

from __future__ import annotations

from app.platforms.sohu.kernel import AutoSohuPublishService


class TestSohuPublishActionDetection:
    def test_detects_final_publish_click(self) -> None:
        class Result:
            extracted_content = "Clicked button '发布'"
            long_term_memory = ""
            error = ""

        class HistoryItem:
            result = [Result()]

        assert AutoSohuPublishService()._did_execute_confirm_publish(HistoryItem())

    def test_does_not_treat_navigation_to_publish_entry_as_final_publish(self) -> None:
        class Result:
            extracted_content = "Clicked link '发布文章'"
            long_term_memory = ""
            error = ""

        class HistoryItem:
            result = [Result()]

        assert not AutoSohuPublishService()._did_execute_confirm_publish(HistoryItem())


class TestSohuArticleLookup:
    async def test_opens_sohu_content_management_first_page(self, monkeypatch) -> None:
        async def no_sleep(_seconds):
            return None

        class FakePage:
            def __init__(self):
                self.gotos = []

            async def goto(self, url: str):
                self.gotos.append(url)

            async def evaluate(self, _script: str):
                return [
                    {
                        "href": "https://mp.sohu.com/mpfe/v4/contentManagement/first/page/news/articlepreview?id=666&accountId=777",
                        "text": "把养生过成日常：真正有效的健康 001 审核中",
                    }
                ]

        class FakeSession:
            def __init__(self, page):
                self.page = page

            async def get_current_page(self):
                return self.page

        page = FakePage()
        service = AutoSohuPublishService(account_id="999")
        monkeypatch.setattr("app.platforms.sohu.kernel.asyncio.sleep", no_sleep)

        result = await service._open_latest_article_from_articles_page(
            FakeSession(page),
            "把养生过成日常：真正有效的健康001",
            logger=None,
        )

        assert page.gotos == ["https://mp.sohu.com/mpfe/v4/contentManagement/first/page"]
        assert result["matched"] is True
        assert result["article_url"] == "https://www.sohu.com/a/666_999"

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
