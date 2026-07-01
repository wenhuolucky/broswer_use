from app.publishing.kernel import PublishService


class FakeHistory:
    def __init__(self, final: str, errors: list[str] | None = None, successful: bool = False):
        self._final = final
        self._errors = errors or []
        self._successful = successful
        self.history = []

    def final_result(self):
        return self._final

    def is_successful(self):
        return self._successful

    def errors(self):
        return self._errors


def test_parse_agent_outcome_prefers_business_failure_reason_over_history_errors():
    service = PublishService()
    history = FakeHistory(
        final=(
            "任务执行结果：部分成功\n\n"
            "已完成步骤：\n"
            "1. 成功写入正文\n\n"
            "失败原因：\n"
            "搜狐号平台显示'今日发布的文章已达上限'，这是平台每日文章发布数量限制。"
            "文章已成功保存为草稿状态，但无法在今天完成最终发布。\n\n"
            "当前文章状态：草稿"
        ),
        errors=[
            "Invalid model output format. Please follow the correct schema. "
            "Input should be an object"
        ],
        successful=False,
    )

    result = service._parse_agent_outcome(history, tracker=None, logger=None)

    assert result["success"] is False
    assert "今日发布的文章已达上限" in result["failure_reason"]
    assert "Invalid model output format" not in result["failure_reason"]


def test_parse_agent_outcome_keeps_history_error_when_no_business_reason():
    service = PublishService()
    history = FakeHistory(
        final="任务失败，但没有明确业务原因",
        errors=["Invalid model output format. Please follow the correct schema."],
        successful=False,
    )

    result = service._parse_agent_outcome(history, tracker=None, logger=None)

    assert result["success"] is False
    assert result["failure_reason"] == "Invalid model output format. Please follow the correct schema."


def test_detect_publish_failure_matches_daily_publish_limit_text():
    service = PublishService()

    result = service._detect_publish_failure_from_state(
        page_url="https://mp.sohu.com/mpfe/v4/contentManagement/first/page",
        page_text="您今日发布的文章已达上限，您还可以发布 0 篇图集和 10 篇动态",
    )

    assert result["failed"] is True
    assert result["signal"] == "failure_text"
    assert result["matched_text"] == "今日发布的文章已达上限"
