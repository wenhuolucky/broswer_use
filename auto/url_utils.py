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
