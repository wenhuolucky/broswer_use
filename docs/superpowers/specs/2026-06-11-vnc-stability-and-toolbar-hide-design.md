# 远程登录 VNC 连接稳定性与控制面板隐藏设计

## 目标

解决远程登录（KasmVNC）的两个体验问题，且**不改动发文主流程**、**不削弱并发能力**：

1. **连接稳定性**：KasmVNC 画面经常在 8-9 秒后断开。
2. **控制面板遮挡**：远程浏览器画面上叠加了 KasmVNC/noVNC 自带的控制面板（侧边/顶部工具条），需要隐藏。

两个问题的改动都集中在 `app/api/vnc_proxy.py` 与 `app/remote/login.py`，与发文内核（`app/publishing/*`、`app/remote/viewer.py`）完全隔离。

## 范围与非目标

### 范围
- `app/api/vnc_proxy.py`：WebSocket 桥接心跳、HTML 响应 CSS 注入。
- `app/remote/login.py`：cloudflared 启动参数、（可选）VNC URL 路径。
- `app/streaming/kasmvnc.py`：（可选）KasmVNC idle timeout 相关启动参数。
- 新增配置项（带默认值，向后兼容）。

### 非目标
- 不修改发文编排（`PublishAgent`）、发文内核（`PublishService` 及其子类）。
- 不修改发文阶段的实时查看器（`app/remote/viewer.py`，走 CDP screencast，与 KasmVNC 无关）。
- 不修改 Cookie 存储、Job 存储、平台 prompt、API 请求/响应结构。
- 不替换 KasmVNC / cloudflared 技术选型。

## 背景：连接链路

远程登录时用户访问画面的完整链路：

```text
浏览器 noVNC JS
  → WSS → Cloudflare 边缘 (quick tunnel)
  → WSS → cloudflared 本地进程
  → WS  → FastAPI vnc_proxy  (Starlette WebSocket, app/api/vnc_proxy.py)
  → WS  → KasmVNC websockify (Xvnc -websocketPort, app/streaming/kasmvnc.py)
```

发文阶段的"实时查看链接"是另一条独立链路（`app/remote/viewer.py` 的 CDP screencast + 自建 aiohttp 服务），**本设计完全不触及它**。

## 问题一：8-9 秒断开

### 根因分析

代码层面已确认的薄弱点：

1. **vnc_proxy 桥接无心跳**（`_bridge()`，vnc_proxy.py:153-181）
   - 两个方向都是"收到才转发"，空闲时 `client_ws.receive()` / `async for message in upstream` 永久阻塞。
   - Starlette WebSocket 不会自动发送 ping/pong 控制帧。
   - 用户不操作时（不动鼠标键盘），noVNC 几乎不发数据，VNC 服务端只在画面变化时才推帧，整条链路长时间空闲。

2. **上游 websockets 连接未配置心跳**（vnc_proxy.py:137-143）
   - `websockets.connect()` 未设置 `ping_interval` / `ping_timeout`，使用库默认（约 20s ping、20s timeout）。
   - websockify 历史上不一定响应标准 WebSocket ping，可能触发 40s 后的误断。

3. **cloudflared 无 idle-timeout 参数**（login.py:492-493）
   - quick tunnel 经 Cloudflare 边缘，空闲 WebSocket 会被边缘节点回收，超时阈值不可控。

4. **KasmVNC 自身可能存在 idle session timeout**
   - `Xvnc` 启动参数未显式设置，依赖默认值。

### 8-9 秒的判断

8-9 秒明显短于 websockets 库 40s 的 ping 超时，因此**主因更可能在链路上游空闲回收**（cloudflared 边缘或中间网络对无数据 WebSocket 的激进超时），而非库层 ping 超时。结论：单点修复不可靠，采用**多层防御 + 诊断日志**，每一段链路都保持有数据流动并能定位实际断点。

### 解决方案：分层 keepalive

#### 层 1：vnc_proxy ↔ KasmVNC（上游）

为 `websockets.connect()` 显式配置心跳：

```python
websockets.connect(
    upstream_url,
    max_size=None,
    open_timeout=10,
    ping_interval=KASMVNC_WS_PING_INTERVAL,   # 默认 15s
    ping_timeout=KASMVNC_WS_PING_TIMEOUT,     # 默认 60s（放宽，避免误断）
    subprotocols=requested or None,
    additional_headers={"Origin": f"http://127.0.0.1:{port}"},
)
```

`ping_timeout` 放宽到 60s，避免 websockify 不及时回 pong 时被库误判断开。

#### 层 2：vnc_proxy → 浏览器（下游，关键）

这是解决"边缘空闲回收"的核心。在 `_bridge()` 中增加**独立心跳协程**，周期性向浏览器侧 WebSocket 发送 ping 帧，使链路始终有数据流动：

```python
async def keepalive_client() -> None:
    while True:
        await asyncio.sleep(VNC_PROXY_KEEPALIVE_INTERVAL)  # 默认 10s
        try:
            await client_ws.send_bytes(b"")   # 或 Starlette 支持的 ping 机制
        except Exception:
            break
```

> 实施细节：Starlette WebSocket 不直接暴露发送 ping 控制帧的 API。落地时优先用 uvicorn 的 `--ws-ping-interval` / `--ws-ping-timeout` 让底层 websockets 自动对浏览器侧发协议级 ping（最干净）；若该机制对某些边缘无效，再退化为应用层定时发送 noVNC 可安全忽略的空帧/保活帧。两种手段二选一或叠加，最终以"链路 10s 内必有一次数据往返"为目标。

`keepalive_client` 与原有两个转发协程一起加入 `asyncio.wait(..., FIRST_COMPLETED)`，任一结束即清理全部，保持原有生命周期语义。

#### 层 3：cloudflared idle-timeout

在 quick tunnel 启动命令中追加：

```python
"--idle-timeout", CLOUDFLARED_IDLE_TIMEOUT,   # 默认 "3600s"
```

> 注意：`--idle-timeout` 对 quick tunnel 的实际效果以运行验证为准；若该版本 cloudflared 不支持或无效，依赖层 2 的下游 keepalive 兜底（有持续数据流则边缘不会判定空闲）。

#### 层 4（可选）：KasmVNC idle timeout

如运行后确认 KasmVNC 自身回收 session，再在 `kasmvnc.py` 的 `Xvnc` 参数或 `/etc/kasmvnc/kasmvnc.yaml` 中放宽 idle timeout。**默认不动**，避免无谓改动启动参数影响并发稳定性。

### 诊断日志

为定位真实断点，在 `vnc_ws` / `_bridge` 增加结构化日志（复用 session logger，不引入新 sink）：
- WebSocket 建立、子协议、上游连接成功/失败。
- 每个方向关闭的触发源（client→upstream 还是 upstream→client）、关闭码、存活时长。
- keepalive 发送失败。

便于上线后从日志判断 8-9 秒断点究竟在哪一层，再决定是否启用层 3/4。

## 问题二：隐藏 KasmVNC 控制面板

### 现状

KasmVNC 的 noVNC 前端由 `Xvnc -httpd /usr/share/kasmvnc/www` 提供，页面带有控制面板（侧边/顶部工具条：Drag、Viewport、Clipboard、Fullscreen、Settings、Disconnect 等）。vnc_proxy 当前对 HTTP 响应做的是**透传流式代理**（`StreamingResponse` + `aiter_raw`），HTML 原样下发，所以面板可见。

### 方案选择

| 方案 | 可行性 | 侵入性 | 取舍 |
|---|:---:|:---:|---|
| A. vnc_proxy 对 HTML 注入隐藏 CSS | 高 | 低 | 保留完整功能，仅视觉隐藏；需对 HTML 主文档改流式为缓冲 |
| B. 改用 `vnc_lite.html` | 中 | 低 | 天然无完整面板，但同时失去剪贴板等能力 |
| C. Dockerfile 改 KasmVNC www 文件 | 中 | 中 | KasmVNC 升级后丢失，运维负担 |
| D. iframe 包装页 | 高 | 低 | 实现稍复杂，与 A 思路重叠 |

**采用方案 A**：视觉隐藏、保留功能、改动最小、与 KasmVNC 版本解耦。

### 实现

仅对 `/vnc/{session_id}/` 的 **HTML 主文档**（`Content-Type: text/html`）做缓冲注入，其余资源（JS/CSS/字体/WebSocket）保持原有流式透传：

1. 在 `vnc_http` 收到上游响应后，判断 `content-type` 是否为 `text/html`。
2. 若是：读取完整 body（HTML 主文档体积小，可安全缓冲），在 `</head>` 前插入：
   ```html
   <style id="kasm-hide-toolbar">
     #noVNC_control_bar_anchor,
     #noVNC_control_bar,
     #noVNC_status,
     #noVNC_hint_anchor { display: none !important; }
   </style>
   ```
   返回普通 `Response`（非流式）。
3. 若否：维持现有 `StreamingResponse` 透传，零行为变化。

> 选择器以 noVNC 标准 ID（`noVNC_control_bar_anchor` 等）为准；KasmVNC 定制版若 ID 不同，上线后按实际 DOM 微调选择器即可，不影响代理逻辑。

注入受新配置 `VNC_HIDE_TOOLBAR`（默认开启）控制，可一键回退。

## 配置项（新增，全部带默认值，向后兼容）

| 环境变量 | 默认值 | 作用 |
|---|---|---|
| `VNC_PROXY_KEEPALIVE_INTERVAL` | `10` | 下游浏览器侧保活间隔（秒） |
| `KASMVNC_WS_PING_INTERVAL` | `15` | 上游 websockets ping 间隔（秒） |
| `KASMVNC_WS_PING_TIMEOUT` | `60` | 上游 websockets ping 超时（秒） |
| `CLOUDFLARED_IDLE_TIMEOUT` | `3600s` | cloudflared quick tunnel 空闲超时 |
| `VNC_HIDE_TOOLBAR` | `true` | 是否注入 CSS 隐藏控制面板 |

uvicorn 侧若采用底层 ping 方案，另在启动命令/配置加 `--ws-ping-interval` / `--ws-ping-timeout`（仅 server 启动参数，不影响应用逻辑）。

## 并发与发文流程影响评估

### 不影响发文流程
- 改动文件 `vnc_proxy.py` / `login.py` 仅服务于远程登录链路。
- 发文阶段使用 `PublishService` + `app/remote/viewer.py`（CDP screencast），与 KasmVNC、vnc_proxy 无任何调用关系。
- 不触碰 `PublishAgent`、Cookie/Job 存储、平台 prompt、API 模型。

### 不削弱并发
- keepalive 是**每连接一个轻量 asyncio 协程**，纯异步 `sleep` + `send`，不持有全局锁、不阻塞事件循环。
- `DisplayPool`、CDP 端口分配、session 生命周期管理完全不变，`MAX_REMOTE_LOGIN_SESSIONS` 并发上限不受影响。
- HTML CSS 注入只对小体积 HTML 主文档缓冲，单连接一次性开销；JS/字体/WebSocket 仍流式，不增加内存峰值。
- 新增协程数量 = 活跃 VNC 连接数 × 1，与现有 session 数同量级，无放大效应。

### 风险与回退
- 每个新行为都有配置开关，可独立关闭回到当前行为。
- 心跳/超时参数全部可调，上线后按日志微调。
- CSS 选择器与 KasmVNC DOM 强相关，若版本差异导致未命中，仅"面板仍显示"，不影响连接与发文。

## 实施步骤

1. `app/core/config.py`：新增 5 个配置项（含默认值与解析）。
2. `app/api/vnc_proxy.py`：
   - `_bridge()` 增加下游 keepalive 协程，纳入 `FIRST_COMPLETED` 生命周期。
   - `websockets.connect()` 增加 `ping_interval` / `ping_timeout`。
   - `vnc_http()` 对 `text/html` 响应做缓冲 + CSS 注入；其余透传。
   - 增加结构化诊断日志。
3. `app/remote/login.py`：cloudflared 启动追加 `--idle-timeout`。
4. `app/server.py` 或启动脚本：（按需）uvicorn WebSocket ping 参数。
5. `.env.example`：补充新配置项说明。
6. 测试（`tests/`，遵循项目"tests 不入库"约定，仅本地验证）：
   - vnc_proxy HTML 注入：`text/html` 注入、非 HTML 透传、`VNC_HIDE_TOOLBAR=false` 回退。
   - 桥接 keepalive 协程在断开时被正确取消。
   - cloudflared 命令含 idle-timeout 参数。
   - 配置项默认值与解析。

## 验证方式

- 本地/容器启动远程登录，打开 VNC URL，**静置 ≥ 60s** 不操作，确认画面不断开。
- 观察 session 日志确认断点层级与 keepalive 正常。
- 确认控制面板已隐藏，鼠标/键盘/扫码操作正常。
- 并发发起多个远程登录（接近 `MAX_REMOTE_LOGIN_SESSIONS`），确认互不影响、发文任务正常完成。

