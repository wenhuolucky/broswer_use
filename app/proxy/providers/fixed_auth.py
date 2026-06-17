"""固定代理服务器 Provider（静态共享，用户名密码认证）"""
from app.proxy.provider import ProxyInfo, ProxyProvider


class FixedAuthProvider(ProxyProvider):
    """固定代理服务器（用户名密码认证模式）

    适用于静态共享代理：IP/端口/账密一次性配置，长期使用不变。
    相比 juliangip 模式：无 API 调用、无速率限制、零运行时开销。
    """

    async def get_proxy(self, pool_entry) -> ProxyInfo:
        """
        从配置获取固定代理信息

        Args:
            pool_entry: IPPoolEntry 配置对象（需要 ip, port, username, password）

        Returns:
            ProxyInfo: 代理信息
        """
        return ProxyInfo(
            ip=pool_entry.ip,
            # 固定代理的 http_port 和 sock_port 是同一个端口
            http_port=pool_entry.port,
            sock_port=pool_entry.port,
            username=pool_entry.username,
            password=pool_entry.password,
            # 城市信息和到期时间不可用
            city_name="",
            order_endtime="",
        )
