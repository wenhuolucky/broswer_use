# Browser Publish Service

基于 FastAPI、Playwright、browser-use 和 OpenAI-compatible LLM（默认 Qwen）的自动发文服务。调用方提交发文任务后，服务会按 `user_id` 查找 Cookie；如果 Cookie 不存在或失效，会返回远程登录链接，用户登录后可调用保存 Cookie 接口继续原发文任务。

## 目录结构

```text
browser-use/
├─ app/                    # 业务代码
│  ├─ server.py            # FastAPI 应用入口
│  ├─ api/                 # HTTP 路由
│  ├─ core/                # 配置、运行参数与日志
│  ├─ cookies/             # Cookie 保存和规范化
│  ├─ jobs/                # 任务模型和状态存储
│  ├─ platforms/           # 平台配置
│  ├─ publishing/          # 发文编排与各平台发文内核
│  ├─ remote/              # 远程登录与浏览器画面服务
│  └─ utils/               # 浏览器、URL 等工具
├─ data/                   # 运行期数据，git 忽略
├─ logs/                   # 运行期日志，git 忽略
├─ Dockerfile
├─ docker-compose.yml
├─ pyproject.toml          # 依赖声明（uv）
├─ uv.lock                 # 锁定版本
└─ .env.example
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

## 接口说明

完整的请求/响应结构以服务自带的交互式文档为准（基于 OpenAPI 自动生成，无需手工维护）：

- 交互式 API 文档（Scalar）：`GET /scalar`
- OpenAPI 描述：`GET /openapi.json`

除 `/health` 与 `/api/v1/ready` 外，`/api/v1` 下的业务接口均需在请求头携带 Bearer Token：

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
| `GET` | `/api/v1/ready` | 否 | 就绪检查（含存储类型） |
| `GET` | `/api/v1/platforms` | 是 | 支持的平台列表 |
| `POST` | `/api/v1/jobs` | 是 | 创建发文任务（传 `channel_id`，立即返回不阻塞） |
| `GET` | `/api/v1/jobs` | 是 | 发文任务列表（支持 channel_id/status 过滤） |
| `GET` | `/api/v1/jobs/{job_id}` | 是 | 查询发文任务状态与发布结果 |
| `POST` | `/api/v1/jobs/{job_id}/save-cookie` | 是 | 发文任务登录后保存 Cookie 并续发 |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | 是 | 取消发文任务 |
| `POST` | `/api/v1/login-sessions` | 是 | 创建登录会话（传 `platform`，签发新 `channel_id`） |
| `GET` | `/api/v1/login-sessions` | 是 | 登录会话列表（支持 channel_id/status 过滤） |
| `GET` | `/api/v1/login-sessions/{session_id}` | 是 | 查询登录会话状态（含 `channel_id`） |
| `POST` | `/api/v1/login-sessions/{session_id}/save-cookie` | 是 | 登录完成后保存 Cookie |
| `DELETE` | `/api/v1/login-sessions/{session_id}` | 是 | 取消登录会话（释放 Xvnc 显示槽） |
| `GET` | `/api/v1/channels` | 是 | 渠道列表（支持 platform 过滤） |
| `GET` | `/api/v1/channels/{channel_id}` | 是 | 查询渠道状态（平台/账号名/cookie 是否有效） |
| `POST` | `/api/v1/channels/{channel_id}/relogin` | 是 | 渠道 cookie 失效后重新登录（channel_id 不变） |
| `DELETE` | `/api/v1/channels/{channel_id}` | 是 | 删除渠道（含 cookie） |

发文（LLM 自动操作）与登录（真人手动操作）是两套独立资源：发文走 `/jobs`，登录走 `/login-sessions`，两者底层共用 Job 存储但在接口上互不可见（拿发文 `job_id` 去访问 `/login-sessions/*` 会得到 404，反之亦然）。

典型流程（登录在先）：先 `POST /api/v1/login-sessions` 传 `platform`，拿到 `channel_id` 和 `live_url`，用户在 `live_url` 登录成功后 cookie 自动绑定到该渠道；之后 `POST /api/v1/jobs` 传 `channel_id` + 文章发文，若该渠道 cookie 已失效则自动重新登录同一渠道并续发；全程用 `GET /api/v1/jobs/{job_id}` 轮询状态。

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

## 运行期目录

- 渠道（cookie + 账号元数据）存在 PostgreSQL 的 `channels` 表，不再落盘
- `data/profiles/{session_id}/`：远程登录临时浏览器 profile
- `data/chrome_profile/`：发文内核浏览器 profile
- `logs/jobs/{YYYY-MM-DD}/{job_id}.log`：每个任务一个日志文件（编排 + 发文内核日志合一），
  仅供运维在服务器侧排查（不对外提供 HTTP 接口）；按日期分目录，超过 14 天的日期目录自动清理
- `logs/service.log`：统一主日志（每行带 job_id，按天轮转、保留 14 天、zip 压缩）
