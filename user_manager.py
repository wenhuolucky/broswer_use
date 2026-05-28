"""
用户管理器 - 管理多用户配置和会话隔离
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Optional

import yaml


class UserConfig:
    """单个用户的配置"""

    def __init__(self, user_id: str, name: str, auth_file: str, chrome_profile: str, cdp_port: int):
        self.user_id = user_id
        self.name = name
        self.auth_file = Path(auth_file)
        self.chrome_profile = Path(chrome_profile)
        self.cdp_port = cdp_port


class UserManager:
    """
    管理多用户配置和会话隔离

    - 加载 users.yaml 配置
    - 为每个用户提供独立的 asyncio.Lock（防止同用户并发发布）
    - 支持动态端口分配避免冲突
    """

    _instance: Optional["UserManager"] = None

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.users: dict[str, UserConfig] = {}
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._default_user_id: Optional[str] = None
        self._load_config()

    @classmethod
    def load_default(cls) -> "UserManager":
        """加载默认配置文件 users.yaml"""
        config_path = Path(__file__).parent / "users.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        return cls(config_path)

    @classmethod
    def get_instance(cls) -> "UserManager":
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls.load_default()
        return cls._instance

    def _load_config(self):
        """从 YAML 文件加载用户配置"""
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self._default_user_id = config.get("default_user_id")

        for user_data in config.get("users", []):
            user_id = user_data["user_id"]
            # 自动创建用户目录
            auth_file = Path(user_data["auth_file"])
            chrome_profile = Path(user_data["chrome_profile"])
            auth_file.parent.mkdir(parents=True, exist_ok=True)
            chrome_profile.mkdir(parents=True, exist_ok=True)

            self.users[user_id] = UserConfig(
                user_id=user_id,
                name=user_data.get("name", user_id),
                auth_file=auth_file,
                chrome_profile=chrome_profile,
                cdp_port=user_data["cdp_port"],
            )
            self._user_locks[user_id] = asyncio.Lock()

    def get_user(self, user_id: str) -> Optional[UserConfig]:
        """根据 user_id 获取用户配置"""
        return self.users.get(user_id)

    def get_default_user_id(self) -> Optional[str]:
        """获取默认用户 ID"""
        return self._default_user_id

    def get_user_ids(self) -> list[str]:
        """获取所有用户 ID 列表"""
        return list(self.users.keys())

    async def acquire_user_slot(self, user_id: str) -> asyncio.Lock:
        """
        获取用户的发布锁。如果用户正在发布，则等待。

        返回获得的 Lock，调用方需要在完成后 release。
        """
        if user_id not in self._user_locks:
            raise ValueError(f"未知用户: {user_id}")

        lock = self._user_locks[user_id]
        await lock.acquire()
        return lock

    def release_user_slot(self, user_id: str):
        """释放用户的发布锁"""
        lock = self._user_locks.get(user_id)
        if lock and lock.locked():
            lock.release()

    def is_user_publishing(self, user_id: str) -> bool:
        """检查用户是否正在发布"""
        lock = self._user_locks.get(user_id)
        return lock is not None and lock.locked()