"""Publish kernel helper tests."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from app.publishing.kernel import PublishTaskBundle
from app.publishing.kernel import PublishService


class TestActionSummary:
    def test_detects_done_action_model_summary(self) -> None:
        assert PublishService._action_summary_is_done(
            'root=DoneActionModel(done=DoneAction(text="{\\"success\\": true}"))'
        )

    def test_detects_generic_done_summary(self) -> None:
        assert PublishService._action_summary_is_done("done")

    def test_does_not_treat_non_done_text_as_done(self) -> None:
        assert not PublishService._action_summary_is_done("Clicked button '确认发布'")


class TestPostConfirmLookupResult:
    def test_builds_success_result_from_found_article_url(self) -> None:
        lookup = {
            "found": True,
            "article_url": "https://www.toutiao.com/article/123/",
            "reason": "",
        }

        result = PublishService._build_post_confirm_lookup_result(lookup)

        assert result == {
            "success": True,
            "article_url": "https://www.toutiao.com/article/123/",
            "account": "",
            "failure_reason": "",
            "publish_signal": "post_confirm_lookup_found",
        }

    def test_builds_failure_result_when_lookup_misses(self) -> None:
        lookup = {"found": False, "article_url": "", "reason": "not_found_after_3_attempts"}

        result = PublishService._build_post_confirm_lookup_result(lookup)

        assert result["success"] is False
        assert result["article_url"] == ""
        assert result["failure_reason"] == "not_found_after_3_attempts"
        assert result["publish_signal"] == "post_confirm_lookup_miss"


class TestBodyCompletenessGuard:
    def test_rejects_probe_only_body_before_publish(self) -> None:
        result = PublishService._body_completeness_from_state(
            page_state={
                "editor_text_length": 30,
                "probe_found": True,
                "editor_source": "contenteditable",
            },
            expected_text_length=608,
        )

        assert result["ok"] is False
        assert result["reason"] == "body_incomplete"

    def test_accepts_body_near_expected_length(self) -> None:
        result = PublishService._body_completeness_from_state(
            page_state={
                "editor_text_length": 590,
                "probe_found": True,
                "editor_source": "contenteditable",
            },
            expected_text_length=608,
        )

        assert result["ok"] is True


class TestCleanupWithTimeout:
    @pytest.mark.asyncio
    async def test_cleanup_awaitable_times_out_without_raising(self) -> None:
        async def slow_cleanup():
            await asyncio.sleep(0.2)

        result = await PublishService._cleanup_awaitable_with_timeout(
            label="runner",
            awaitable_factory=slow_cleanup,
            timeout_seconds=0.01,
            logger=None,
        )

        assert result == {"label": "runner", "closed": False, "timed_out": True}

    @pytest.mark.asyncio
    async def test_cleanup_awaitable_reports_success(self) -> None:
        async def fast_cleanup():
            return None

        result = await PublishService._cleanup_awaitable_with_timeout(
            label="browser",
            awaitable_factory=fast_cleanup,
            timeout_seconds=0.5,
            logger=None,
        )

        assert result == {"label": "browser", "closed": True, "timed_out": False}


class TestPublishTools:
    def test_clipboard_permission_origins_follow_platform(self) -> None:
        assert PublishService._clipboard_permission_origins("sohu") == ["https://mp.sohu.com"]
        assert PublishService._clipboard_permission_origins("toutiao") == ["https://mp.toutiao.com"]
        assert PublishService._clipboard_permission_origins("unknown") == ["https://mp.toutiao.com"]

    def test_registers_sohu_cover_tool_only_for_sohu_platform(self, monkeypatch) -> None:
        registered_actions: list[str] = []

        class FakeActionResult:
            def __init__(self, extracted_content: str, include_in_memory: bool):
                self.extracted_content = extracted_content
                self.include_in_memory = include_in_memory

        class FakeController:
            def action(self, description: str):
                def decorator(func):
                    registered_actions.append(func.__name__)
                    return func

                return decorator

        fake_browser_use = types.SimpleNamespace(
            ActionResult=FakeActionResult,
            Controller=FakeController,
        )
        monkeypatch.setitem(sys.modules, "browser_use", fake_browser_use)

        PublishService()._build_publish_tools(
            logger=None,
            body_payload=PublishTaskBundle(
                task="",
                plain_text="body",
                rich_html="",
                body_probe="body",
                is_markdown=False,
                platform_name="sohu",
            ),
        )
        assert "set_sohu_title" in registered_actions
        assert "set_sohu_cover" in registered_actions

        registered_actions.clear()
        PublishService()._build_publish_tools(
            logger=None,
            body_payload=PublishTaskBundle(
                task="",
                plain_text="body",
                rich_html="",
                body_probe="body",
                is_markdown=False,
                platform_name="toutiao",
            ),
        )
        assert "set_sohu_title" not in registered_actions
        assert "set_sohu_cover" not in registered_actions
