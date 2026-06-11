from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import COOKIE_DIR


class CookieStore:
    """Cookie storage scoped to the publish service."""

    def __init__(self, base_dir: Path = COOKIE_DIR):
        self.base_dir = Path(base_dir)

    def path_for(self, platform: str, user_id: str) -> Path:
        return self.base_dir / platform / f"{user_id}.json"

    def save(self, platform: str, user_id: str, cookies: list[dict[str, Any]], origins: list | None = None) -> Path:
        path = self.path_for(platform, user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"cookies": cookies, "origins": origins or []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load(self, platform: str, user_id: str) -> list[dict[str, Any]]:
        path = self.path_for(platform, user_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        cookies = data.get("cookies", [])
        return cookies if isinstance(cookies, list) else []

    def load_storage_state_text(self, platform: str, user_id: str) -> str:
        return self.path_for(platform, user_id).read_text(encoding="utf-8")

    def delete(self, platform: str, user_id: str) -> bool:
        path = self.path_for(platform, user_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def has_valid_cookie(self, platform: str, user_id: str) -> bool:
        path = self.path_for(platform, user_id)
        if not path.exists():
            return False
        try:
            cookies = self.load(platform, user_id)
        except (OSError, json.JSONDecodeError):
            return False
        if not cookies:
            return False
        return any(self._cookie_matches_platform(platform, cookie) for cookie in cookies)

    def _cookie_matches_platform(self, platform: str, cookie: dict[str, Any]) -> bool:
        domain = str(cookie.get("domain", ""))
        if platform == "toutiao":
            if cookie.get("name") not in {
                "uid_tt",
                "uid_tt_ss",
                "sessionid",
                "sessionid_ss",
                "sid_tt",
                "sid_guard",
                "sid_ucp_v1",
                "ssid_ucp_v1",
            }:
                return False
            return any(auth_domain in domain or domain in auth_domain for auth_domain in (
                ".toutiao.com",
                "mp.toutiao.com",
                "www.toutiao.com",
            ))
        if platform == "sohu":
            return any(auth_domain in domain or domain in auth_domain for auth_domain in (
                ".sohu.com",
                "mp.sohu.com",
                "www.sohu.com",
            ))
        return bool(domain)
