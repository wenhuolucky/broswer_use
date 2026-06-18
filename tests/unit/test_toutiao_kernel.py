"""Toutiao publishing kernel regressions."""

from __future__ import annotations

from app.platforms.toutiao.kernel import AutoToutiaoPublishService


class TestToutiaoArticleLookup:
    async def test_fast_probe_returns_title_match_without_waiting(self, monkeypatch) -> None:
        async def no_sleep(_seconds):
            return None

        class FakePage:
            def __init__(self):
                self.gotos = []

            async def goto(self, url: str):
                self.gotos.append(url)

        class FakeSession:
            def __init__(self, page):
                self.page = page

            async def get_current_page(self):
                return self.page

        service = AutoToutiaoPublishService()
        wait_calls = 0
        extract_calls = 0

        async def fail_if_waiting(_page, _logger, _attempt):
            nonlocal wait_calls
            wait_calls += 1
            raise AssertionError("fast title match should not wait for rendered article entries")

        async def extract(_page, _logger):
            nonlocal extract_calls
            extract_calls += 1
            return [
                {
                    "href": "https://mp.toutiao.com/profile_v4/graphic/preview?pgc_id=123",
                    "text": "把养生过成日常：真正有效的健康 005 06-18 15:14 审核中",
                }
            ]

        monkeypatch.setattr("app.platforms.toutiao.kernel.asyncio.sleep", no_sleep)
        monkeypatch.setattr(service, "_wait_for_article_entries", fail_if_waiting)
        monkeypatch.setattr(service, "_extract_article_candidates", extract)

        result = await service._open_latest_article_from_articles_page(
            FakeSession(FakePage()),
            "把养生过成日常：真正有效的健康005",
            logger=None,
        )

        assert result["matched"] is True
        assert result["article_url"] == "https://mp.toutiao.com/profile_v4/graphic/preview?pgc_id=123"
        assert wait_calls == 0
        assert extract_calls == 1

    async def test_fast_probe_miss_falls_back_to_wait_then_extract(self, monkeypatch) -> None:
        async def no_sleep(_seconds):
            return None

        class FakePage:
            async def goto(self, _url: str):
                return None

        class FakeSession:
            def __init__(self, page):
                self.page = page

            async def get_current_page(self):
                return self.page

        service = AutoToutiaoPublishService()
        wait_calls = 0
        extract_calls = 0

        async def wait(_page, _logger, _attempt):
            nonlocal wait_calls
            wait_calls += 1
            return 1

        async def extract(_page, _logger):
            nonlocal extract_calls
            extract_calls += 1
            if extract_calls == 1:
                return []
            return [
                {
                    "href": "https://mp.toutiao.com/profile_v4/graphic/preview?pgc_id=456",
                    "text": "把养生过成日常：真正有效的健康 006 06-18 15:20 审核中",
                }
            ]

        monkeypatch.setattr("app.platforms.toutiao.kernel.asyncio.sleep", no_sleep)
        monkeypatch.setattr(service, "_wait_for_article_entries", wait)
        monkeypatch.setattr(service, "_extract_article_candidates", extract)

        result = await service._open_latest_article_from_articles_page(
            FakeSession(FakePage()),
            "把养生过成日常：真正有效的健康006",
            logger=None,
        )

        assert result["matched"] is True
        assert result["article_url"] == "https://mp.toutiao.com/profile_v4/graphic/preview?pgc_id=456"
        assert wait_calls == 1
        assert extract_calls == 2

    async def test_waits_only_until_article_candidates_exist(self, monkeypatch) -> None:
        async def no_sleep(_seconds):
            return None

        class FakePage:
            def __init__(self):
                self.calls = 0

            async def evaluate(self, _script: str):
                self.calls += 1
                return 2

        service = AutoToutiaoPublishService()
        monkeypatch.setattr("app.platforms.toutiao.kernel.asyncio.sleep", no_sleep)

        count = await service._wait_for_article_entries(FakePage(), logger=None, attempt=1)

        assert count == 2

    async def test_stops_waiting_after_bounded_candidate_polling(self, monkeypatch) -> None:
        async def no_sleep(_seconds):
            return None

        class FakePage:
            def __init__(self):
                self.calls = 0

            async def evaluate(self, _script: str):
                self.calls += 1
                return 0

        page = FakePage()
        service = AutoToutiaoPublishService()
        monkeypatch.setattr("app.platforms.toutiao.kernel.asyncio.sleep", no_sleep)

        count = await service._wait_for_article_entries(page, logger=None, attempt=1)

        assert count == 0
        assert page.calls == service.article_candidate_poll_seconds
