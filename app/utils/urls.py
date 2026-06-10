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
    """Sohu 文章 URL 归一化。

    支持以下 pattern 转成官方移动端 URL https://m.sohu.com/a/{id}_{account_id}?sec=wd：

    1) https://mp.sohu.com/h5/v2/newsPreview?id=...&type=article
       旧版预览 URL，需要 env 传入的 account_id。

    2) https://mp.sohu.com/mpfe/v4/contentManagement/news/articlepreview?id=...&accountId=...
       以及中间带 first/page 段的变体：
       https://mp.sohu.com/mpfe/v4/contentManagement/first/page/news/articlepreview?id=...&accountId=...
       后台管理列表里点文章标题拿到的预览 URL，URL 自身带 accountId 参数。

    3) https://m.sohu.com/a/{id}_{account_id}?sec=wd
       已经是移动端 URL，直接 passthrough。
    """
    if not url:
        return url

    if url.startswith("https://m.sohu.com/a/"):
        return url

    parsed = urlparse(url)
    if parsed.netloc != "mp.sohu.com":
        return url

    path = parsed.path
    query = parse_qs(parsed.query)

    # Pattern 1: /h5/v2/newsPreview — 旧版预览 URL，account_id 只能从 env 来
    if path == "/h5/v2/newsPreview":
        article_id = query.get("id", [""])[0]
        if article_id and account_id:
            return f"https://m.sohu.com/a/{article_id}_{account_id}?sec=wd"
        return url

    # Pattern 2: /.../news/articlepreview — 后台管理列表的预览 URL
    # 路径中间可能有 first/page 等额外段，统一以 /news/articlepreview 结尾匹配
    if path.endswith("/news/articlepreview"):
        article_id = query.get("id", [""])[0]
        # URL 自身带的 accountId 优先；env 传进来的作 fallback
        effective_account_id = account_id or query.get("accountId", [""])[0]
        if article_id and effective_account_id:
            return f"https://m.sohu.com/a/{article_id}_{effective_account_id}?sec=wd"
        return url

    return url


def normalize_article_url(platform: str, url: str, account_id: str = "") -> str:
    if platform == "toutiao":
        return normalize_toutiao_article_url(url)
    if platform == "sohu":
        return normalize_sohu_article_url(url, account_id)
    return url
