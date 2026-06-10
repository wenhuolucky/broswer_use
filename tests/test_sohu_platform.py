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
