from app.publishing.kernel import PublishService
from app.platforms.sohu.kernel import AutoSohuPublishService


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


def test_sohu_no_cover_prompt_uses_sohu_specific_cover_rules():
    service = AutoSohuPublishService(account_id="122705471")

    bundle = service._build_publish_task(
        title="劳动法的5个小知识",
        content="正文前\n\n![图](https://example.com/a.jpg)\n\n正文后",
        cover_path=None,
        logger=None,
        cover_loop_exceeded=False,
    )

    assert "搜狐号封面设置：本次没有提供外部封面图片" in bundle.task
    assert "如果正文中有图片" in bundle.task
    assert "选择正文中的第一张图片作为封面" in bundle.task
    assert "如果正文中没有图片：不要设置封面" in bundle.task
    assert "不要打开素材库" in bundle.task
    assert "搜狐号后续仍然必须点击页面底部的“发布”按钮" in bundle.task
    assert "如果头条自动将正文第一张图片设为封面" not in bundle.task
    assert "选择'无封面'" not in bundle.task
    assert "预览并发布" not in bundle.task
    assert "确认发布" not in bundle.task
