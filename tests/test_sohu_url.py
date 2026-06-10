from __future__ import annotations


def test_normalize_sohu_preview_url_to_mobile_url():
    from app.utils.urls import normalize_sohu_article_url

    url = "https://mp.sohu.com/h5/v2/newsPreview?id=1020931946&type=article"

    assert normalize_sohu_article_url(url, "122702850") == "https://m.sohu.com/a/1020931946_122702850?sec=wd"


def test_normalize_sohu_mobile_url_passthrough():
    from app.utils.urls import normalize_sohu_article_url

    url = "https://m.sohu.com/a/1020931946_122702850?sec=wd"

    assert normalize_sohu_article_url(url, "122702850") == url


def test_normalize_sohu_preview_without_account_id_returns_original():
    from app.utils.urls import normalize_sohu_article_url

    url = "https://mp.sohu.com/h5/v2/newsPreview?id=1020931946&type=article"

    assert normalize_sohu_article_url(url, "") == url


def test_normalize_article_url_routes_by_platform():
    from app.utils.urls import normalize_article_url

    sohu_url = "https://mp.sohu.com/h5/v2/newsPreview?id=1020931946&type=article"

    assert (
        normalize_article_url("sohu", sohu_url, account_id="122702850")
        == "https://m.sohu.com/a/1020931946_122702850?sec=wd"
    )
