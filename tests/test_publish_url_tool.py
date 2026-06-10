from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_article_url_tool_retries_three_times_before_failure(monkeypatch):
    from app.publishing.service import PublishService

    service = PublishService()
    attempts = []

    async def fake_open_latest(session, title, logger):
        attempts.append(title)
        return {"matched": False, "article_url": "", "detail_title": ""}

    monkeypatch.setattr(service, "_open_latest_article_from_articles_page", fake_open_latest)
    monkeypatch.setattr(service, "_url_lookup_retry_delays", (0, 0, 0))

    result = await service._lookup_published_article_url(
        session=object(),
        title="title",
        logger=None,
    )

    assert len(attempts) == 3
    assert result == {
        "found": False,
        "article_url": "",
        "matched_title": "",
        "attempts": 3,
        "reason": "确认发布后连续 3 次未在作品列表找到同标题文章，无法确认发布成功",
    }


@pytest.mark.asyncio
async def test_article_url_tool_returns_when_second_attempt_finds_url(monkeypatch):
    from app.publishing.service import PublishService

    service = PublishService()
    attempts = []

    async def fake_open_latest(session, title, logger):
        attempts.append(title)
        if len(attempts) == 2:
            return {"matched": True, "article_url": "https://www.toutiao.com/item/1", "detail_title": title}
        return {"matched": False, "article_url": "", "detail_title": ""}

    monkeypatch.setattr(service, "_open_latest_article_from_articles_page", fake_open_latest)
    monkeypatch.setattr(service, "_url_lookup_retry_delays", (0, 0, 0))

    result = await service._lookup_published_article_url(
        session=object(),
        title="title",
        logger=None,
    )

    assert len(attempts) == 2
    assert result == {
        "found": True,
        "article_url": "https://www.toutiao.com/item/1",
        "matched_title": "title",
        "attempts": 2,
        "reason": "",
    }


def test_agent_task_requires_url_tool_after_confirm_publish():
    from app.publishing.service import PublishService

    task = PublishService()._build_publish_task(
        title="title",
        content="content",
        cover_path=None,
        logger=None,
    )

    assert "get_published_article_url" in task
    assert "确认发布" in task
    assert "不要直接调用 done" in task


@pytest.mark.asyncio
async def test_toutiao_article_url_lookup_attempts_at_most_three(monkeypatch):
    from app.publishing.toutiao_service import AutoToutiaoPublishService

    service = AutoToutiaoPublishService()
    attempts = []

    class Page:
        async def goto(self, url):
            attempts.append(url)

    class Session:
        async def get_current_page(self):
            return Page()

    async def fake_wait_for_article_entries(page, logger, attempt):
        return 0

    async def fake_extract_article_candidates(page, logger):
        return []

    async def fake_sleep(delay):
        return None

    monkeypatch.setattr(service, "_wait_for_article_entries", fake_wait_for_article_entries)
    monkeypatch.setattr(service, "_extract_article_candidates", fake_extract_article_candidates)
    monkeypatch.setattr(service, "_log_article_candidates", lambda items, logger, attempt, rendered_count: None)
    monkeypatch.setattr("app.publishing.toutiao_service.asyncio.sleep", fake_sleep)

    result = await service._lookup_published_article_url(
        session=Session(),
        title="title",
        logger=None,
    )

    assert result["found"] is False
    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_publish_url_tool_is_registered_and_returns_action_result(monkeypatch):
    import json

    from app.publishing.service import PublishService

    service = PublishService()

    async def fake_lookup(session, title, logger):
        return {
            "found": True,
            "article_url": "https://www.toutiao.com/item/1",
            "matched_title": title,
            "attempts": 1,
            "reason": "",
        }

    monkeypatch.setattr(service, "_lookup_published_article_url", fake_lookup)

    controller = service._build_publish_tools(logger=None)
    action = controller.registry.registry.actions["get_published_article_url"]
    params = action.param_model(title="title")

    result = await action.function(params=params, browser_session=object())

    payload = json.loads(result.extracted_content)
    assert payload["found"] is True
    assert payload["article_url"] == "https://www.toutiao.com/item/1"


@pytest.mark.asyncio
async def test_publish_url_tool_uses_original_task_title_when_llm_title_is_polluted(monkeypatch):
    import json

    from app.publishing.service import PublishService

    service = PublishService()
    lookup_titles = []

    async def fake_lookup(session, title, logger):
        lookup_titles.append(title)
        return {
            "found": True,
            "article_url": "https://www.toutiao.com/item/1",
            "matched_title": title,
            "attempts": 1,
            "reason": "",
        }

    monkeypatch.setattr(service, "_lookup_published_article_url", fake_lookup)

    controller = service._build_publish_tools(logger=None, original_title="如果失败会怎么样？")
    action = controller.registry.registry.actions["get_published_article_url"]
    params = action.param_model(title="如果失败会怎么样？6月10日")

    result = await action.function(params=params, browser_session=object())

    payload = json.loads(result.extracted_content)
    assert payload["found"] is True
    assert payload["matched_title"] == "如果失败会怎么样？"
    assert lookup_titles == ["如果失败会怎么样？"]


def test_parse_agent_outcome_uses_agent_done_article_url():
    from app.publishing.service import PublishService

    class History:
        history = [object()]

        def final_result(self):
            return '{"success": true, "account_name": "acct", "article_url": "https://www.toutiao.com/item/1", "failure_reason": ""}'

        def is_successful(self):
            return True

        def errors(self):
            return []

    result = PublishService()._parse_agent_outcome(History(), tracker=None, logger=None)

    assert result["success"] is True
    assert result["account"] == "acct"
    assert result["article_url"] == "https://www.toutiao.com/item/1"


def test_parse_agent_outcome_extracts_article_url_from_non_json_final_text():
    from app.publishing.service import PublishService

    class History:
        history = [object()]

        def final_result(self):
            return """
文章发布成功！

账号名称：风醒浅影晓渐临
文章标题：关于成都的那些事
文章链接：https://mp.toutiao.com/profile_v4/graphic/preview?pgc_id=7649284280467587584
"""

        def is_successful(self):
            return True

        def errors(self):
            return []

    result = PublishService()._parse_agent_outcome(History(), tracker=None, logger=None)

    assert result["success"] is True
    assert result["article_url"] == "https://www.toutiao.com/article/7649284280467587584/?&source=m_redirect"


def test_parse_agent_outcome_prefers_guard_failure_over_agent_success():
    from app.publishing.service import PublishService

    class History:
        history = [object()]

        def final_result(self):
            return '{"success": true, "account_name": "acct", "article_url": "https://www.toutiao.com/item/1", "failure_reason": ""}'

        def is_successful(self):
            return True

        def errors(self):
            return []

    result = PublishService()._parse_agent_outcome(
        History(),
        tracker=None,
        logger=None,
        detected_failure={"failed": True, "matched_text": "发布失败", "signal": "failure_text"},
    )

    assert result["success"] is False
    assert result["article_url"] == ""
    assert result["failure_reason"] == "发布失败"
