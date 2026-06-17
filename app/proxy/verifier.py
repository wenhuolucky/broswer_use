"""出口 IP 验证

启动浏览器后打开一个 IP 查询服务，对比实际出口 IP 与代理配置是否一致。
失败即抛异常，由调用方决定任务走向（严格模式下任务失败，不 fallback 直连）。
"""
import json
import re

from app.proxy import proxy_logger as logger
from app.proxy.provider import ProxyInfo


class ExitIPVerifier:
    """出口 IP 验证器"""

    async def verify(
        self,
        context,
        proxy_info: ProxyInfo,
        check_url: str = "https://api.ip.sb/ip",
    ):
        """
        验证实际出口 IP 是否与代理配置一致

        Args:
            context: Playwright 的 browser context
            proxy_info: 代理信息
            check_url: 用于查询出口 IP 的 URL

        Raises:
            Exception: 验证失败 / IP 不匹配 / 网络异常
        """
        page = None
        try:
            page = await context.new_page()
            await page.goto(check_url, timeout=15000)

            result = await page.evaluate("() => document.body.innerText")
            actual_ip = self._extract_ip_from_response(result)

            if not actual_ip:
                raise Exception(
                    f"出口 IP 验证失败: 无法从响应中提取 IP - {result[:200]}"
                )

            if actual_ip != proxy_info.ip:
                raise Exception(
                    f"出口 IP 不匹配: 期望={proxy_info.ip}, 实际={actual_ip}"
                )

            logger.info(
                f"[Proxy] 出口 IP 验证通过: 实际出口={actual_ip}, "
                f"期望={proxy_info.ip} ✅"
            )

        except Exception as exc:
            error_msg = str(exc)
            if "出口 IP" in error_msg:
                logger.error(f"[Proxy] {error_msg}")
                raise
            logger.error(f"[Proxy] 出口 IP 验证失败: {exc}")
            raise Exception(f"出口 IP 验证失败: {exc}")
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    @staticmethod
    def _extract_ip_from_response(response: str) -> str:
        """
        从响应中提取 IP 地址

        支持格式：
        - 纯文本 IP：`222.212.94.89`
        - JSON 格式：`{"ip": "xxx", ...}`
        - 其他包含 IP 的文本

        Returns:
            str: 提取的 IP 地址，失败返回空字符串
        """
        if not response:
            return ""

        response = response.strip()

        # 1. JSON 格式：{"ip": "xxx"}
        try:
            data = json.loads(response)
            if isinstance(data, dict):
                ip = data.get("ip") or data.get("cip")
                if ip and re.match(
                    r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip
                ):
                    return ip
        except (json.JSONDecodeError, TypeError):
            pass

        # 2. 直接匹配 IP 地址
        ip_match = re.search(
            r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", response
        )
        if ip_match:
            ip = ip_match.group(1)
            # 排除私有 IP
            parts = ip.split(".")
            if (
                parts[0] != "127"
                and parts[0] != "10"
                and not (parts[0] == "192" and parts[1] == "168")
            ):
                return ip

        return ""
