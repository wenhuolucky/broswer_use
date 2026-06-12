"""Cookie 归一化测试（app/cookies/normalize.py）。

确保各种来源（浏览器导出、扩展导出）的 cookie 都被规整成
Playwright 可接受的 auth state：sameSite 合法、expires 语义正确。
"""

from __future__ import annotations

import pytest

from app.cookies.normalize import (
    _normalize_expires,
    _normalize_samesite,
    normalize_cookie,
    normalize_cookie_list,
)


class TestSameSite:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (None, "Lax"),
            ("Strict", "Strict"),
            ("Lax", "Lax"),
            ("None", "None"),
            # 别名映射
            ("no_restriction", "None"),
            ("unspecified", "Lax"),
            ("", "Lax"),
            # 大小写不敏感
            ("LAX", "Lax"),
            ("strict", "Strict"),
            ("none", "None"),
            # 带空白
            ("  Strict  ", "Strict"),
            # 非法值兜底
            ("garbage", "Lax"),
            (123, "Lax"),
        ],
    )
    def test_normalize_samesite(self, value: object, expected: str) -> None:
        assert _normalize_samesite(value) == expected


class TestExpires:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (None, -1),
            (0, 0.0),
            (1700000000, 1700000000.0),
            ("1700000000", 1700000000.0),
            (1700000000.5, 1700000000.5),
            # 不可解析 → -1
            ("not-a-number", -1),
            ([], -1),
        ],
    )
    def test_normalize_expires(self, value: object, expected: float) -> None:
        assert _normalize_expires(value) == expected


class TestNormalizeCookie:
    def test_minimal_defaults(self) -> None:
        out = normalize_cookie({"name": "sid", "value": "x"})
        assert out["name"] == "sid"
        assert out["value"] == "x"
        assert out["path"] == "/"
        assert out["httpOnly"] is False
        assert out["secure"] is False
        assert out["sameSite"] == "Lax"
        # 缺省无 expires → -1
        assert out["expires"] == -1

    def test_expirationdate_fallback(self) -> None:
        # 浏览器扩展常用 expirationDate 而非 expires
        out = normalize_cookie({"name": "sid", "expirationDate": 1700000000})
        assert out["expires"] == 1700000000.0

    def test_session_cookie_without_expiration(self) -> None:
        out = normalize_cookie({"name": "sid", "session": True})
        assert out["expires"] == -1

    def test_session_true_but_has_expiration(self) -> None:
        # session=True 但显式带 expirationDate → 用 expirationDate，不强制 -1
        out = normalize_cookie(
            {"name": "sid", "session": True, "expirationDate": 1700000000}
        )
        assert out["expires"] == 1700000000.0

    def test_zero_expires_becomes_negative_one(self) -> None:
        out = normalize_cookie({"name": "sid", "expires": 0})
        assert out["expires"] == -1

    def test_bool_coercion(self) -> None:
        out = normalize_cookie({"name": "sid", "httpOnly": 1, "secure": "yes"})
        assert out["httpOnly"] is True
        assert out["secure"] is True


class TestNormalizeCookieList:
    def test_skips_non_dict_and_nameless(self) -> None:
        raw = [
            {"name": "a", "value": "1"},
            "not-a-dict",
            {"value": "no-name"},  # 无 name 被跳过
            {"name": "", "value": "empty-name"},  # 空 name 被跳过
            {"name": "b", "value": "2"},
        ]
        out = normalize_cookie_list(raw)
        assert [c["name"] for c in out] == ["a", "b"]

    def test_empty(self) -> None:
        assert normalize_cookie_list([]) == []
