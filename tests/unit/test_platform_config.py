"""平台配置测试（app/platforms/base.py + sohu/config.py + toutiao/config.py）。

聚焦不触发浏览器的纯配置逻辑：account id 解析、正文探针、cookie 校验、账号名提取。
get_agent_prompt 属于 prompt 工程，文本易变，这里不做断言。
"""

from __future__ import annotations

from app.platforms.base import PlatformConfig
from app.platforms.sohu.config import SohuPlatform
from app.platforms.toutiao.config import ToutiaoPlatform


class TestSohuArticleUrlAccountId:
    def test_reads_account_number_from_metadata(self) -> None:
        assert SohuPlatform().article_url_account_id({"account_number": "122702850"}) == "122702850"

    def test_strips_whitespace(self) -> None:
        assert SohuPlatform().article_url_account_id({"account_number": " 111 "}) == "111"

    def test_missing_metadata_returns_empty(self) -> None:
        assert SohuPlatform().article_url_account_id({}) == ""
        assert SohuPlatform().article_url_account_id(None) == ""


class TestToutiaoArticleUrlAccountId:
    def test_uses_base_default_empty(self) -> None:
        # 头条不需要 account id，沿用 base 默认空串
        assert ToutiaoPlatform().article_url_account_id({"account_number": "x"}) == ""


class TestBodyProbe:
    def test_strips_markdown_and_compacts(self) -> None:
        # 图片/链接/标题/强调符号被去掉，空白被压缩，截取前 30 字符
        probe = PlatformConfig.body_probe("# 标题\n\n这是**正文** [链接](http://x) ![图](http://y)")
        assert probe == "标题这是正文链接"

    def test_empty(self) -> None:
        assert PlatformConfig.body_probe("") == ""
        assert PlatformConfig.body_probe(None) == ""

    def test_truncates_to_30(self) -> None:
        assert len(PlatformConfig.body_probe("一" * 100)) == 30

    def test_matches_sohu_prompt_probe(self) -> None:
        # 内核与搜狐 prompt 必须共用同一算法，两边探针一致
        content = "# 标题\n\n正文内容 *斜体* 与 [链接](http://x)"
        plain = PlatformConfig.strip_markdown(content)
        assert PlatformConfig.body_probe(content) == "".join(plain.split())[:30]


class TestBaseHelpers:
    def _cfg(self) -> PlatformConfig:
        return PlatformConfig(
            name="demo",
            home_url="https://demo.example",
            publish_url="https://demo.example/publish",
            auth_domains=["demo.example"],
            account_cookies=["acc_name"],
        )

    def test_validate_cookies_match(self) -> None:
        cfg = self._cfg()
        assert cfg.validate_cookies([{"domain": "www.demo.example"}]) is True

    def test_validate_cookies_reverse_containment(self) -> None:
        # base 实现是双向包含：domain in auth_domain 或 auth_domain in domain
        cfg = self._cfg()
        assert cfg.validate_cookies([{"domain": "demo"}]) is True

    def test_validate_cookies_no_match(self) -> None:
        cfg = self._cfg()
        assert cfg.validate_cookies([{"domain": "other.com"}]) is False

    def test_validate_cookies_empty(self) -> None:
        assert self._cfg().validate_cookies([]) is False

    def test_extract_account_name(self) -> None:
        cfg = self._cfg()
        cookies = [
            {"name": "irrelevant", "value": "x"},
            {"name": "acc_name", "value": "我的账号"},
        ]
        assert cfg.extract_account_name(cookies) == "我的账号"

    def test_extract_account_name_missing(self) -> None:
        cfg = self._cfg()
        assert cfg.extract_account_name([{"name": "irrelevant", "value": "x"}]) == ""
