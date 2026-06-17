"""渠道（channel）→ 代理 IP 绑定逻辑

参考实现：browser_use_demo4/app/proxy/assignment.py、deepseek_v3/proxy/assignment.py
关键改造：account_id → channel_id（服务签发的渠道句柄）。

绑定关系持久化到 data/proxy_assignments.json（与 ChannelStore 解耦，迁移成本最低）。
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.proxy import proxy_logger as logger
from app.proxy.config import ProxyConfig, load_proxy_config
from app.proxy.provider import ProxyInfo, ProxyProvider


class ProxyAssignmentManager:
    """代理分配管理器

    策略：最少绑定优先 + 永久绑定 + 无容量上限

    - 最少绑定优先: 新渠道永远分给当前绑定数最少的 IP，实现自动负载均衡
    - 永久绑定: 一旦分配，channel→IP 持久化到 assignments 文件，不再漂移
    - 无容量上限: 不限制单个 IP 的绑定数，由最少绑定优先保证均衡

    加新 IP: 只需在 proxies.yaml 的 ip_pool 中新增条目，新渠道自动流向新 IP
    """

    def __init__(
        self,
        config_path: str,
        assignments_path: str,
    ):
        self.config: ProxyConfig = load_proxy_config(config_path)
        self.assignments_path = Path(assignments_path)
        self.assignments: dict = self._load_assignments()
        # 协程锁：保护 assignments dict 的并发读写
        self._lock = asyncio.Lock()
        # Provider 实例缓存
        self._provider_cache: dict[str, ProxyProvider] = {}

        logger.info(
            f"[ProxyConfig] 加载 proxies.yaml 成功: "
            f"ip_pool={len(self.config.ip_pool)}, "
            f"protocol={self.config.defaults.protocol}, "
            f"verify_exit_ip={self.config.defaults.verify_exit_ip}"
        )
        logger.info(
            f"[ProxyAssignment] 加载 {self.assignments_path}: "
            f"已分配渠道数={len(self.assignments)}"
        )

    @property
    def is_enabled(self) -> bool:
        """管理器已就绪即视为启用（由 server 在 PROXY_ENABLED 时初始化）。"""
        return True

    async def get_proxy_for_channel(self, channel_id: str) -> ProxyInfo:
        """
        获取 channel_id 对应的代理信息

        Args:
            channel_id: 服务签发的渠道句柄（uuid4 hex）

        Returns:
            ProxyInfo: 代理信息

        Raises:
            Exception: provider 调用失败
        """
        async with self._lock:
            # 1. 检查是否已有分配
            if channel_id in self.assignments:
                ip_index = self.assignments[channel_id]["ip_index"]
                logger.info(
                    f"[Proxy] {channel_id} 查询代理分配: 已有映射 ip_index={ip_index}"
                )
            else:
                # 2. 为新渠道分配 IP
                ip_index = self._assign_ip_to_channel(channel_id)
                self.assignments[channel_id] = {
                    "ip_index": ip_index,
                    "assigned_at": datetime.now().isoformat(),
                }
                self._save_assignments()

                label = self.config.ip_pool[ip_index].label or f"IP-{ip_index}"
                usage = self._get_ip_usage_count(ip_index)
                logger.info(
                    f"[ProxyAssignment] 新渠道 {channel_id} 分配到 "
                    f"ip_index={ip_index} ({label}), "
                    f"当前负载={usage}"
                )

        # 3. 从 ip_pool 获取配置（锁外执行，避免持锁调 API）
        ip_config = self.config.ip_pool[ip_index]
        provider = self._get_provider(ip_config.provider)

        # 4. 调用 Provider 获取代理信息
        logger.info(
            f"[Proxy] 获取代理: provider={ip_config.provider}, "
            f"label={ip_config.label or f'IP-{ip_index}'}"
        )
        proxy_info = await provider.get_proxy(ip_config)

        logger.info(
            f"[Proxy] 获取代理成功: ip={proxy_info.ip}, "
            f"port={proxy_info.http_port}, "
            f"auth={'是' if proxy_info.requires_auth else '否'}, "
            f"city={proxy_info.city_name or '未知'}"
        )

        return proxy_info

    def get_protocol_for(self, channel_id: str) -> str:
        """获取渠道对应 IP 池条目的协议（ip_pool 条目 protocol 覆盖 defaults）"""
        if channel_id not in self.assignments:
            return self.config.defaults.protocol
        ip_index = self.assignments[channel_id]["ip_index"]
        ip_config = self.config.ip_pool[ip_index]
        return ip_config.protocol or self.config.defaults.protocol

    def get_ip_index_for(self, channel_id: str) -> Optional[int]:
        """获取渠道对应的 ip_pool 索引（未分配返回 None）"""
        if channel_id not in self.assignments:
            return None
        return self.assignments[channel_id]["ip_index"]

    def _assign_ip_to_channel(self, channel_id: str) -> int:
        """
        为新渠道分配 IP（最少绑定优先）

        遍历 ip_pool，找到当前绑定渠道数最少的 IP。
        平局时选索引最小的（保证可重现）。

        Returns:
            int: ip_index
        """
        ip_usage = self._get_all_ip_usage()

        # 找到绑定数最少的 ip_index
        min_count = None
        best_index = 0
        for idx in range(len(self.config.ip_pool)):
            count = ip_usage.get(idx, 0)
            if min_count is None or count < min_count:
                min_count = count
                best_index = idx

        return best_index

    def _get_all_ip_usage(self) -> dict:
        """统计所有 IP 的使用情况"""
        ip_usage = {}
        for _cid, info in self.assignments.items():
            idx = info["ip_index"]
            ip_usage[idx] = ip_usage.get(idx, 0) + 1
        return ip_usage

    def _get_ip_usage_count(self, ip_index: int) -> int:
        """获取指定 IP 的使用数量"""
        return sum(
            1 for info in self.assignments.values() if info["ip_index"] == ip_index
        )

    def _load_assignments(self) -> dict:
        """加载持久化的分配记录"""
        if self.assignments_path.exists():
            try:
                with open(self.assignments_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                logger.warning(
                    f"[ProxyAssignment] 加载分配记录失败, 降级为空: {exc}"
                )
                return {}
        return {}

    def _save_assignments(self):
        """保存分配记录"""
        try:
            self.assignments_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.assignments_path, "w", encoding="utf-8") as f:
                json.dump(self.assignments, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning(f"[ProxyAssignment] 保存分配记录失败: {exc}")

    def _get_provider(self, provider_name: str) -> ProxyProvider:
        """获取 Provider 实例（带缓存）"""
        if provider_name not in self._provider_cache:
            if provider_name == "juliangip":
                from app.proxy.providers.juliang import JuliangProvider
                self._provider_cache[provider_name] = JuliangProvider()
            elif provider_name == "fixed_auth":
                from app.proxy.providers.fixed_auth import FixedAuthProvider
                self._provider_cache[provider_name] = FixedAuthProvider()
            else:
                raise Exception(f"未知的 provider: {provider_name}")
        return self._provider_cache[provider_name]


# ──────────────────────────────────────────────────────────────
# 全局单例（由 server.py lifespan 在 PROXY_ENABLED=true 时初始化）
# ──────────────────────────────────────────────────────────────
_manager: Optional[ProxyAssignmentManager] = None


def get_assignment_manager() -> Optional[ProxyAssignmentManager]:
    """获取全局分配管理器单例（未启用/未初始化返回 None）"""
    return _manager


def init_assignment_manager(
    config_path: str,
    assignments_path: str,
) -> ProxyAssignmentManager:
    """初始化全局分配管理器（服务启动时调用一次）"""
    global _manager
    if _manager is None:
        _manager = ProxyAssignmentManager(config_path, assignments_path)
    return _manager


def reset_assignment_manager():
    """重置全局单例（用于测试）"""
    global _manager
    _manager = None
