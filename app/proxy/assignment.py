"""渠道（channel）→ 代理 IP 绑定逻辑

参考实现：browser_use_demo4/app/proxy/assignment.py、deepseek_v3/proxy/assignment.py
关键改造：account_id → channel_id（服务签发的渠道句柄）。

绑定关系持久化到 data/proxy_assignments.json（与 ChannelStore 解耦，迁移成本最低）。

健康检测机制：
- IP 健康状态追踪（连续失败计数）
- 失败自动 failover（切换到健康 IP）
- 定期健康检查（主动发现失效 IP）
"""
import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.proxy import proxy_logger as logger
from app.proxy.config import ProxyConfig, load_proxy_config
from app.proxy.provider import ProxyInfo, ProxyProvider


@dataclass
class IPHealthState:
    """IP 健康状态追踪

    记录每个 IP 的失败次数，连续失败达到阈值后标记为不健康，
    触发自动 failover 切换到其他健康 IP。
    """
    ip_index: int
    failed_count: int = 0
    last_failure: float = 0.0
    is_healthy: bool = True
    failure_reason: str = ""

    def record_failure(self, reason: str):
        """记录一次失败"""
        self.failed_count += 1
        self.last_failure = time.time()
        self.failure_reason = reason
        # 连续失败 3 次标记为不健康
        if self.failed_count >= 3:
            self.is_healthy = False

    def reset(self):
        """重置健康状态（成功时调用）"""
        self.failed_count = 0
        self.failure_reason = ""
        self.is_healthy = True


class ProxyAssignmentManager:
    """代理分配管理器

    策略：最少绑定优先 + 永久绑定 + 健康检测自动 failover

    - 最少绑定优先：新渠道永远分给当前绑定数最少的 IP，实现自动负载均衡
    - 永久绑定：一旦分配，channel→IP 持久化到 assignments 文件，不再漂移
    - 健康检测：IP 连续失败 3 次标记为不健康，自动切换到健康 IP
    - 无容量上限：不限制单个 IP 的绑定数，由最少绑定优先保证均衡

    加新 IP: 只需在 proxies.yaml 的 ip_pool 中新增条目，新渠道自动流向新 IP
    """

    # 默认失败阈值：连续失败 3 次标记为不健康
    DEFAULT_FAILURE_THRESHOLD = 3

    def __init__(
        self,
        config_path: str,
        assignments_path: str,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    ):
        self.config: ProxyConfig = load_proxy_config(config_path)
        self.assignments_path = Path(assignments_path)
        self.assignments: dict = self._load_assignments()
        # 协程锁：保护 assignments dict 的并发读写
        self._lock = asyncio.Lock()
        # Provider 实例缓存
        self._provider_cache: dict[str, ProxyProvider] = {}
        # IP 健康状态追踪
        self._health_states: dict[int, IPHealthState] = {}
        # 失败阈值
        self._failure_threshold = failure_threshold

        logger.info(
            f"[ProxyConfig] 加载 proxies.yaml 成功: "
            f"ip_pool={len(self.config.ip_pool)}, "
            f"protocol={self.config.defaults.protocol}, "
            f"verify_exit_ip={self.config.defaults.verify_exit_ip}"
        )
        logger.info(
            f"[ProxyAssignment] 加载 {self.assignments_path}: "
            f"已分配渠道数={len(self.assignments)}, "
            f"failure_threshold={failure_threshold}"
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
                    f"[Proxy] {channel_id} 查询代理分配：已有映射 ip_index={ip_index}"
                )

                # 2. 检查 IP 健康状态
                health = self._health_states.get(ip_index)
                if health and not health.is_healthy:
                    logger.warning(
                        f"[Proxy] IP {ip_index} 不健康，重新分配 channel {channel_id}"
                    )
                    self._unassign_channel(channel_id)
                    # 继续到下面的重新分配逻辑
                else:
                    # 3. 从 ip_pool 获取配置（锁外执行，避免持锁调 API）
                    ip_config = self.config.ip_pool[ip_index]
                    provider = self._get_provider(ip_config.provider)

                    try:
                        # 4. 调用 Provider 获取代理信息
                        logger.info(
                            f"[Proxy] 获取代理：provider={ip_config.provider}, "
                            f"label={ip_config.label or f'IP-{ip_index}'}"
                        )
                        proxy_info = await provider.get_proxy(ip_config)

                        # 成功：重置健康状态
                        if health:
                            health.reset()

                        logger.info(
                            f"[Proxy] 获取代理成功：ip={proxy_info.ip}, "
                            f"port={proxy_info.http_port}, "
                            f"auth={'是' if proxy_info.requires_auth else '否'}, "
                            f"city={proxy_info.city_name or '未知'}"
                        )

                        return proxy_info
                    except Exception as e:
                        # 失败：记录并标记
                        if not health:
                            self._health_states[ip_index] = IPHealthState(ip_index=ip_index)
                        self._health_states[ip_index].record_failure(str(e))
                        logger.warning(
                            f"[Proxy] IP {ip_index} 获取失败：{e}, "
                            f"failed_count={self._health_states[ip_index].failed_count}"
                        )

                        # 如果超过阈值，解除绑定并重新分配
                        if not self._health_states[ip_index].is_healthy:
                            self._unassign_channel(channel_id)
                        else:
                            raise  # 未超过阈值，向上抛异常
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

        # 4. 调用 Provider 获取代理信息（锁外执行）
        proxy_info = await provider.get_proxy(ip_config)

        logger.info(
            f"[Proxy] 获取代理成功：ip={proxy_info.ip}, "
            f"port={proxy_info.http_port}, "
            f"auth={'是' if proxy_info.requires_auth else '否'}, "
            f"city={proxy_info.city_name or '未知'}"
        )

        return proxy_info

    async def _assign_and_get_proxy(self, channel_id: str) -> ProxyInfo:
        """分配健康的 IP 并返回代理"""
        async with self._lock:
            # 最少绑定优先，但只考虑健康的 IP
            all_ip_usage = self._get_all_ip_usage()
            available_ips = [
                (ip_index, all_ip_usage.get(ip_index, 0))
                for ip_index in range(len(self.config.ip_pool))
                if self._health_states.get(
                    ip_index, IPHealthState(ip_index=ip_index)
                ).is_healthy
            ]

            if not available_ips:
                logger.error("[Proxy] 没有健康的 IP 可用")
                raise Exception("没有健康的 IP 可用")

            # 选择绑定数最少的健康 IP
            ip_index = min(available_ips, key=lambda x: x[1])[0]

            # 绑定并返回
            self.assignments[channel_id] = {
                "ip_index": ip_index,
                "assigned_at": datetime.now().isoformat(),
            }
            self._save_assignments()

            label = self.config.ip_pool[ip_index].label or f"IP-{ip_index}"
            logger.info(
                f"[ProxyAssignment] 重新分配 {channel_id} 到 "
                f"ip_index={ip_index} ({label})"
            )

        # 从 ip_pool 获取配置（锁外执行）
        ip_config = self.config.ip_pool[ip_index]
        provider = self._get_provider(ip_config.provider)

        # 调用 Provider 获取代理信息
        proxy_info = await provider.get_proxy(ip_config)
        return proxy_info

    def _unassign_channel(self, channel_id: str):
        """解除 channel 的 IP 绑定"""
        if channel_id in self.assignments:
            old_ip_index = self.assignments[channel_id]["ip_index"]
            del self.assignments[channel_id]
            self._save_assignments()
            logger.info(
                f"[Proxy] 解除 {channel_id} 的 IP 绑定 (原 ip_index={old_ip_index})"
            )
            return True
        return False

    def unassign_channel(self, channel_id: str) -> bool:
        """解除 channel 的 IP 绑定，供账号删除级联清理调用。"""
        return self._unassign_channel(channel_id)

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
                    f"[ProxyAssignment] 加载分配记录失败，降级为空：{exc}"
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
            logger.warning(f"[ProxyAssignment] 保存分配记录失败：{exc}")

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

    async def start_health_check_loop(self, interval_seconds: int = 300):
        """定期健康检查循环

        Args:
            interval_seconds: 检查间隔（秒），默认 300 秒（5 分钟）
        """
        logger.info(
            f"[Proxy] 启动 IP 健康检查循环，间隔={interval_seconds}秒"
        )
        while True:
            await asyncio.sleep(interval_seconds)
            await self._check_all_ips_health()

    async def _check_all_ips_health(self):
        """检查所有 IP 的健康状态"""
        for ip_index in range(len(self.config.ip_pool)):
            ip_config = self.config.ip_pool[ip_index]
            provider = self._get_provider(ip_config.provider)

            try:
                await provider.get_proxy(ip_config)
                # 成功：重置健康状态
                if ip_index in self._health_states:
                    self._health_states[ip_index].reset()
                logger.debug(f"[Proxy] IP {ip_index} 健康检查通过")
            except Exception as e:
                # 失败：记录
                if ip_index not in self._health_states:
                    self._health_states[ip_index] = IPHealthState(ip_index=ip_index)
                self._health_states[ip_index].record_failure(str(e))
                logger.warning(
                    f"[Proxy] IP {ip_index} 健康检查失败：{e}, "
                    f"failed_count={self._health_states[ip_index].failed_count}"
                )


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
    failure_threshold: int = ProxyAssignmentManager.DEFAULT_FAILURE_THRESHOLD,
) -> ProxyAssignmentManager:
    """初始化全局分配管理器（服务启动时调用一次）"""
    global _manager
    if _manager is None:
        _manager = ProxyAssignmentManager(
            config_path, assignments_path, failure_threshold
        )
    return _manager


def reset_assignment_manager():
    """重置全局单例（用于测试）"""
    global _manager
    _manager = None
