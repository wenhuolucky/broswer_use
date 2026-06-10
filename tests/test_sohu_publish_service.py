from __future__ import annotations


class History:
    history = [object()]

    def __init__(self, final, successful=True, errors=None):
        self._final = final
        self._successful = successful
        self._errors = errors or []

    def final_result(self):
        return self._final

    def is_successful(self):
        return self._successful

    def errors(self):
        return self._errors


def test_sohu_service_extracts_and_normalizes_preview_url(monkeypatch):
    from app.publishing.sohu_service import AutoSohuPublishService

    monkeypatch.setenv("SOHU_ACCOUNT_ID", "122702850")
    service = AutoSohuPublishService(user_id="user1")

    result = service._parse_agent_outcome(
        History(
            '{"success": true, "account_name": "acct", '
            '"article_url": "https://mp.sohu.com/h5/v2/newsPreview?id=1020931946&type=article", '
            '"failure_reason": ""}'
        ),
        tracker=None,
        logger=None,
    )

    assert result["success"] is True
    assert result["article_url"] == "https://m.sohu.com/a/1020931946_122702850?sec=wd"


def test_sohu_service_extracts_preview_url_from_non_json_final_text(monkeypatch):
    from app.publishing.sohu_service import AutoSohuPublishService

    monkeypatch.setenv("SOHU_ACCOUNT_ID", "122702850")
    service = AutoSohuPublishService(user_id="user1")

    result = service._parse_agent_outcome(
        History("搜狐号提交审核成功，预览链接：https://mp.sohu.com/h5/v2/newsPreview?id=1020931946&type=article"),
        tracker=None,
        logger=None,
    )

    assert result["success"] is True
    assert result["article_url"] == "https://m.sohu.com/a/1020931946_122702850?sec=wd"


def test_sohu_service_requires_url_for_success(monkeypatch):
    from app.publishing.sohu_service import AutoSohuPublishService

    monkeypatch.setenv("SOHU_ACCOUNT_ID", "122702850")
    service = AutoSohuPublishService(user_id="user1")

    result = service._parse_agent_outcome(
        History('{"success": true, "account_name": "acct", "article_url": "", "failure_reason": ""}'),
        tracker=None,
        logger=None,
    )

    assert result["success"] is False
    assert result["article_url"] == ""
    assert result["failure_reason"] == "搜狐号提交审核成功，但未获取到文章 URL"
