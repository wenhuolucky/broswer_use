"""
平台配置 - 云浏览器 Cookie 采集专用
复用 platforms/base.py 的 PlatformConfig 验证逻辑
"""
from pathlib import Path

from src.platforms.toutiao import ToutiaoPlatform
from src.platforms.sohu import SohuPlatform

# 平台配置映射（使用类属性手动构造）
PLATFORMS = {
    "toutiao": ToutiaoPlatform(
        name="toutiao",
        home_url="https://mp.toutiao.com",
        publish_url="https://mp.toutiao.com/profile_v4/graphic/publish",
        auth_domains=[".toutiao.com", "mp.toutiao.com", "www.toutiao.com"],
        users_yaml="users.yaml",
    ),
    "sohu": SohuPlatform(
        name="sohu",
        home_url="https://mp.sohu.com",
        publish_url="https://mp.sohu.com",
        auth_domains=[".sohu.com", "mp.sohu.com", "www.sohu.com"],
        users_yaml="sohu_users.yaml",
    ),
}


def get_platform(name: str):
    """获取平台配置"""
    if name not in PLATFORMS:
        raise ValueError(f"未知平台: {name}，支持: {list(PLATFORMS.keys())}")
    return PLATFORMS[name]


def get_default_auth_file(platform_name: str) -> Path:
    """获取平台默认的 cloud auth 文件路径"""
    return Path(__file__).parent / f"{platform_name}_cloud_auth.json"
