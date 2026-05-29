"""
Cookie 持久化 - 保存/加载/验证
格式与 publisher.py 的 auth.json 完全一致: {"cookies": [...], "origins": [...]}
"""
import json
from pathlib import Path

from platforms.base import PlatformConfig


class CookieStore:
    """Cookie 持久化和验证"""

    @staticmethod
    def save(cookies: list, path: Path, origins: list = None) -> None:
        """保存 cookies 为 Playwright storage_state 格式"""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "cookies": cookies,
            "origins": origins or [],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def load(path: Path) -> dict:
        """加载 auth 文件，返回 {"cookies": [...], "origins": [...]}"""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def validate(cookies: list, platform: PlatformConfig) -> bool:
        """检查 cookies 是否包含目标平台的登录态"""
        if not cookies:
            return False
        return platform.validate_cookies(cookies)
