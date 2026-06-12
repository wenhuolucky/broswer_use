"""文章 URL 归一化测试（app/utils/urls.py）。

这是回归价值最高的纯逻辑模块：头条/搜狐各有多种历史 URL 形态，
归一化结果直接进入 API 返回值，必须钉死每条分支。
"""

from __future__ import annotations

import pytest

from app.utils.urls import (
    normalize_article_url,
    normalize_sohu_article_url,
    normalize_toutiao_article_url,
)


class TestToutiao:
    @pytest.mark.parametrize(
        "url, expected",
        [
            # 空串原样返回
            ("", ""),
            # 已是 m.toutiao.com/article/，直接放行（http/https 都放行）
            (
                "https://m.toutiao.com/article/123456/",
                "https://m.toutiao.com/article/123456/",
            ),
            (
                "http://m.toutiao.com/article/123456/",
                "http://m.toutiao.com/article/123456/",
            ),
            # 预览 URL 带数字 pgc_id → 改写为正式文章 URL
            (
                "https://mp.toutiao.com/profile_v4/graphic/preview?pgc_id=789",
                "https://www.toutiao.com/article/789/?&source=m_redirect",
            ),
            # pgc_id 非数字 → 原样返回
            (
                "https://mp.toutiao.com/profile_v4/graphic/preview?pgc_id=abc",
                "https://mp.toutiao.com/profile_v4/graphic/preview?pgc_id=abc",
            ),
            # 缺 pgc_id → 原样返回
            (
                "https://mp.toutiao.com/profile_v4/graphic/preview",
                "https://mp.toutiao.com/profile_v4/graphic/preview",
            ),
            # 域名/路径不匹配 → 原样返回
            (
                "https://example.com/whatever?pgc_id=789",
                "https://example.com/whatever?pgc_id=789",
            ),
        ],
    )
    def test_normalize(self, url: str, expected: str) -> None:
        assert normalize_toutiao_article_url(url) == expected


class TestSohu:
    @pytest.mark.parametrize(
        "url, account_id, expected",
        [
            # 空串原样
            ("", "", ""),
            # Pattern 4: 已是 www.sohu.com/a/...，passthrough（即使带 query）
            (
                "https://www.sohu.com/a/111_222",
                "",
                "https://www.sohu.com/a/111_222",
            ),
            # Pattern 3: 旧版 m.sohu.com/a/{id}_{acc} → 改写到 www
            (
                "https://m.sohu.com/a/111_222?sec=wd",
                "",
                "https://www.sohu.com/a/111_222",
            ),
            # Pattern 3 反例：m.sohu.com/a/ 但路径不符 {id}_{acc} → 原样
            (
                "https://m.sohu.com/a/notmatch",
                "",
                "https://m.sohu.com/a/notmatch",
            ),
            # Pattern 1: /h5/v2/newsPreview，account_id 由 env 传入
            (
                "https://mp.sohu.com/h5/v2/newsPreview?id=555&type=article",
                "999",
                "https://www.sohu.com/a/555_999",
            ),
            # Pattern 1 缺 account_id → 原样返回
            (
                "https://mp.sohu.com/h5/v2/newsPreview?id=555&type=article",
                "",
                "https://mp.sohu.com/h5/v2/newsPreview?id=555&type=article",
            ),
            # Pattern 2: 后台预览 URL，env account_id 优先
            (
                "https://mp.sohu.com/mpfe/v4/contentManagement/news/articlepreview?id=666&accountId=777",
                "999",
                "https://www.sohu.com/a/666_999",
            ),
            # Pattern 2: 无 env account_id 时回落到 URL 自带 accountId
            (
                "https://mp.sohu.com/mpfe/v4/contentManagement/news/articlepreview?id=666&accountId=777",
                "",
                "https://www.sohu.com/a/666_777",
            ),
            # Pattern 2: 路径中间带 first/page 段的变体
            (
                "https://mp.sohu.com/mpfe/v4/contentManagement/first/page/news/articlepreview?id=666&accountId=777",
                "",
                "https://www.sohu.com/a/666_777",
            ),
            # Pattern 2: 既无 env 也无 URL accountId → 原样
            (
                "https://mp.sohu.com/mpfe/v4/contentManagement/news/articlepreview?id=666",
                "",
                "https://mp.sohu.com/mpfe/v4/contentManagement/news/articlepreview?id=666",
            ),
            # 非 sohu 已知域名 → 原样
            (
                "https://example.com/a/1_2",
                "",
                "https://example.com/a/1_2",
            ),
        ],
    )
    def test_normalize(self, url: str, account_id: str, expected: str) -> None:
        assert normalize_sohu_article_url(url, account_id) == expected


class TestDispatch:
    def test_toutiao(self) -> None:
        url = "https://mp.toutiao.com/profile_v4/graphic/preview?pgc_id=789"
        assert (
            normalize_article_url("toutiao", url)
            == "https://www.toutiao.com/article/789/?&source=m_redirect"
        )

    def test_sohu_passes_account_id(self) -> None:
        url = "https://mp.sohu.com/h5/v2/newsPreview?id=555&type=article"
        assert (
            normalize_article_url("sohu", url, account_id="999")
            == "https://www.sohu.com/a/555_999"
        )

    def test_unknown_platform_passthrough(self) -> None:
        assert normalize_article_url("weibo", "https://x.com/p/1") == "https://x.com/p/1"
