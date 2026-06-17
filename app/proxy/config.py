"""代理配置加载"""
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class IPPoolEntry:
    """
    IP 池条目

    支持两种 provider 类型：
    - juliangip: 巨量 IP 独享代理，通过 API 动态获取 IP（需 trade_no + api_key）
    - fixed_auth: 固定代理服务器，用户名密码认证（需 ip + port + username + password）
    """
    provider: str
    label: str = ""

    # juliangip 模式
    trade_no: str = ""
    api_key: str = ""

    # fixed_auth 模式
    ip: str = ""
    port: int = 0
    username: str = ""
    password: str = ""
    protocol: str = ""  # 可覆盖 defaults 的 protocol

    def validate(self):
        """验证必填字段"""
        if self.provider == "juliangip":
            if not self.trade_no or not self.api_key:
                raise ValueError(
                    f"juliangip provider 必须提供 trade_no 和 api_key (label={self.label})"
                )
        elif self.provider == "fixed_auth":
            if not self.ip or not self.port or not self.username or not self.password:
                raise ValueError(
                    f"fixed_auth provider 必须提供 ip, port, username, password (label={self.label})"
                )
        else:
            raise ValueError(f"未知的 provider: {self.provider} (label={self.label})")


@dataclass
class ProxyDefaults:
    """默认配置"""
    protocol: str = "http"
    verify_exit_ip: bool = True
    exit_ip_check_url: str = "https://api.ip.sb/ip"
    cache_ttl_seconds: int = 300


@dataclass
class ProxyConfig:
    """代理配置（最少绑定优先，无容量上限）"""
    ip_pool: list[IPPoolEntry]
    defaults: ProxyDefaults = field(default_factory=ProxyDefaults)


def load_proxy_config(config_path: str) -> ProxyConfig:
    """
    加载代理配置

    Args:
        config_path: 配置文件路径

    Returns:
        ProxyConfig: 配置对象

    Raises:
        FileNotFoundError: 配置文件不存在
        Exception: YAML 解析失败
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"代理配置文件不存在: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data:
        raise ValueError(f"代理配置文件为空: {config_path}")

    # 解析 ip_pool
    ip_pool = []
    for entry in data.get("ip_pool", []):
        pool_entry = IPPoolEntry(
            provider=entry["provider"],
            label=entry.get("label", ""),
            # juliangip 模式
            trade_no=entry.get("trade_no", ""),
            api_key=entry.get("api_key", ""),
            # fixed_auth 模式
            ip=entry.get("ip", ""),
            port=int(entry.get("port", 0)),
            username=entry.get("username", ""),
            password=entry.get("password", ""),
            protocol=entry.get("protocol", ""),
        )
        pool_entry.validate()
        ip_pool.append(pool_entry)

    if not ip_pool:
        raise ValueError(f"代理配置文件中 ip_pool 为空: {config_path}")

    # 解析 defaults
    defaults_data = data.get("defaults", {}) or {}
    defaults = ProxyDefaults(
        protocol=defaults_data.get("protocol", "http"),
        verify_exit_ip=defaults_data.get("verify_exit_ip", True),
        exit_ip_check_url=defaults_data.get(
            "exit_ip_check_url", "https://api.ip.sb/ip"
        ),
        cache_ttl_seconds=defaults_data.get("cache_ttl_seconds", 300),
    )

    return ProxyConfig(
        ip_pool=ip_pool,
        defaults=defaults,
    )
