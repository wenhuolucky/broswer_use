"""平台注册表测试（app/platforms/registry.py）。

注册表是「有哪些平台」的单一事实来源；适配器、编排器、job store、
/platforms 路由都经过它。这里只验证查询/解析逻辑，
不触发 kernel 工厂（那会拉起 browser_use / Playwright）。
"""

from __future__ import annotations

import pytest

from app.platforms import registry
from app.platforms.base import PlatformConfig


def test_is_supported() -> None:
    assert registry.is_supported("toutiao") is True
    assert registry.is_supported("sohu") is True
    assert registry.is_supported("weibo") is False
    assert registry.is_supported("") is False


def test_config_for_known() -> None:
    cfg = registry.config_for("toutiao")
    assert isinstance(cfg, PlatformConfig)
    assert cfg.name == "toutiao"


def test_config_for_unknown_returns_none() -> None:
    assert registry.config_for("weibo") is None


def test_configs_contains_all_platforms() -> None:
    cfgs = registry.configs()
    assert set(cfgs) == {"toutiao", "sohu"}
    assert all(isinstance(c, PlatformConfig) for c in cfgs.values())


def test_kernel_for_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unsupported platform"):
        registry.kernel_for("weibo")


def test_kernel_for_blank_raises() -> None:
    # kernel_for 会 strip()，空白也应被当作未知平台
    with pytest.raises(ValueError):
        registry.kernel_for("   ")
