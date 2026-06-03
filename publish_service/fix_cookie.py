#!/usr/bin/env python
"""Cookie normalization helpers for Playwright-compatible auth state."""

from __future__ import annotations

from typing import Any

_VALID_SAMESITE = {"Strict", "Lax", "None"}
_SAMESITE_ALIAS = {
    "no_restriction": "None",
    "unspecified": "Lax",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
    "": "Lax",
}


def _normalize_samesite(value: Any) -> str:
    if value is None:
        return "Lax"
    if isinstance(value, str):
        text = value.strip()
        if text in _VALID_SAMESITE:
            return text
        mapped = _SAMESITE_ALIAS.get(text.lower())
        if mapped:
            return mapped
    return "Lax"


def _normalize_expires(value: Any) -> float:
    if value is None:
        return -1
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1


def normalize_cookie(raw: dict) -> dict:
    fixed = {
        "name": raw.get("name", ""),
        "value": raw.get("value", ""),
        "domain": raw.get("domain", ""),
        "path": raw.get("path", "/"),
        "expires": _normalize_expires(raw.get("expires", raw.get("expirationDate"))),
        "httpOnly": bool(raw.get("httpOnly", False)),
        "secure": bool(raw.get("secure", False)),
        "sameSite": _normalize_samesite(raw.get("sameSite")),
    }

    if raw.get("session") is True and "expirationDate" not in raw:
        fixed["expires"] = -1

    if fixed["expires"] == 0:
        fixed["expires"] = -1

    return fixed


def normalize_cookie_list(raw_cookies: list) -> list:
    result = []
    for cookie in raw_cookies:
        if not isinstance(cookie, dict):
            continue
        if not cookie.get("name"):
            continue
        result.append(normalize_cookie(cookie))
    return result
