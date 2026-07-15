# Browser Publish Service

基于 FastAPI、Playwright、browser-use 和 OpenAI-compatible LLM（默认 Qwen）的自动发文服务。调用方先通过登录会话获得 `channel_id`，之后提交发文任务时只传 `channel_id`；如果该渠道 Cookie 不存在或失效，服务会返回远程登录链接，用户登录后可保存 Cookie 并继续原发文任务。

## 目录结构

```text
browser-use/
├─ app/                    # 业务代码
│  ├─ server.py            # FastAPI 应用入口
│  ├─ api/                 # HTTP 路由（含 VNC/发布实时查看的反向代理）
│  ├─ accounts/            # 账号管理（加密、模型、MySQL 存储）
│  ├─ channels/            # 渠道存储（SQLite，含 cookie）
│  ├─ cookies/             # Cookie 保存和规范化
│  ├─ core/                # 配置、运行参数与日志
│  ├─ domain/              # 领域模型（Channel、Job、请求/响应）
│  ├─ jobs/                # 任务模型和状态存储（SQLite）
│  ├─ platforms/           # 平台配置（sohu/toutiao）
│  ├─ proxy/               # 多 IP 代理模块（渠道→IP 永久绑定）
│  ├─ publishing/          # 发文编排与各平台发文内核
│  ├─ remote/              # 远程登录与浏览器画面服务
│  ├─ schemas/             # Pydantic 数据模型
│  └─ utils/               # 浏览器、URL 等工具
├─ data/                   # 运行期数据，git 忽略
├─ docs/                   # 设计文档与 API 参考
├─ logs/                   # 运行期日志，git 忽略
├─ tests/                  # 单元测试
├─ Dockerfile
├─ docker-compose.yml
├─ entrypoint.sh
├─ pyproject.toml          # 依赖声明（uv）
├─ uv.lock                 # 锁定版本
├─ .env.example
└── proxies.yaml.example   # 多 IP 代理配置模板
```

## 依赖

- Python >= 3.13
- 运行时依赖声明在 `pyproject.toml`，`uv.lock` 锁定精确版本
- 新增依赖用 `uv add <包名>`，开发依赖用 `uv add --dev <包名>`

## 数据库

本服务使用两种数据库：

| 数据库 | 用途 | 配置 |
|---|---|---|
| **SQLite** | 渠道（channel）、任务（job）存储，持久化 cookie 和发文记录 | `SQLITE_PATH=data/app.db`（默认） |
| **MySQL** | 账号表 `article_accounts` 存储，按 `group_id` 分组管理账号 | `MYSQL_HOST`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE` |

### MySQL 表初始化

`article_accounts` 表由部署侧提前创建，后端只读写不建表。建表后需扩展 `phone` 字段长度（手机号加密后密文最长约 71 字符）：

```sql
ALTER TABLE article_accounts MODIFY COLUMN phone VARCHAR(64) NOT NULL;
```

## 配置

复制环境变量模板：

```bash
cp .env.example .env
```

### 必填配置

```text
# LLM（默认 Qwen）
LLM_API_KEY=your_qwen_key
LLM_BASE_URL=http://47.242.205.13:8110/v1
LLM_MODEL=Qwen/Qwen3.5-397B-A17B
BROWSER_USE_VISION=true
BROWSER_USE_VISION_DETAIL=low

# 接口鉴权 Token（业务路由的 Bearer Token）
PUBLISH_API_TOKEN=change_me_to_a_strong_secret

# 手机号加密密钥（32 bytes 的 base64url 编码）
# 生成方式：openssl rand -base64 32 | tr '+/' '-_' | tr -d '='
ACCOUNT_PHONE_ENCRYPTION_KEY=<生成的密钥>

# MySQL 账号存储
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=browser_publish
MYSQL_PASSWORD=change_me
MYSQL_DATABASE=browser_publish
```

### 关键可选配置

```text
# SQLite 存储路径（留空则回退内存存储，仅本地开发/测试用）
SQLITE_PATH=data/app.db

# 服务端口（默认 8833）
SERVICE_PORT=8833

# 远程登录并发（每个会话约占 400MB 内存，按服务器内存设置）
MAX_REMOTE_LOGIN_SESSIONS=30

# 登录 live url 对外暴露的公网 base（反向代理需转发 /vnc/{session_id}/）
REMOTE_PUBLIC_BASE_URL=http://222.212.94.89
```

完整配置项见 `.env.example`，所有字段均有注释说明。

`BROWSER_USE_VISION` 支持 `auto`、`true`、`false`；发布 agent 默认 `true`，确保 browser-use 每步截图会进入支持视觉的 LLM 输入，便于识别短暂弹窗、toast 和页面异常。`BROWSER_USE_VISION_DETAIL` 支持 `low`、`high`、`auto`，默认 `low` 以控制图片负载。

## Docker 启动

```bash
docker compose up -d --build
```

服务地址（`docker-compose.yml` 默认将容器内 8833 映射到宿主机 8833，可通过 `HOST_PORT` 环境变量自定义）：

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

## 运行测试

```bash
uv run pytest
```

测试位于 `tests/unit/`，使用 pytest + pytest-asyncio。

## 手机号加密

数据库中的手机号使用 **AES-256-SIV 确定性加密**存储，不再保存明文。

- 密文格式：`enc:v1:<base64url(ciphertext)>`
- 同一手机号 + 同一密钥 = 同一密文，可直接 `WHERE phone = ?` 查询
- API 接口仍使用明文手机号，服务内部自动加解密，对外契约不变
- 加密密钥通过环境变量 `ACCOUNT_PHONE_ENCRYPTION_KEY` 管理，缺失时服务无法启动

### 历史数据迁移

如果数据库已有明文手机号，需执行迁移：

```python
import os
import pymysql
from app.accounts.crypto import PhoneCrypto

crypto = PhoneCrypto()

conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST"),
    user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"),
    database=os.getenv("MYSQL_DATABASE"),
    charset="utf8mb4",
    autocommit=True,
    cursorclass=pymysql.cursors.DictCursor,
)

with conn.cursor() as cur:
    cur.execute("SELECT id, phone FROM article_accounts WHERE phone NOT LIKE 'enc:v1:%%'")
    rows = cur.fetchall()

for row in rows:
    encrypted = crypto.encrypt_phone(row["phone"])
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE article_accounts SET phone = %s WHERE id = %s",
            (encrypted, row["id"])
        )
    print(f"Migrated id={row['id']}")

conn.close()
print(f"Done. Migrated {len(rows)} records.")
```

## Channel 去重

绑定接口 `PUT /api/v1/accounts/{platform}/{phone}` 按 `group_id + platform + phone` 去重：

- 同一分组下同一手机号重复绑定时，旧 channel 及其 cookie、代理绑定会被自动清理
- 新 channel 正常绑定，对外接口不变

## 标题字数规则

| 平台 | 范围 | 计算规则 |
|---|---|---|
| 头条号 | 2-30 字 | 汉字算 1 字，英文/数字 2 字符算 1 字，**空格不计** |
| 搜狐号 | 5-72 字 | 汉字算 1 字，英文/数字 2 字符算 1 字，**空格计入** |

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

完整的请求/响应结构以服务自带的交互式文档为准（基于 OpenAPI 自动生成，无需手工维护）。Scalar 页面已尽量使用中文摘要、参数说明、字段说明、枚举值、必填项和默认值；代码随附文档用于补充流程语义和集成注意事项：

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
| `GET` | `/api/v1/accounts/all` | 是 | 列出指定 `group_id` 下的所有账号 |
| `GET` | `/api/v1/accounts/available` | 是 | 列出指定 `group_id` 下的可用账号（仅排除 `disabled` / `muted`） |
| `GET` | `/api/v1/accounts/{platform}/{phone}` | 是 | 查询指定账号详情 |
| `PUT` | `/api/v1/accounts/{platform}/{phone}` | 是 | 保存或重新绑定账号到 channel |
| `PATCH` | `/api/v1/accounts/{platform}/{phone}` | 是 | 修改账号手机号、状态、失败次数等持久字段 |
| `DELETE` | `/api/v1/accounts/{platform}/{phone}` | 是 | 删除账号并清理关联 channel/cookie/proxy assignment |

完整字段级说明见 `docs/api-reference.md`。

发文（LLM 自动操作）与登录（真人手动操作）是两套独立资源：发文走 `/jobs`，登录走 `/login-sessions`，两者底层共用 Job 存储但在接口上互不可见（拿发文 `job_id` 去访问 `/login-sessions/*` 会得到 404，反之亦然）。

账号管理接口使用调用方传入的 `group_id` 做分组/租户隔离，服务端 `.env` 不提供默认 `group_id`。凡文档标为必填的 `group_id` 都没有默认值：缺少该字段会由 FastAPI/OpenAPI 校验返回 `422`；传空字符串会返回业务错误 `400 missing_group_id`。

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

## 故障排查

### 服务启动失败

- 检查 `.env` 中所有必填配置是否已填写（见上文"必填配置"）
- 检查 MySQL 是否可达：`mysql -h $MYSQL_HOST -u $MYSQL_USER -p`
- 查看容器日志：`docker compose logs publish`

### 登录页白屏

- `docker-compose.yml` 中已配置 `extra_hosts` 将 `g1.itc.cn` 钉到可达 IP（搜狐号登录页 bundle 托管在此 CDN）
- 若日后仍白屏，在服务器执行 `getent ahosts g1.itc.cn` 获取可达 IP 后更新 `docker-compose.yml`

### Cookie 失效

- 发文任务会自动检测 cookie 失效并返回 `live_url` 要求重新登录
- 登录成功后 cookie 自动更新，原发文任务继续执行
- 若频繁失效，检查代理 IP 是否被平台封禁

### 端口被占用

- 默认服务端口 8833，可通过 `HOST_PORT` 环境变量修改宿主机映射端口
- CDP 端口段 9000-9999，KasmVNC 端口段 6900+，均监听 127.0.0.1
