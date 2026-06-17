"""代理信息数据类和 Provider 抽象基类"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ProxyInfo:
    """代理信息"""
    ip: str
    http_port: int
    sock_port: int
    city_name: str = ""
    order_endtime: str = ""
    # 用户名密码认证（fixed_auth 模式使用）
    username: str = ""
    password: str = ""

    @property
    def requires_auth(self) -> bool:
        """是否需要用户名密码认证"""
        return bool(self.username and self.password)


class ProxyProvider(ABC):
    """代理服务商抽象基类"""

    @abstractmethod
    async def get_proxy(self, pool_entry) -> ProxyInfo:
        """
        获取代理信息

        Args:
            pool_entry: IPPoolEntry 配置对象（来自 app.proxy.config）

        Returns:
            ProxyInfo: 代理信息

        Raises:
            Exception: 获取代理失败
        """
        pass
