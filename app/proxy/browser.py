"""代理的 Playwright 集成封装

把 ProxyAssignmentManager 取到的 ProxyInfo 转成 Playwright 的 proxy 参数字典，
供登录/发文两条浏览器启动路径统一调用。代理注入在 Playwright 启动浏览器那一层
（launch_persistent_context(proxy=...)），browser-use 之后通过 CDP 连接即可。
"""
from __future__ import annotations

from app.proxy import proxy_logger as logger
from app.proxy.assignment import get_assignment_manager
from app.proxy.provider import ProxyInfo


def build_playwright_proxy(proxy_info: ProxyInfo, protocol: str = "http") -> dict | None:
    """把 ProxyInfo 转为 Playwright launch 的 proxy 参数字典。

    - HTTP 代理：Chromium 支持账密认证，带 username/password。
    - SOCKS5 代理：Chromium 不支持账密认证，仅传 server；若配了账密会失效，记 warning。

    Returns:
        dict | None: Playwright proxy 参数；proxy_info 为空时返回 None。
    """
    if not proxy_info:
        return None

    if protocol == "http":
        server = f"http://{proxy_info.ip}:{proxy_info.http_port}"
    else:
        server = f"socks5://{proxy_info.ip}:{proxy_info.sock_port}"

    proxy_dict: dict = {"server": server}

    if proxy_info.requires_auth:
        if protocol == "http":
            proxy_dict["username"] = proxy_info.username
            proxy_dict["password"] = proxy_info.password
        else:
            logger.warning(
                "[Proxy] SOCKS5 代理不支持用户名密码认证（Chromium 限制），"
                "将尝试无认证连接。如失败请改用 http 协议。"
            )

    return proxy_dict


async def get_channel_proxy(channel_id: str) -> tuple[dict | None, ProxyInfo | None]:
    """获取渠道绑定的代理，返回 (playwright_proxy_dict, proxy_info)。

    代理未启用 / 管理器未初始化 / channel_id 为空时返回 (None, None)，调用方据此直连。
    代理获取失败（如 juliangip API 异常）会向上抛出——严格模式不 fallback 直连。
    """
    if not channel_id:
        return None, None
    mgr = get_assignment_manager()
    if mgr is None or not mgr.is_enabled:
        return None, None

    proxy_info = await mgr.get_proxy_for_channel(channel_id)
    protocol = mgr.get_protocol_for(channel_id)
    proxy_dict = build_playwright_proxy(proxy_info, protocol)
    return proxy_dict, proxy_info
