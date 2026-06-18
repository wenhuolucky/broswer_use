"""Publish kernel helper tests."""

from __future__ import annotations

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
