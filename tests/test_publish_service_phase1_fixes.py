import asyncio
import json
import logging
from types import SimpleNamespace

from api.publish.publish_service import LLMTokenTracker, PublishService


class DummyHistory:
    def __init__(self, final_result=None, successful=False, errors=None, steps=1):
        self._final_result = final_result
        self._successful = successful
        self._errors = errors or []
        self.history = [object()] * steps

    def final_result(self):
        return self._final_result

    def is_successful(self):
        return self._successful

    def errors(self):
        return self._errors


def test_rich_html_prompt_allows_publish_even_if_structure_is_not_perfect():
    prompt = PublishService()._build_publish_task(
        title="标题",
        content="# 标题\n\n正文",
        cover_path=None,
        cover_image_url=None,
        logger=None,
    )

    assert "必须立即返回失败" not in prompt
    assert "不要继续预览或发布" not in prompt
    assert "如果富文本结构不完全理想" in prompt
    assert "仍然继续执行发布" in prompt


def test_rich_html_prompt_requires_single_cover_when_cover_exists():
    prompt = PublishService()._build_publish_task(
        title="标题",
        content="正文",
        cover_path="C:/tmp/cover.jpg",
        cover_image_url=None,
        logger=None,
    )

    assert "单封面" in prompt
    assert "不要选择三封面或五封面" in prompt


def test_rich_html_prompt_requires_single_cover_when_cover_url_exists():
    prompt = PublishService()._build_publish_task(
        title="标题",
        content="正文",
        cover_path=None,
        cover_image_url="https://example.com/cover.jpg",
        logger=None,
    )

    assert "单封面" in prompt
    assert "https://example.com/cover.jpg" in prompt


def test_rich_html_prompt_skips_cover_after_cover_loop_limit():
    prompt = PublishService()._build_publish_task(
        title="标题",
        content="正文",
        cover_path=None,
        cover_image_url="https://example.com/cover.jpg",
        logger=None,
        cover_loop_exceeded=True,
    )

    assert "跳过封面设置" in prompt
    assert "继续后续发布流程" in prompt


def test_parse_agent_outcome_accepts_json_success_payload():
    service = PublishService()
    tracker = LLMTokenTracker()
    result = service._parse_agent_outcome(
        DummyHistory(
            final_result=json.dumps(
                {
                    "success": True,
                    "account_name": "acct",
                    "article_url": "https://example.com/a",
                }
            ),
            successful=False,
        ),
        tracker=tracker,
        logger=None,
    )

    assert result["success"] is True
    assert result["account"] == "acct"
    assert result["article_url"] == "https://example.com/a"
    assert result["failure_reason"] == ""


def test_parse_agent_outcome_treats_non_json_success_message_as_success():
    service = PublishService()
    tracker = LLMTokenTracker()
    result = service._parse_agent_outcome(
        DummyHistory(
            final_result="任务已成功发布，账号=acct，链接=https://example.com/a",
            successful=True,
        ),
        tracker=tracker,
        logger=None,
    )

    assert result["success"] is True
    assert result["failure_reason"] == ""


def test_parse_agent_outcome_keeps_failure_reason_from_history_errors():
    service = PublishService()
    tracker = LLMTokenTracker()
    result = service._parse_agent_outcome(
        DummyHistory(
            final_result=None,
            successful=False,
            errors=["richtext_structure_not_rendered"],
        ),
        tracker=tracker,
        logger=None,
    )

    assert result["success"] is False
    assert result["failure_reason"] == "richtext_structure_not_rendered"


def test_parse_agent_outcome_prefers_detected_publish_success_over_history_failure():
    service = PublishService()
    tracker = LLMTokenTracker()
    result = service._parse_agent_outcome(
        DummyHistory(
            final_result=None,
            successful=False,
            errors=["agent_did_not_return_done"],
        ),
        tracker=tracker,
        logger=None,
        detected_success={
            "success": True,
            "signal": "success_text",
            "article_url": "",
            "page_url": "https://mp.toutiao.com/profile_v4/graphic/publish",
            "matched_text": "提交成功",
        },
    )

    assert result["success"] is True
    assert result["failure_reason"] == ""
    assert result["publish_signal"] == "success_text"


def test_parse_agent_outcome_does_not_finish_management_page_success_without_article_url():
    service = PublishService()
    tracker = LLMTokenTracker()
    result = service._parse_agent_outcome(
        DummyHistory(
            final_result=None,
            successful=False,
            errors=["agent_did_not_return_done"],
        ),
        tracker=tracker,
        logger=None,
        detected_success={
            "success": True,
            "signal": "success_text",
            "article_url": "",
            "page_url": "https://mp.toutiao.com/profile_v4/graphic/articles",
            "matched_text": "作品管理",
        },
    )

    assert result["success"] is False
    assert result["article_url"] == ""
    assert result["failure_reason"] == "agent_did_not_return_done"


def test_parse_agent_outcome_uses_post_publish_article_url_when_confirm_pending():
    service = PublishService()
    tracker = LLMTokenTracker()
    result = service._parse_agent_outcome(
        DummyHistory(
            final_result='Clicked button "确认发布"',
            successful=False,
            errors=["agent_did_not_return_done"],
        ),
        tracker=tracker,
        logger=None,
        confirm_pending=True,
        post_publish_verification={
            "matched": True,
            "article_url": "https://mp.toutiao.com/article/1234567890",
            "detail_title": "再次测试002",
        },
    )

    assert result["success"] is True
    assert result["article_url"] == "https://mp.toutiao.com/article/1234567890"
    assert result["publish_signal"] == "post_publish_verification"
    assert result["failure_reason"] == ""


def test_parse_agent_outcome_does_not_mark_success_for_single_confirm_pending_without_verification():
    service = PublishService()
    tracker = LLMTokenTracker()
    result = service._parse_agent_outcome(
        DummyHistory(
            final_result='Clicked button "确认发布"',
            successful=False,
            errors=["agent_did_not_return_done"],
        ),
        tracker=tracker,
        logger=None,
        confirm_pending=True,
        post_publish_verification=None,
    )

    assert result["success"] is False
    assert result["publish_signal"] == ""
    assert result["failure_reason"] == "agent_did_not_return_done"


def test_detect_publish_success_from_page_text_and_url():
    service = PublishService()
    detection = service._detect_publish_success_from_state(
        page_url="https://mp.toutiao.com/profile_v4/graphic/publish",
        page_text="标题 测试001 提交成功 继续创作",
    )

    assert detection["success"] is True
    assert detection["signal"] == "success_text"
    assert detection["matched_text"] == "提交成功"


def test_detect_publish_success_from_url_change():
    service = PublishService()
    detection = service._detect_publish_success_from_state(
        page_url="https://mp.toutiao.com/profile_v4/manage/article",
        page_text="作品管理",
    )

    assert detection["success"] is True
    assert detection["signal"] == "post_publish_url"


def test_detect_publish_failure_from_page_text():
    service = PublishService()
    detection = service._detect_publish_failure_from_state(
        page_url="https://mp.toutiao.com/profile_v4/graphic/publish",
        page_text="发布失败 请重试",
    )

    assert detection["failed"] is True
    assert detection["signal"] == "failure_text"
    assert detection["matched_text"] == "发布失败"


def test_detect_publish_failure_ignores_unknown_publish_page_state():
    service = PublishService()
    detection = service._detect_publish_failure_from_state(
        page_url="https://mp.toutiao.com/profile_v4/graphic/publish",
        page_text="标题 再次测试002 预览并发布",
    )

    assert detection["failed"] is False


class DummyPage:
    async def evaluate(self, script, *args):
        return None


class DummySession:
    def __init__(self, urls):
        self._urls = iter(urls)
        self.page = DummyPage()

    async def get_current_page(self):
        return self.page

    async def get_current_page_url(self):
        return next(self._urls)


class DummyEvaluatePage:
    def __init__(self, responses):
        self._responses = iter(responses)

    async def evaluate(self, script, *args):
        return next(self._responses)


class DummySessionWithPage(DummySession):
    def __init__(self, urls, page):
        super().__init__(urls)
        self.page = page


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_try_collect_article_url_from_articles_page_first_item(monkeypatch):
    service = PublishService()
    session = DummySession(["https://mp.toutiao.com/profile_v4/graphic/publish"])
    calls = []

    async def fake_open_latest_article_from_articles_page(session_obj, title, logger):
        calls.append(("open_latest", title))
        return {
            "matched": True,
            "article_url": "https://mp.toutiao.com/article/1234567890",
            "detail_title": title,
        }

    monkeypatch.setattr(service, "_open_latest_article_from_articles_page", fake_open_latest_article_from_articles_page)

    article_url = asyncio.run(service._try_collect_article_url_after_publish(session, "再次测试002", None))

    assert article_url == "https://mp.toutiao.com/article/1234567890"
    assert ("open_latest", "再次测试002") in calls


def test_try_collect_article_url_returns_empty_when_first_article_title_mismatches(monkeypatch):
    service = PublishService()
    session = DummySession(["https://mp.toutiao.com/profile_v4/graphic/publish"])

    async def fake_open_latest_article_from_articles_page(session_obj, title, logger):
        return {
            "matched": False,
            "article_url": "https://mp.toutiao.com/article/1234567890",
            "detail_title": "别的文章",
        }

    monkeypatch.setattr(service, "_open_latest_article_from_articles_page", fake_open_latest_article_from_articles_page)

    article_url = asyncio.run(service._try_collect_article_url_after_publish(session, "再次测试002", None))

    assert article_url == ""


def test_try_collect_article_url_from_current_article_page():
    service = PublishService()
    session = DummySession(["https://mp.toutiao.com/article/1234567890"])

    article_url = asyncio.run(service._try_collect_article_url_after_publish(session, "再次测试002", None))

    assert article_url == "https://mp.toutiao.com/article/1234567890"


def test_open_latest_article_from_articles_page_handles_non_dict_evaluate_result():
    service = PublishService()
    page = DummyEvaluatePage([None, None, "unexpected-string"])
    session = DummySessionWithPage(
        ["https://mp.toutiao.com/profile_v4/graphic/articles"],
        page,
    )

    result = asyncio.run(service._open_latest_article_from_articles_page(session, "再次测试002", None))

    assert result == {"matched": False, "article_url": "", "detail_title": ""}


def test_open_latest_article_from_articles_page_accepts_stringified_json_result():
    service = PublishService()
    page = DummyEvaluatePage(
        [
            None,
            None,
            '{"clicked": true, "text": "再次测试002"}',
            "再次测试002",
        ]
    )
    session = DummySessionWithPage(
        [
            "https://mp.toutiao.com/article/1234567890",
        ],
        page,
    )

    result = asyncio.run(service._open_latest_article_from_articles_page(session, "再次测试002", None))

    assert result["matched"] is True
    assert result["article_url"] == "https://mp.toutiao.com/article/1234567890"
    assert result["detail_title"] == "再次测试002"


def test_open_latest_article_from_articles_page_uses_item_link_then_reads_article_url():
    service = PublishService()
    page = DummyEvaluatePage(
        [
            None,
            None,
            {"clicked": True, "text": "再次测试002"},
            "再次测试002",
        ]
    )
    session = DummySessionWithPage(
        [
            "https://mp.toutiao.com/item/1234567890",
            "https://mp.toutiao.com/article/1234567890",
        ],
        page,
    )

    result = asyncio.run(service._open_latest_article_from_articles_page(session, "再次测试002", None))

    assert result["matched"] is True
    assert result["article_url"] == "https://mp.toutiao.com/article/1234567890"


def test_open_latest_article_from_articles_page_logs_selected_item_link_and_redirect_details():
    service = PublishService()
    page = DummyEvaluatePage(
        [
            None,
            None,
            {
                "clicked": True,
                "text": "再次测试002",
                "href": "https://mp.toutiao.com/item/1234567890",
            },
            "再次测试002",
        ]
    )
    session = DummySessionWithPage(
        [
            "https://mp.toutiao.com/item/1234567890",
            "https://mp.toutiao.com/article/1234567890",
        ],
        page,
    )
    logger = logging.getLogger("test_item_link_logs")
    handler = ListHandler()
    logger.handlers = []
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    result = asyncio.run(service._open_latest_article_from_articles_page(session, "再次测试002", logger))

    assert result["matched"] is True
    joined = "\n".join(handler.messages)
    assert "selected article entry" in joined
    assert "href=https://mp.toutiao.com/item/1234567890" in joined
    assert "text=再次测试002" in joined
    assert "article entry redirect status" in joined
    assert "initial_url=https://mp.toutiao.com/item/1234567890" in joined
    assert "final_url=https://mp.toutiao.com/article/1234567890" in joined
    assert "article detail verification" in joined
    assert "expected=再次测试002" in joined
    assert "actual=再次测试002" in joined
    assert "matched=True" in joined


def test_confirm_publish_detection_ignores_model_reasoning_text():
    service = PublishService()
    history_item = SimpleNamespace(
        model_output=SimpleNamespace(thinking="下一步我要点击确认发布"),
        result=[SimpleNamespace(extracted_content='Clicked label "单图"', long_term_memory=None, error=None)],
    )

    assert service._did_execute_confirm_publish(history_item) is False


def test_confirm_publish_detection_requires_real_click_result():
    service = PublishService()
    history_item = SimpleNamespace(
        model_output=SimpleNamespace(thinking="准备发布"),
        result=[SimpleNamespace(extracted_content='Clicked button "确认发布"', long_term_memory=None, error=None)],
    )

    assert service._did_execute_confirm_publish(history_item) is True
