# Browser Publish Service

基于 FastAPI、Playwright、browser-use 和 OpenAI-compatible LLM（默认 Qwen）的自动发文服务。调用方先通过登录会话获得 `channel_id`，之后提交发文任务时只传 `channel_id`；如果该渠道 Cookie 不存在或失效，服务会返回远程登录链接，用户登录后可保存 Cookie 并继续原发文任务。

## 目录结构

```text
browser-use/
├─ app/                    # 业务代码
│  ├─ server.py            # FastAPI 应用入口
│  ├─ api/                 # HTTP 路由（含 VNC/发布实时查看的反向代理）
│  ├─ core/                # 配置、运行参数与日志
│  ├─ cookies/             # Cookie 保存和规范化
│  ├─ jobs/                # 任务模型和状态存储
│  ├─ platforms/           # 平台配置（sohu/toutiao）
│  ├─ proxy/               # 多 IP 代理模块（渠道→IP 永久绑定）
│  ├─ publishing/          # 发文编排与各平台发文内核
│  ├─ remote/              # 远程登录与浏览器画面服务
│  └─ utils/               # 浏览器、URL 等工具
├─ data/                   # 运行期数据，git 忽略
├─ logs/                   # 运行期日志，git 忽略
├─ Dockerfile
├─ docker-compose.yml
├─ entrypoint.sh
├─ pyproject.toml          # 依赖声明（uv）
├─ uv.lock                 # 锁定版本
├─ .env.example
└─ proxies.yaml.example     # 多 IP 代理配置模板
```

## 配置

复制环境变量模板：

```bash
cp .env.example .env
```

至少配置：

```text
LLM_API_KEY=your_qwen_key
LLM_BASE_URL=http://47.242.205.13:8110/v1
LLM_MODEL=Qwen/Qwen3.5-397B-A17B
BROWSER_USE_VISION=auto
BROWSER_USE_VISION_DETAIL=low
```

`BROWSER_USE_VISION` 支持 `auto`、`true`、`false`；默认 `auto`，便于使用多模态模型时按需启用截图输入。`BROWSER_USE_VISION_DETAIL` 支持 `low`、`high`、`auto`，默认 `low` 以控制图片负载。

搜狐号发布会把后台预览链接转换为 `https://www.sohu.com/a/{article_id}_{account_id}`。其中的搜狐号数字 id 按渠道存在各 channel 的 `metadata`（`account_number`）里，登录期抓取。

## Docker 启动

```bash
docker compose up -d --build
```

服务地址（`docker-compose.yml` 默认将容器内 8833 映射到宿主机 8833）：

```text
http://127.0.0.1:8833
```

查看日志：

```bash
docker compose logs -f publish
```

停止服务：

```bash
docker compose down
```

## 本地启动

使用 [uv](https://docs.astral.sh/uv/) 管理 Python 3.13 虚拟环境（无需 conda）：

```bash
# 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 按 pyproject.toml / uv.lock 创建 .venv 并安装依赖（uv 会自动拉取 Python 3.13）
uv sync

# 安装浏览器并启动服务
uv run playwright install chromium
uv run uvicorn app.server:app --host 127.0.0.1 --port 8833
```

依赖在 `pyproject.toml` 中声明，`uv.lock` 锁定精确版本。新增依赖用 `uv add <包名>`，开发依赖用 `uv add --dev <包名>`。

## 多 IP 代理（可选）

### 背景

大厂平台（今日头条、搜狐号等）会检测同一 IP 下有多账号发文，触发验证码或封号。本服务支持为每个渠道（channel）绑定一个独立的静态代理 IP，使登录和发文流量始终走同一个代理出口 IP，避免风控。

### 启用代理

```bash
# 1. 复制代理配置模板并填入真实代理信息
cp proxies.yaml.example proxies.yaml

# 2. 在 .env 中开启代理
# PROXY_ENABLED=true
# 默认配置路径即 proxies.yaml，无需修改 PROXY_CONFIG_PATH
```

### 代理池配置

`proxies.yaml` 结构（完整写法见 `proxies.yaml.example`）：

```yaml
defaults:
  protocol: http          # Chromium 支持 HTTP 代理的账密认证（推荐）
  verify_exit_ip: true    # 浏览器启动后校验实际出口 IP
  exit_ip_check_url: "https://api.ip.sb/ip"

ip_pool:
  - provider: fixed_auth   # 固定代理服务器
    ip: "1.2.3.4"
    port: 2018
    username: "your_user"
    password: "your_pass"
    protocol: http
    label: "静态代理-1"

  - provider: juliangip    # 巨量 IP 独享代理（按需启用）
    trade_no: "YOUR_TRADE_NO"
    api_key: "YOUR_API_KEY"
    label: "独享IP-1"
```

两种 provider：
- **`fixed_auth`**：固定代理服务器 + 用户名密码认证，一般有多个固定 IP 加多条
- **`juliangip`**：通过巨量 IP API 动态获取，订单期内 IP 固定

### 绑定机制

- **最少绑定优先（least-bind-first）**：新渠道创建时自动分配到当前绑定数最少的 IP
- **永久绑定**：一旦绑定关系写入 `data/proxy_assignments.json`，不会自动变更
- **发布/登录均走同一代理**：通过 Playwright `launch_persistent_context(proxy=...)` 注入

### 出口 IP 校验

`verify_exit_ip: true` 时，浏览器启动后会访问 `exit_ip_check_url` 获取实际出口 IP，与代理 IP 进行比对：
- 校验失败：写 ⚠️ 警告日志，**不阻断流程**（防止 IP 检测服务抖动导致全量发布失败）
- 校验通过：写 ✅ 成功日志，发布正常进行

### 注意事项

1. **Docker 部署需挂载 `proxies.yaml`**：`docker-compose.yml` 已包含 `./proxies.yaml:/app/proxies.yaml:ro` 只读挂载
2. **严格启动**：`PROXY_ENABLED=true` 时，`proxies.yaml` 缺失或无效会阻塞服务启动
3. **代理失败即失败**：获取代理失败时发布任务直接失败，不会 fallback 直连
4. **预热池自动关闭**：启用代理后远程登录的 warm pool 自动置零（因为预热时不知道要给哪个 channel 分配代理）
5. **SOCKS5 不支持账密**：Chromium 限制，SOCKS5 代理无法传用户名密码，推荐用 HTTP
6. **IP 池变更**：新增 IP 只需在 `ip_pool` 追加条目，新渠道自动流向它；已绑定的渠道不受影响

## 接口说明

### 信任模型（重要）

完整的请求/响应结构以服务自带的交互式文档为准（基于 OpenAPI 自动生成，无需手工维护）：

- 交互式 API 文档（Scalar）：`GET /scalar`
- OpenAPI 描述：`GET /openapi.json`
- 代码随附的企业级 API 文档：[`docs/api-reference.md`](docs/api-reference.md)

除 `/health` 外，`/api/v1` 下的业务接口均需在请求头携带 Bearer Token：

```text
Authorization: Bearer <PUBLISH_API_TOKEN>
```

> **信任模型（重要）**：本服务面向**单一受信任的后端集成方**（server-to-server）。
> `PUBLISH_API_TOKEN` 是一个共享密钥，**绝不能下发到终端用户的设备、浏览器或任何客户端代码**。
> `channel_id` 是服务签发的能力句柄，**不是经过认证的身份，也没有按租户的授权**——
> 持有 token 的一方可以读取/操作任意 `channel_id`。若未来要把接口直接开放给彼此不信任的多个第三方，
> 必须先引入**一租户一凭证**并在所有 job/channel 路由上加**归属校验**。

核心概念 **channel_id**：服务签发的全局句柄，与平台无关，一对一绑定到「一个平台 + 一个平台账号 + 一份 cookie」。先登录拿到 `channel_id`，之后发文只认它。

主要端点：

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|:---:|---|
| `GET` | `/health` | 否 | 进程存活检查 |
| `GET` | `/api/v1/platforms` | 是 | 支持的平台列表 |
| `POST` | `/api/v1/jobs` | 是 | 创建发文任务（传 `channel_id`，立即返回不阻塞） |
| `GET` | `/api/v1/jobs/{job_id}` | 是 | 查询发文任务状态与发布结果 |
| `POST` | `/api/v1/jobs/{job_id}/save-cookie` | 是 | 发文任务登录后保存 Cookie 并续发 |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | 是 | 取消发文任务 |
| `POST` | `/api/v1/login-sessions` | 是 | 创建登录会话（传 `platform`，签发新 `channel_id`） |
| `GET` | `/api/v1/login-sessions/{session_id}` | 是 | 查询登录会话状态（含 `channel_id`） |
| `DELETE` | `/api/v1/login-sessions/{session_id}` | 是 | 取消登录会话（释放 Xvnc 显示槽） |
| `GET` | `/api/v1/channels/{channel_id}` | 是 | 查询渠道状态（平台/账号名/cookie 是否有效） |
| `GET` | `/api/v1/channels/{channel_id}/publish-status` | 是 | 查询渠道发文状态（idle/publishing + publish_count） |
| `DELETE` | `/api/v1/channels/{channel_id}` | 是 | 删除渠道（含 cookie） |

完整字段级说明见 `docs/api-reference.md`。

发文（LLM 自动操作）与登录（真人手动操作）是两套独立资源：发文走 `/jobs`，登录走 `/login-sessions`，两者底层共用 Job 存储但在接口上互不可见（拿发文 `job_id` 去访问 `/login-sessions/*` 会得到 404，反之亦然）。

典型流程（登录在先）：先 `POST /api/v1/login-sessions` 传 `platform`，拿到 `channel_id` 和 `live_url`，用户在 `live_url` 登录成功后 cookie 自动绑定到该渠道；之后 `POST /api/v1/jobs` 传 `channel_id` + 文章发文，若该渠道 cookie 已失效则自动重新登录同一渠道并续发；全程用 `GET /api/v1/jobs/{job_id}` 轮询状态。

同一个 `channel_id` 下多次提交发文会排队串行执行：当前任务发文完成、失败或取消后，系统自动启动同渠道下一篇。不同 `channel_id` 的发文任务互不等待，仍可并发执行。

发文请求示例：

```bash
curl -X POST http://127.0.0.1:8833/api/v1/jobs \
  -H "Authorization: Bearer <PUBLISH_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
    "title": "测试标题",
    "content": "文章正文",
    "cover_image_url": "https://example.com/cover.jpg"
  }'
```

查询渠道是否可提交新发文：

```bash
curl http://127.0.0.1:8833/api/v1/channels/3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c/publish-status \
  -H "Authorization: Bearer <PUBLISH_API_TOKEN>"
```

空闲返回：

```json
{
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "account_status": "idle",
  "publish_count": 0
}
```

发文中或排队中返回：

```json
{
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "account_status": "publishing",
  "publish_count": 3
}
```

`publish_count` 统计同一 `channel_id` 下未完成的 publish job，包含正在执行和排队中的任务，不包含已成功、已失败、已取消和 login-only 任务。`waiting_cookie` 也计入 `publishing`，因为它仍属于某个发文任务的补登/续发流程。

## 运行期目录

- 渠道（cookie + 账号元数据）和任务状态存在 SQLite 的 `data/app.db` 中，无需额外数据库服务
- `data/proxy_assignments.json`：渠道→代理 IP 绑定关系持久化（启用代理时自动生成）
- `data/profiles/{session_id}/`：远程登录临时浏览器 profile
- `data/chrome_profile/`：发文内核浏览器 profile，容器化部署时通过 `docker-compose.yml` 的 `shm_size` 挂载足够 `/dev/shm`
- `logs/jobs/{YYYY-MM-DD}/{job_id}.log`：每个任务一个日志文件（编排 + 发文内核日志合一），
  仅供运维在服务器侧排查（不对外提供 HTTP 接口）；按日期分目录，超过 14 天的日期目录自动清理
- `logs/service.log`：统一主日志（每行带 job_id，按天轮转、保留 14 天、zip 压缩）
