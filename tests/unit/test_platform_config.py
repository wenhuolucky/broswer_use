"""平台配置测试（app/platforms/base.py + sohu/config.py + toutiao/config.py）。

聚焦不触发浏览器的纯配置逻辑：账号映射解析、cookie 校验、账号名提取。
get_agent_prompt 属于 prompt 工程，文本易变，这里不做断言。
"""

from __future__ import annotations

import pytest

from app.platforms.base import PlatformConfig
from app.platforms.sohu.config import SohuPlatform
from app.platforms.toutiao.config import ToutiaoPlatform


class TestSohuAccountIdMap:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("", {}),
            ("u1:111", {"u1": "111"}),
            ("u1:111,u2:222", {"u1": "111", "u2": "222"}),
            # 无冒号的项被忽略
            ("u1:111,garbage,u2:222", {"u1": "111", "u2": "222"}),
            # 两侧空白被 trim
            (" u1 : 111 , u2:222 ", {"u1": "111", "u2": "222"}),
            # value 含冒号：只在第一个冒号切分
            ("u1:a:b", {"u1": "a:b"}),
            # 空 user 或空 account 被丢弃
            (":111,u2:", {}),
        ],
    )
    def test_parse_account_id_map(self, raw: str, expected: dict) -> None:
        assert SohuPlatform._parse_account_id_map(raw) == expected


class TestSohuAccountIdForUser:
    def test_map_hit_takes_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOHU_ACCOUNT_ID_MAP", "u1:111,u2:222")
        monkeypatch.setenv("SOHU_ACCOUNT_ID", "999")
        assert SohuPlatform().account_id_for_user("u1") == "111"

    def test_falls_back_to_single_account(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOHU_ACCOUNT_ID_MAP", "u1:111")
        monkeypatch.setenv("SOHU_ACCOUNT_ID", "999")
        # u2 不在 map 中 → 回落到单账号
        assert SohuPlatform().account_id_for_user("u2") == "999"

    def test_no_config_returns_empty(self) -> None:
        # conftest 的 autouse fixture 已清空相关 env
        assert SohuPlatform().account_id_for_user("u1") == ""


class TestToutiaoAccountId:
    def test_uses_base_default_empty(self) -> None:
        # 头条不需要 account_id，沿用 base 默认空串
        assert ToutiaoPlatform().account_id_for_user("anyone") == ""


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
