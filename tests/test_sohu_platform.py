from __future__ import annotations


def test_sohu_account_id_prefers_user_map(monkeypatch):
    from app.platforms.sohu import SohuPlatform

    monkeypatch.setenv("SOHU_ACCOUNT_ID_MAP", "user1:111,user2:222")
    monkeypatch.setenv("SOHU_ACCOUNT_ID", "999")

    assert SohuPlatform().account_id_for_user("user2") == "222"


def test_sohu_account_id_uses_global_fallback(monkeypatch):
    from app.platforms.sohu import SohuPlatform

    monkeypatch.delenv("SOHU_ACCOUNT_ID_MAP", raising=False)
    monkeypatch.setenv("SOHU_ACCOUNT_ID", "999")

    assert SohuPlatform().account_id_for_user("user1") == "999"


def test_sohu_account_id_returns_empty_when_unconfigured(monkeypatch):
    from app.platforms.sohu import SohuPlatform

    monkeypatch.delenv("SOHU_ACCOUNT_ID_MAP", raising=False)
    monkeypatch.delenv("SOHU_ACCOUNT_ID", raising=False)

    assert SohuPlatform().account_id_for_user("user1") == ""


def test_sohu_platform_validates_sohu_cookie_domain():
    from app.platforms.sohu import SohuPlatform

    cookies = [{"name": "SUV", "value": "v", "domain": ".sohu.com"}]

    assert SohuPlatform().validate_cookies(cookies) is True


def test_sohu_prompt_uses_plain_text_body_and_requires_body_guard():
    from app.platforms.sohu import SohuPlatform

    prompt = SohuPlatform().get_agent_prompt(
        title="title",
        content="# 标题\n\n正文第一段",
        cover_instruction="不需要封面",
        body_image_instruction="",
        is_markdown=True,
        rich_html="<h1>标题</h1><p>正文第一段</p>",
    )

    assert "搜狐号正文写入策略" in prompt
    assert "只使用纯文本写入正文" in prompt
    assert "不要把 HTML 源码写入搜狐号编辑器" in prompt
    assert "发布前正文校验" in prompt
    assert "搜狐号正文写入失败，发布前未检测到正文内容" in prompt
    assert "<h1>标题</h1>" not in prompt
