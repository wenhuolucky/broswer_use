import json

import pytest

from auto.cookie_store import CookieStore
from auto.models import AutoPublishRequest


def test_auto_publish_request_requires_user_id():
    with pytest.raises(Exception):
        AutoPublishRequest(platform="toutiao", title="t", content="c")


def test_cookie_store_saves_under_platform_and_user(tmp_path):
    store = CookieStore(base_dir=tmp_path)
    cookies = [{"name": "sid", "value": "1", "domain": ".toutiao.com"}]

    path = store.save("toutiao", "user1", cookies)

    assert path == tmp_path / "toutiao" / "user1.json"
    assert json.loads(path.read_text(encoding="utf-8"))["cookies"] == cookies
    assert store.load("toutiao", "user1") == cookies


def test_cookie_store_validates_platform_domains(tmp_path):
    store = CookieStore(base_dir=tmp_path)

    assert not store.has_valid_cookie("toutiao", "missing")

    store.save("toutiao", "user1", [{"name": "sid", "value": "1", "domain": ".example.com"}])
    assert not store.has_valid_cookie("toutiao", "user1")

    store.save("toutiao", "user1", [{"name": "uid_tt", "value": "1", "domain": ".toutiao.com"}])
    assert store.has_valid_cookie("toutiao", "user1")


def test_cookie_store_rejects_toutiao_visitor_cookies_without_login_identity(tmp_path):
    store = CookieStore(base_dir=tmp_path)
    store.save("toutiao", "user1", [
        {"name": "s_v_web_id", "value": "verify_x", "domain": "mp.toutiao.com"},
        {"name": "passport_csrf_token", "value": "csrf", "domain": ".toutiao.com"},
        {"name": "ttwid", "value": "visitor", "domain": ".toutiao.com"},
    ])

    assert not store.has_valid_cookie("toutiao", "user1")
