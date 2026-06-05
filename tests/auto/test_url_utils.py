from auto.url_utils import normalize_toutiao_article_url


def test_normalize_toutiao_article_url_keeps_empty_url():
    assert normalize_toutiao_article_url("") == ""


def test_normalize_toutiao_article_url_keeps_mobile_article_url():
    url = "https://m.toutiao.com/article/7646246126252835364/?source=m_redirect"

    assert normalize_toutiao_article_url(url) == url


def test_normalize_toutiao_article_url_converts_mp_preview_url_with_pgc_id():
    url = "https://mp.toutiao.com/profile_v4/graphic/preview?pgc_id=7646246126252835364"

    assert (
        normalize_toutiao_article_url(url)
        == "https://www.toutiao.com/article/7646246126252835364/?&source=m_redirect"
    )


def test_normalize_toutiao_article_url_keeps_preview_url_without_numeric_pgc_id():
    url = "https://mp.toutiao.com/profile_v4/graphic/preview?pgc_id=abc"

    assert normalize_toutiao_article_url(url) == url


def test_normalize_toutiao_article_url_keeps_other_urls():
    url = "https://www.toutiao.com/item/7646246126252835364/"

    assert normalize_toutiao_article_url(url) == url
