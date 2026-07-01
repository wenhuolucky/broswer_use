from app.publishing.kernel import PublishService


def test_toutiao_no_cover_prompt_requires_preview_then_confirm():
    service = PublishService()

    bundle = service._build_publish_task(
        title="劳动法的5个小知识",
        content="这是一段用于发布测试的正文内容。",
        cover_path=None,
        logger=None,
        cover_loop_exceeded=False,
    )

    assert "选择'无封面'只是封面设置步骤完成，不代表已经发布" in bundle.task
    assert "先点击页面底部的“预览并发布”按钮" in bundle.task
    assert "再点击弹出的最终确认层里的“确认发布”按钮" in bundle.task
    assert "只有完成“确认发布”点击后，才允许调用 get_published_article_url" in bundle.task
