"""代理模块

为渠道（channel）提供 → 静态代理 IP 的永久绑定能力，避免所有渠道共用本机 IP
触发平台风控。每个 channel 的登录和发文都通过同一个独立 IP 出口。

架构：
- config.py: 加载 proxies.yaml
- provider.py: ProxyInfo 数据类 + ProxyProvider 抽象基类
- providers/: 具体 provider 实现（fixed_auth, juliangip）
- assignment.py: channel → IP 分配逻辑 + 持久化
- verifier.py: 出口 IP 一致性验证
- browser.py: Playwright 代理参数封装 + 按渠道获取代理

迁移自 deepseek_v3/proxy（参考实现 browser_use_demo4/app/proxy），核心改造：
account_id → channel_id 语义。
"""
import logging

proxy_logger = logging.getLogger("app.proxy")
