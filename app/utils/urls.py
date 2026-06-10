from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def normalize_toutiao_article_url(url: str) -> str:
    if not url:
        return url

    if url.startswith(("https://m.toutiao.com/article/", "http://m.toutiao.com/article/")):
        return url

    parsed = urlparse(url)
    if parsed.netloc == "mp.toutiao.com" and parsed.path == "/profile_v4/graphic/preview":
        pgc_id = parse_qs(parsed.query).get("pgc_id", [""])[0]
        if pgc_id.isdigit():
            return f"https://www.toutiao.com/article/{pgc_id}/?&source=m_redirect"

    return url


def normalize_sohu_article_url(url: str, account_id: str = "") -> str:
    if not url:
        return url

    if url.startswith("https://m.sohu.com/a/"):
        return url

    parsed = urlparse(url)
    if parsed.netloc == "mp.sohu.com" and parsed.path == "/h5/v2/newsPreview":
        article_id = parse_qs(parsed.query).get("id", [""])[0]
        if article_id and account_id:
            return f"https://m.sohu.com/a/{article_id}_{account_id}?sec=wd"

    return url


def normalize_article_url(platform: str, url: str, account_id: str = "") -> str:
    if platform == "toutiao":
        return normalize_toutiao_article_url(url)
    if platform == "sohu":
        return normalize_sohu_article_url(url, account_id)
    return url
