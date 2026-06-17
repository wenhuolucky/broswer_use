"""巨量 IP 独享代理 Provider（通过 API 动态获取 IP）"""
import hashlib

import httpx

from app.proxy.provider import ProxyInfo, ProxyProvider
from app.proxy import proxy_logger as logger


class JuliangProvider(ProxyProvider):
    """巨量 IP 独享代理（独享 IP 模式，订单期内 IP 不变）"""

    API_URL = "http://v2.api.juliangip.com/alone/getips"

    async def get_proxy(self, pool_entry) -> ProxyInfo:
        """
        从巨量 IP API 获取代理信息

        Args:
            pool_entry: IPPoolEntry 配置对象（需要 trade_no, api_key）

        Returns:
            ProxyInfo: 代理信息

        Raises:
            Exception: API 调用失败 / 返回错误码
        """
        trade_no = pool_entry.trade_no
        api_key = pool_entry.api_key

        # 签名: sign = MD5(trade_no + api_key)
        sign = hashlib.md5(f"{trade_no}{api_key}".encode()).hexdigest()

        params = {
            "trade_no": trade_no,
            "sign": sign,
            "sock_port": 1,
            "city_name": 1,
            "order_endtime": 1,
            "result_type": "json",
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(self.API_URL, params=params, timeout=10)
                data = resp.json()
        except Exception as exc:
            logger.error(f"[JuliangProvider] API 调用异常: {exc}")
            raise

        if data.get("code") != 200:
            raise Exception(
                f"巨量 IP API 错误 (code={data.get('code')}): {data.get('msg')}"
            )

        proxy_data = data["data"]
        return ProxyInfo(
            ip=proxy_data["proxy_ip"],
            http_port=proxy_data["http_port"],
            sock_port=proxy_data["sock_port"],
            city_name=proxy_data.get("city_name", "") or "",
            order_endtime=proxy_data.get("order_endtime", "") or "",
        )
