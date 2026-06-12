"""公共 fixture。

阶段一只覆盖纯函数单测，依赖很轻；这里主要提供环境变量隔离，
避免本机 .env 里的 SOHU_ACCOUNT_ID 等变量污染测试。
"""

from __future__ import annotations

import pytest

# 与搜狐账号映射相关的环境变量，测试前统一清空，保证用例可复现。
_SOHU_ENV_VARS = ("SOHU_ACCOUNT_ID", "SOHU_ACCOUNT_ID_MAP")


@pytest.fixture(autouse=True)
def _clean_sohu_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SOHU_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
