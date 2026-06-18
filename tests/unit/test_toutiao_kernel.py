"""Toutiao publishing kernel regressions."""

from __future__ import annotations

from app.platforms.toutiao.kernel import AutoToutiaoPublishService


class TestToutiaoArticleLookup:
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
