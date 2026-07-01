from app.platforms.sohu.kernel import AutoSohuPublishService


def test_sohu_title_matched_draft_candidate_is_not_success():
    service = AutoSohuPublishService(account_id="122705471")

    result = service._select_candidate_article(
        [
            {
                "href": "https://mp.sohu.com/mpfe/v4/contentManagement/news/articlepreview?id=1044123495&accountId=122705471",
                "text": "法律普及 2026-07-01 11:30 草稿 栏目0 发布编辑 更多",
            }
        ],
        "法律普及",
    )

    assert result["matched"] is False
    assert result["status"] == "draft"
    assert result["article_url"] == ""
    assert "草稿" in result["reason"]


def test_sohu_title_matched_reviewing_candidate_returns_article_url():
    service = AutoSohuPublishService(account_id="122705471")

    result = service._select_candidate_article(
        [
            {
                "href": "https://mp.sohu.com/mpfe/v4/contentManagement/news/articlepreview?id=1044122306&accountId=122705471",
                "text": "劳动法的5个小知识 2026-07-01 11:27 审核中 栏目0 更多",
            }
        ],
        "劳动法的5个小知识",
    )

    assert result["matched"] is True
    assert result["status"] == "reviewing"
    assert result["article_url"] == "https://www.sohu.com/a/1044122306_122705471"
    assert result["reason"] == ""


def test_sohu_prefers_publishable_match_over_draft_match():
    service = AutoSohuPublishService(account_id="122705471")

    result = service._select_candidate_article(
        [
            {
                "href": "https://mp.sohu.com/mpfe/v4/contentManagement/news/articlepreview?id=1044123495&accountId=122705471",
                "text": "法律普及 2026-07-01 11:30 草稿 栏目0 发布编辑 更多",
            },
            {
                "href": "https://www.sohu.com/a/1044125555_122705471",
                "text": "法律普及 2026-07-01 11:35 阅读 0 评论 0 栏目0 编辑 更多",
            },
        ],
        "法律普及",
    )

    assert result["matched"] is True
    assert result["status"] == "published"
    assert result["article_url"] == "https://www.sohu.com/a/1044125555_122705471"


async def test_sohu_lookup_preserves_draft_failure_reason():
    service = AutoSohuPublishService(account_id="122705471")

    async def fake_open_latest_article_from_articles_page(session, title, logger):
        return {
            "matched": False,
            "article_url": "",
            "detail_title": "法律普及 2026-07-01 11:30 草稿 栏目0 发布编辑 更多",
            "status": "draft",
            "reason": "搜狐号文章仍为草稿，未发布成功",
        }

    service._open_latest_article_from_articles_page = fake_open_latest_article_from_articles_page

    result = await service._lookup_published_article_url(None, "法律普及", logger=None)

    assert result["found"] is False
    assert result["article_url"] == ""
    assert result["matched_title"] == "法律普及 2026-07-01 11:30 草稿 栏目0 发布编辑 更多"
    assert result["reason"] == "搜狐号文章仍为草稿，未发布成功"
