from __future__ import annotations

import re
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
    """Sohu 文章 URL 归一化（统一输出 https://www.sohu.com/a/{id}_{account_id}）。

    2026-06-10 重设计：原输出 https://m.sohu.com/a/{id}_{account_id}?sec=wd
    改为 https://www.sohu.com/a/{id}_{account_id}。所有历史 URL 形式
    （包括旧的 m.sohu.com 移动端 URL）都会被改写到 www.sohu.com，
    保持 API 返回值的一致性。

    支持的输入 pattern：

    1) https://mp.sohu.com/h5/v2/newsPreview?id=...&type=article
       旧版预览 URL，URL 自身不带 account_id，只能由调用方传入
       （来自该渠道 metadata 的 account_number）。

    2) https://mp.sohu.com/mpfe/v4/contentManagement/news/articlepreview?id=...&accountId=...
       以及中间带 first/page 段的变体：
       https://mp.sohu.com/mpfe/v4/contentManagement/first/page/news/articlepreview?id=...&accountId=...
       后台管理列表里点文章标题拿到的预览 URL，URL 自身带 accountId 参数。

    3) https://m.sohu.com/a/{id}_{account_id}?sec=wd
       旧版移动端 URL，rewriting 到 www.sohu.com。

    4) https://www.sohu.com/a/{id}_{account_id}
       已经是最终 URL，passthrough。
    """
    if not url:
        return url

    parsed = urlparse(url)
    netloc = parsed.netloc
    path = parsed.path
    query = parse_qs(parsed.query)

    # Pattern 4: 已经是 www.sohu.com/a/...，passthrough
    if netloc == "www.sohu.com" and path.startswith("/a/"):
        return url

    # Pattern 3: 旧版 m.sohu.com/a/{id}_{account_id}?sec=wd，rewriting
    if netloc == "m.sohu.com" and path.startswith("/a/"):
        # 路径格式: /a/{id}_{account_id}
        match = re.match(r"^/a/(\d+)_(\d+)$", path)
        if match:
            article_id, url_account_id = match.groups()
            return f"https://www.sohu.com/a/{article_id}_{url_account_id}"
        # 路径不符合 {id}_{account_id} 形式，原样返回
        return url

    if netloc != "mp.sohu.com":
        return url

    # Pattern 1: /h5/v2/newsPreview — 旧版预览 URL，account_id 只能由调用方传入
    if path == "/h5/v2/newsPreview":
        article_id = query.get("id", [""])[0]
        if article_id and account_id:
            return f"https://www.sohu.com/a/{article_id}_{account_id}"
        return url

    # Pattern 2: /.../news/articlepreview — 后台管理列表的预览 URL
    # 路径中间可能有 first/page 等额外段，统一以 /news/articlepreview 结尾匹配
    if path.endswith("/news/articlepreview"):
        article_id = query.get("id", [""])[0]
        # env 传进来的 account_id 优先级最高；URL 自身 accountId 作 fallback
        effective_account_id = account_id or query.get("accountId", [""])[0]
        if article_id and effective_account_id:
            return f"https://www.sohu.com/a/{article_id}_{effective_account_id}"
        return url

    return url


def normalize_article_url(platform: str, url: str, account_id: str = "") -> str:
    if platform == "toutiao":
        return normalize_toutiao_article_url(url)
    if platform == "sohu":
        return normalize_sohu_article_url(url, account_id)
    return url
