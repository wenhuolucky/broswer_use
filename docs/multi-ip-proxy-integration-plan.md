# 多 IP 代理集成实施计划

> **目标**：将 `deepseek_v3/proxy` 模块迁移到当前项目，实现「渠道 → 静态代理 IP 永久绑定」，让每个渠道的**登录和发文都通过同一个独立 IP 出口**，避免平台风控。
>
> **来源模块**：`C:\program001\llm-crawler\deepseek_v3\proxy\`（约 690 行）
> **参考实现**：`C:\program001\browser_use_demo4\browser-use\`（重构前的旧项目，已落地多 IP）
>
> **编写日期**：2026-06-17
> **修订**：2026-06-17（修正浏览器代理注入机制的理解；补充预热池冲突约束）
>
> **实施状态**：✅ 已完成（2026-06-17）。代码已落地，94 项单测全通过（含 8 项新增代理测试）。下方「实施步骤」保留为设计记录；实际偏差见 3.1 节（代理改为浏览器启动时惰性分配，而非 ChannelStore.create()）。

---

## 〇、核心机制澄清（关键认知）

代理注入**不在 browser-use 层**，而在 **Playwright 启动浏览器那一层**。两条路径统一为同一模式：

```
Step 1: Playwright launch_persistent_context(proxy={...})   ← 代理在这里注入，原生支持账密
            ↓ 浏览器以 --remote-debugging-port=<port> 暴露 CDP
Step 2: BrowserSession(cdp_url=...) + await session.connect()  ← browser-use 只是连上去做自动化
```

**为什么必须这样**：browser-use 0.13 的 `BrowserSession(proxy=ProxySettings(...))` 会把代理转成 Chrome CLI 参数 `--proxy-server`，**但丢弃 username/password**，需要认证的代理（我们的 fixed_auth 全部需要）会失败。而 Playwright 原生 `proxy={"server","username","password"}` 完整支持账密认证。

**发文路径** (`kernel.py`) 当前**已经是这个模式**（Playwright launch → BrowserSession CDP 连接），所以只需加一个 `proxy=` 参数。

**登录路径** (`login.py`) 当前是 `BrowserSession(user_data_dir=...)` + `session.start()`，让 browser-use 自己启动浏览器——**无法注入带认证的代理**。需改造为与发文路径一致的「Playwright 启动 + CDP 连接」模式。旧项目 demo4 的登录路径正是用 `subprocess.Popen` 启动 Chrome + `connect_over_cdp`，本质相同。

---

## 一、现状分析

### 1.1 当前项目两条浏览器启动路径

| 路径 | 文件 | 函数 | 当前 API | 代理可注入性 |
|------|------|------|---------|------------|
| 远程登录 | `app/remote/login.py` | `_build_login_browser()` (L86) + `_open_login_browser()` (L181) | browser-use `BrowserSession(user_data_dir=)` + `start()` | ❌ 需改造 |
| 发布执行 | `app/publishing/kernel.py` | `_launch_browser()` (L480) | Playwright `launch_persistent_context()` + `BrowserSession(cdp_url=)` (L214-224) | ✅ 直接加参数 |

### 1.2 待迁移模块结构

```
deepseek_v3/proxy/
├── __init__.py          (14 行)  日志初始化
├── config.py            (123 行) YAML 配置加载，IPPoolEntry / ProxyDefaults / ProxyConfig
├── provider.py          (41 行)  ProxyInfo 数据类 + ProxyProvider 抽象基类
├── assignment.py        (225 行) ProxyAssignmentManager，账号→IP 永久绑定 + 负载均衡
├── verifier.py          (115 行) ExitIPVerifier，出口 IP 校验
└── providers/
    ├── __init__.py      (0 行)
    ├── juliang.py       (63 行)  巨量IP API Provider（MD5签名 + httpx）
    └── fixed_auth.py    (32 行)  固定代理 Provider（零 API 开销）
```

外部依赖：`pyyaml`、`httpx` —— 当前项目均已具备，无需新增。

### 1.3 关键差异对照

| 维度 | deepseek_v3 | 当前项目 | 适配策略 |
|------|-------------|---------|---------|
| 账号标识 | email 字符串 | `channel_id` (UUID hex) | 用 `channel_id` 作为绑定 key |
| 绑定关系存储 | 独立 JSON 文件 | 已有 SQLite（ChannelStore） | 沿用旧项目方案：独立 `data/proxy_assignments.json`（与 ChannelStore 解耦，迁移成本最低） |
| 全局单例 | `_manager` 全局变量 | FastAPI `agent` 单例 | 挂到 `PublishAgent`，沿用旧项目 `get_assignment_manager()` 亦可 |
| 日志系统 | `core.logger.log_manager` | `app.core.request_logging` | 改用 `logging.getLogger("app.proxy")` |

---

## 二、目标架构

```
app/
├── proxy/                          # 新增：代理模块（从 deepseek_v3 搬运 + 适配）
│   ├── __init__.py                 # 模块 logger
│   ├── config.py                   # proxies.yaml 加载
│   ├── provider.py                 # ProxyInfo + ProxyProvider ABC
│   ├── assignment.py               # ProxyAssignmentManager（channel_id 语义）
│   ├── verifier.py                 # ExitIPVerifier
│   ├── browser.py                  # 新增：build_playwright_proxy() + 代理获取封装
│   └── providers/
│       ├── __init__.py
│       ├── juliang.py              # 巨量IP API
│       └── fixed_auth.py           # 固定代理
├── core/config.py                  # 新增 PROXY_* 环境变量
├── channels/store.py               # create() 时触发代理分配
├── remote/login.py                 # 改造为 Playwright 启动 + CDP 连接，注入代理
├── publishing/kernel.py            # _launch_browser() 注入代理
├── publishing/orchestrator.py      # 传递 channel_id 到 kernel
└── publishing/adapter.py           # publish() 透传 channel_id
proxies.yaml                        # 新增：代理配置（gitignore）
proxies.yaml.example                # 配置模板
data/proxy_assignments.json         # 绑定关系持久化（运行期生成，gitignore）
```

---

## 三、关键设计决策

### 3.1 代理绑定时机：浏览器启动时惰性分配（实现修正）

> **修正**：原计划在 `ChannelStore.create()` 分配。但 `create()` 是同步方法，而 `get_proxy_for_channel` 是 async（含 `asyncio.Lock` + 可能的 juliangip API 调用），强行 async 化会波及所有 `create()` 调用方。改为**惰性分配**：

代理在浏览器启动前由 `get_channel_proxy(channel_id)` 按需分配——首次调用即分配 IP 并持久化到 `data/proxy_assignments.json`，后续同 channel_id 返回同一 IP。

```
登录: start_login_only() → create(platform) 得 channel_id
        → _build_session → get_channel_proxy(channel_id)  ← 首次分配 + 持久化
发文: _publish_with_cookie → get_channel_proxy(channel_id)  ← 命中已有映射，同一 IP
```

`channel_id` 是绑定 key：登录用 pending 渠道的 channel_id 分配，发文复用 bound 渠道的同一 channel_id（`bind` 不换 id，除非 native_key 去重合并到既有渠道——见下），故登录/发文出口 IP 天然一致。

**re-login 去重边界**：若账号二次登录，`bind()` 会把新 pending 渠道合并到既有 canonical 渠道（`native_key` 命中）。此时本次登录会话用的是 pending 渠道的 IP，而后续发文用 canonical 渠道的 IP，二者可能不同。属低频边界，本期接受（仅影响那一次手动登录会话的出口，发文始终用 canonical 渠道的稳定 IP）。

### 3.2 ⚠️ 预热池冲突（必须处理）

`RemoteLoginRunner` 有**预热池**：`_replenish()` 用 `channel_id=""` **提前启动浏览器**（login.py L678），用户请求来了再 `_claim_warm()` 把真实 channel_id 贴上去（L317）。

**矛盾**：预热时浏览器已启动，但此时还不知道是哪个 channel、该用哪个代理 IP。代理无法事后注入到已运行的浏览器。

**解决方案（二选一）**：

| 方案 | 做法 | 优点 | 缺点 |
|------|------|------|------|
| **A（推荐，先做）** | `PROXY_ENABLED=true` 时**禁用预热池**（`warm_target=0`），登录会话全部按需 `_build_session`，启动时已知 channel_id | 实现简单、逻辑清晰、IP 绑定语义干净 | 用户等待浏览器冷启动（数秒） |
| **B（后续优化）** | 预热会话创建时就**预分配一个代理 IP**（轮询 ip_pool）；`_claim_warm` 时把 channel 绑定到该会话已有的 IP | 保留预热加速 | 翻转了分配语义（变成 IP 决定 channel，而非 channel 决定 IP），「最少绑定优先」失效 |

**决策**：第一阶段用方案 A。`PROXY_ENABLED=false` 时预热池行为完全不变。

### 3.3 DISPLAY 注入方式

登录浏览器要渲染到指定 Xvnc 显示。改用 Playwright 启动后，两种方式：
- **保留现有模式**：`_open_login_browser` 的 `_launch_lock` 临界区临时设 `os.environ["DISPLAY"]`（最小改动）
- **更优**：Playwright `launch_persistent_context(env={"DISPLAY": display, ...})` 直接传环境变量给浏览器进程（Playwright 原生支持，无需全局锁）

**决策**：改用 Playwright 的 `env=` 参数，移除 DISPLAY 全局锁逻辑（顺带简化并发）。

**为什么现有代码要用全局锁**：browser-use 0.12/0.13 接收 `env` 参数但不真正传给浏览器子进程（启动浏览器的 `create_subprocess_exec` 未带 `env=`）。现有代码只能 hack：在 `start()` fork 那一瞬把**整个 Python 进程**的 `os.environ["DISPLAY"]` 临时改成本会话显示号，让 fork 出的 Chromium 继承，随即还原。`os.environ` 是进程全局状态，并发会话会互相覆盖（A 设 `:100`、B 设 `:101` 覆盖了 A），所以必须用 `_launch_lock` 把「设 DISPLAY → start() → 还原」串行化——代价是登录会话不能并发启动。

**Playwright 为何不需要锁**：`launch_persistent_context(env={...})` 显式把环境变量交给那个特定浏览器子进程，不经过 `os.environ` 全局状态，各会话各传各的 DISPLAY，天然隔离，可并发启动。

| | 当前（browser-use 自启动） | 改造后（Playwright 启动） |
|---|---|---|
| DISPLAY 传递 | 改全局 `os.environ` 再还原（hack） | `launch_persistent_context(env=)` 直接传给进程 |
| 并发安全 | 需 `_launch_lock` 串行化 fork | 天然隔离，无需锁 |
| 副作用 | 登录会话启动排队 | 可并发启动 |

### 3.4 严格模式

沿用 deepseek_v3 / demo4 的策略：

| 场景 | 行为 |
|------|------|
| `proxies.yaml` 缺失/格式错误（`PROXY_ENABLED=true`） | 阻止服务启动 |
| 代理获取失败 | 任务失败，**不 fallback 直连**（保护真实 IP） |
| 出口 IP 验证失败 | 记录 warning，不阻断（调试期；可配置严格） |
| `PROXY_ENABLED=false` | 全部直连，与现有行为一致 |

---

## 四、实施步骤

### 步骤 1：搬运 proxy 模块

新增 8 个文件到 `app/proxy/`，import 路径改写：

| 原 import | 改为 |
|----------|------|
| `from deepseek_v3.proxy import proxy_logger` | `from app.proxy import proxy_logger` |
| `from deepseek_v3.proxy.config import ...` | `from app.proxy.config import ...` |
| `from core.logger import log_manager` | `import logging; proxy_logger = logging.getLogger("app.proxy")` |

`assignment.py` 适配：`account_id` → `channel_id` 语义重命名（`get_proxy_for_account` → `get_proxy_for_channel` 等）。

### 步骤 2：配置 & 环境变量

**`proxies.yaml.example`**（提交）+ **`proxies.yaml`**（gitignore，真实代理）：

```yaml
defaults:
  protocol: http                    # HTTP 优先（Chromium 账密认证支持好）
  verify_exit_ip: true
  exit_ip_check_url: "https://api.ip.sb/ip"
  cache_ttl_seconds: 300

ip_pool:
  - provider: fixed_auth
    ip: "YOUR_PROXY_IP"
    port: 2018
    username: "YOUR_USERNAME"
    password: "YOUR_PASSWORD"
    protocol: http
    label: "静态代理-1"
  # - provider: juliangip
  #   trade_no: "YOUR_TRADE_NO"
  #   api_key: "YOUR_API_KEY"
  #   label: "独享IP-1"
```

**`app/core/config.py`** 新增：

```python
PROXY_ENABLED = os.getenv("PROXY_ENABLED", "false").lower() in ("true", "1", "yes")
PROXY_CONFIG_PATH = _project_path(os.getenv("PROXY_CONFIG_PATH"), PROJECT_ROOT / "proxies.yaml")
PROXY_ASSIGNMENTS_PATH = _project_path(os.getenv("PROXY_ASSIGNMENTS_PATH"), DATA_DIR / "proxy_assignments.json")
```

**`.gitignore`** 追加：`proxies.yaml`、`data/proxy_assignments.json`
**`.env.example`** 补充 `PROXY_*` 说明。

### 步骤 3：代理获取封装 `app/proxy/browser.py`

```python
async def get_channel_proxy(channel_id: str):
    """返回 (proxy_dict_for_playwright, proxy_info)；未启用/未绑定返回 (None, None)"""
    mgr = get_proxy_manager()
    if not mgr or not mgr.is_enabled:
        return None, None
    proxy_info = await mgr.get_proxy_for_channel(channel_id)
    protocol = mgr.get_protocol_for(channel_id)
    proxy_dict = {"server": f"{protocol}://{proxy_info.ip}:{proxy_info.http_port}"}
    if proxy_info.requires_auth and protocol == "http":
        proxy_dict["username"] = proxy_info.username
        proxy_dict["password"] = proxy_info.password
    return proxy_dict, proxy_info
```

### 步骤 4：渠道创建时分配代理

`app/channels/store.py` 的 `create()`：分配 IP 并把 `proxy_ip_index` / `proxy_label` 写入 `channel.metadata`（复用现有 metadata JSON 列，不改 schema）。

### 步骤 5：发文路径注入（先做，可独立验证）

`app/publishing/kernel.py` `_launch_browser()`：

```python
proxy_dict, proxy_info = await get_channel_proxy(self._channel_id)
context = await playwright.chromium.launch_persistent_context(
    user_data_dir=temp_dir,
    headless=False,
    executable_path=get_browser_path(),
    viewport={"width": 1440, "height": 1000},
    proxy=proxy_dict,                     # ← 新增（None 时 Playwright 忽略）
    args=[f"--remote-debugging-port={cdp_port}", "--no-sandbox", ...],
)
```

`channel_id` 透传：`orchestrator._publish_with_cookie`（已有 channel_id）→ `adapter.publish(channel_id=)` → `kernel.publish(channel_id=)` → 存 `self._channel_id`。

### 步骤 6：登录路径改造

`app/remote/login.py`：把 `_build_login_browser` + `_open_login_browser`（browser-use 自启动）改为：

```python
async def _launch_login_browser(profile_dir, display, cdp_port, *, proxy_dict=None):
    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=False,
        executable_path=_find_browser_path(),
        env={"DISPLAY": display, **os.environ},   # 替代全局锁设 DISPLAY
        proxy=proxy_dict,                          # ← 注入代理
        args=[f"--remote-debugging-port={cdp_port}", "--kiosk", "--test-type", ...],
    )
    return pw, context

# 然后 browser-use 通过 CDP 连接（与 kernel 一致）
session = BrowserSession(cdp_url=get_cdp_url(cdp_port))
await session.connect()
```

**配套调整**：
- `_build_session` 启动前调 `get_channel_proxy(channel_id)`
- `PROXY_ENABLED=true` 时设 `warm_target=0` 禁用预热池（见 3.2 方案 A）
- 移除 `_launch_lock` DISPLAY 临界区（改用 Playwright `env=`）
- 验证 `--kiosk` + openbox 全屏跟随、`_suppress_background_video`（CDP 注入）在 CDP 连接模式下仍生效——这部分原本就走 CDP，应不受影响

### 步骤 7：出口 IP 验证

两条路径浏览器启动后、正式操作前，调 `ExitIPVerifier.verify(context, proxy_info)`。受 `defaults.verify_exit_ip` 开关控制，失败仅 warning。

### 步骤 8：服务启动初始化

`app/server.py` lifespan：`PROXY_ENABLED=true` 时初始化 `ProxyAssignmentManager`（校验 proxies.yaml，失败则阻止启动），挂到 `agent`。

---

## 五、文件改动清单

### 新增（10）

| 文件 | 说明 |
|------|------|
| `app/proxy/__init__.py` | logger |
| `app/proxy/config.py` | 搬运，改 import |
| `app/proxy/provider.py` | 直接搬运 |
| `app/proxy/assignment.py` | 搬运 + channel_id 语义 |
| `app/proxy/verifier.py` | 搬运，改 import |
| `app/proxy/browser.py` | 新增，get_channel_proxy / build_playwright_proxy |
| `app/proxy/providers/__init__.py` | 直接搬运 |
| `app/proxy/providers/juliang.py` | 搬运，改 import |
| `app/proxy/providers/fixed_auth.py` | 搬运，改 import |
| `proxies.yaml.example` | 模板 |

### 修改（8）

| 文件 | 改动 |
|------|------|
| `app/core/config.py` | 新增 `PROXY_ENABLED`/`PROXY_CONFIG_PATH`/`PROXY_ASSIGNMENTS_PATH` |
| `app/server.py` | lifespan 初始化 ProxyAssignmentManager |
| `app/channels/store.py` | `create()` 分配代理写入 metadata |
| `app/publishing/orchestrator.py` | `_publish_with_cookie` 透传 channel_id |
| `app/publishing/adapter.py` | `publish()` 新增 channel_id 透传 |
| `app/publishing/kernel.py` | `publish()`/`_launch_browser()` 注入代理 |
| `app/remote/login.py` | 改造为 Playwright+CDP；注入代理；禁预热池（启用时） |
| `.env.example` / `.gitignore` | 配置说明 + 忽略敏感文件 |

---

## 六、数据流图

### 6.1 登录 + 代理分配（方案 A，禁预热池）

```
POST /login-sessions {platform}
  → orchestrator.start_login_only()
      → channel_store.create(platform)
          → 分配代理 ip_index（最少绑定优先），写入 channel.metadata
      → _start_remote_login(job_id)
          → remote_runner.start(platform, channel_id)
              → _build_session(platform, channel_id)
                  → get_channel_proxy(channel_id) → proxy_dict
                  → Playwright launch_persistent_context(proxy=proxy_dict, env={DISPLAY})
                  → BrowserSession(cdp_url=...).connect()
                  → ExitIPVerifier.verify()  ← 确认出口 IP
  → 用户 VNC 手动登录（流量走代理 IP）
  → cookie 检测 → channel bind（IP 绑定已在 create 时定）
```

### 6.2 发文 + 代理复用

```
POST /jobs {channel_id, ...}
  → orchestrator._publish_with_cookie(job_id)
      → get_channel_proxy(channel_id) → 同一 proxy_dict（持久化）
      → adapter.publish(channel_id=...)
          → kernel.publish(channel_id=...)
              → _launch_browser(): Playwright launch(proxy=proxy_dict)
              → BrowserSession(cdp_url=...).connect()
  → 发文流量走与登录相同的 IP
```

---

## 七、实施顺序

| 阶段 | 内容 | 优先级 |
|------|------|-------|
| P1 | 搬运 proxy 模块 + 改 import | 最先 |
| P2 | 配置文件 + config.py + .gitignore | 与 P1 并行 |
| P3 | `proxy/browser.py` 封装 + server.py 初始化 | P1/P2 后 |
| P4 | `ChannelStore.create()` 分配代理 | P3 后 |
| P5 | **发文路径注入**（kernel + adapter + orchestrator 透传 channel_id） | 可独立验证 |
| P6 | **登录路径改造**（Playwright+CDP + 禁预热池 + 注入） | P5 后 |
| P7 | 出口 IP 验证集成 | P5/P6 后 |
| P8 | 严格模式 & 错误处理收尾 | 最后 |

**里程碑验证**：
- P5 后：发文时访问 `api.ip.sb/ip`，确认出口 IP = 代理 IP
- P6 后：同一渠道登录 + 发文用同一代理 IP（IP 一致性）
- P8 后：代理不可用时任务正确失败，不泄露真实 IP；`PROXY_ENABLED=false` 全功能正常

---

## 八、风险 & 注意事项

| # | 风险 | 应对 |
|---|------|------|
| 1 | 登录路径改造可能影响 `--kiosk` 全屏、背景视频抑制、导航守卫 | 这些原本就走 CDP（`get_or_create_cdp_session`），CDP 连接模式下应保持；P6 重点回归测试 |
| 2 | 预热池禁用后用户登录等待变长 | 第一阶段接受；后续做方案 B（预热预分配 IP） |
| 3 | SOCKS5 不支持 Chromium 账密认证 | 配置强制 HTTP 优先（已是默认） |
| 4 | 代理引入网络延迟，VNC 登录体验下降 | 选就近地域代理；属可接受成本 |
| 5 | 巨量IP 独享 IP 有订单有效期 | 日志记录 `order_endtime`，运维监控；本期不做自动续期 |
| 6 | 出口 IP 验证 URL（api.ip.sb）不可用 | 可配置；失败仅 warning 不阻断 |
| 7 | 向后兼容 | `PROXY_ENABLED=false`（默认）零影响 |

---

## 九、测试策略

| 测试项 | 方法 |
|--------|------|
| 分配均衡性 | 建 N 个 channel，验证 IP 均匀分布 |
| 持久化 | 重启服务，同 channel_id 仍是同 IP |
| 发文代理生效 | 发文时访问 ip 探测站，出口 = 代理 IP |
| 登录/发文一致性 | 同渠道两路径同 IP |
| 严格模式 | 配无效代理，任务失败而非直连 |
| 向后兼容 | `PROXY_ENABLED=false` 全功能正常 |
| 登录路径回归 | kiosk 全屏、背景视频抑制、导航守卫、cookie 自动捕获 |
