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
