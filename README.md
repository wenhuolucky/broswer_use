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
│  ├─ publishing/          # 发文编排和头条发文内核
│  ├─ remote/              # 远程登录与浏览器画面服务
│  └─ utils/               # 浏览器、URL 等工具
├─ data/                   # 运行期数据，git 忽略
├─ logs/                   # 运行期日志，git 忽略
├─ Dockerfile
├─ docker-compose.yml
├─ requirements.txt
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
AGENT_VERBOSE_LOG_ENABLED=true
AGENT_VERBOSE_LOG_INPUT_MODE=summary
AGENT_VERBOSE_LOG_MAX_CHARS=12000
AGENT_VERBOSE_LOG_MESSAGE_MAX_CHARS=12000
AGENT_LOG_COLOR_ENABLED=true
SOHU_ACCOUNT_ID=
SOHU_ACCOUNT_ID_MAP=
```

`BROWSER_USE_VISION` 支持 `auto`、`true`、`false`；默认 `auto`，便于使用多模态模型时按需启用截图输入。`BROWSER_USE_VISION_DETAIL` 支持 `low`、`high`、`auto`，默认 `low` 以控制图片负载。

Agent 详细诊断日志默认开启，会在任务日志中输出 `[AgentLLM:*]`、`[AgentStep:*]`、`[AgentState:*]`、`[AgentGuard:*]`、`[AgentTool:*]`、`[AgentFinal:*]` 等固定标志，方便排查 LLM 输出、动作选择、页面状态和最终结果。`AGENT_VERBOSE_LOG_INPUT_MODE` 支持 `summary`、`none`、`full`，默认 `summary` 只输出输入摘要；`AGENT_LOG_COLOR_ENABLED` 只影响控制台彩色输出，文件日志保持纯文本。

搜狐号发布会把后台预览链接转换为 `https://m.sohu.com/a/{article_id}_{account_id}?sec=wd`。单账号部署可配置 `SOHU_ACCOUNT_ID`；多账号部署可配置 `SOHU_ACCOUNT_ID_MAP`，格式为 `user1:122702850,user2:122580788`。

## Docker 启动

```bash
docker compose up -d --build
```

服务地址：

```text
http://127.0.0.1:19000
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

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m uvicorn app.server:app --host 127.0.0.1 --port 19000
```

## 接口说明

### 1. 健康检查

```http
GET /api/v1/publish/health
```

用于确认服务进程是否可用。

请求示例：

```bash
curl http://127.0.0.1:19000/api/v1/publish/health
```

响应示例：

```json
{
  "status": "ok",
  "service": "publish"
}
```

### 2. 创建发文任务

```http
POST /api/v1/publish/publish
```

创建一条发文任务。接口会立即返回任务信息，不会阻塞等待真实发布完成。

请求字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `user_id` | string | 是 | 用户标识，用于隔离 Cookie |
| `platform` | string | 否 | 发布平台，默认 `toutiao` |
| `title` | string | 是 | 文章标题，最长 200 字符 |
| `content` | string | 是 | 文章正文，支持普通文本或 Markdown |
| `cover_image_url` | string/null | 否 | 封面图片 URL |

请求示例：

```bash
curl -X POST http://127.0.0.1:19000/api/v1/publish/publish \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user1",
    "platform": "toutiao",
    "title": "测试标题",
    "content": "文章正文",
    "cover_image_url": "https://example.com/cover.jpg"
  }'
```

如果该用户已有有效 Cookie，服务会创建后台发布任务：

```json
{
  "code": 200,
  "message": "任务创建成功，发布任务正在后台执行",
  "data": {
    "job_id": "xxxx",
    "task_status": "running",
    "query_url": "/api/v1/publish/jobs/xxxx",
    "login_url": "",
    "remote_session_id": "",
    "log_file_path": "logs/jobs/xxxx.log"
  }
}
```

如果该用户没有 Cookie 或 Cookie 失效，服务会返回远程登录链接：

```json
{
  "code": 200,
  "message": "任务创建成功，需要用户登录",
  "data": {
    "job_id": "xxxx",
    "task_status": "login_required",
    "query_url": "/api/v1/publish/jobs/xxxx",
    "login_url": "https://xxxxx.trycloudflare.com",
    "remote_session_id": "session-xxxx",
    "log_file_path": "logs/jobs/xxxx.log"
  }
}
```

### 3. 查询任务状态

```http
GET /api/v1/publish/jobs/{job_id}
```

查询发文任务当前状态和最终发布结果。

请求示例：

```bash
curl http://127.0.0.1:19000/api/v1/publish/jobs/{job_id}
```

常见状态：

| `code` | `task_status` | 说明 |
|---:|---|---|
| `200` | `published` | 发布成功 |
| `202` | `running` | 后台任务正在执行 |
| `401` | `login_required` | 需要用户完成远程登录 |
| `404` | `not_found` | 任务不存在 |
| `408` | `expired` | 远程登录 session 已过期 |
| `410` | `closed` | 远程登录 session 已关闭 |
| `500` | `failed` | 发布失败 |
| `503` | `query_failed` | 查询任务状态失败 |

发布成功响应示例：

```json
{
  "code": 200,
  "task_status": "published",
  "message": "文章发布成功",
  "data": {
    "job_id": "xxxx",
    "user_id": "user1",
    "platform": "toutiao",
    "title": "测试标题",
    "cover_image_url": "https://example.com/cover.jpg",
    "article_url": "https://www.toutiao.com/item/...",
    "publish_result": {
      "success": true,
      "account_name": "账号名称",
      "platform_user_id": "平台用户 ID",
      "article_title": "测试标题",
      "publish_signal": "",
      "operation_time": "2026-06-08 10:00:00"
    }
  }
}
```

需要登录时响应示例：

```json
{
  "code": 401,
  "task_status": "login_required",
  "message": "需要用户登录或 Cookie 已失效",
  "data": {
    "job_id": "xxxx",
    "login_url": "https://xxxxx.trycloudflare.com"
  }
}
```

### 4. 保存远程登录 Cookie

```http
POST /api/v1/publish/savecookie/{job_id}
```

当创建任务返回 `login_url` 后，用户打开链接完成扫码登录。调用该接口后，服务会从当前远程浏览器 session 中主动提取 Cookie，保存到 `data/cookies/{platform}/{user_id}.json`，然后继续原发文任务。

请求示例：

```bash
curl -X POST http://127.0.0.1:19000/api/v1/publish/savecookie/{job_id}
```

成功响应示例：

```json
{
  "code": 200,
  "job_id": "xxxx",
  "status": "succeeded",
  "message": "Cookie 保存成功，发布任务已继续执行",
  "login_url": "",
  "log_file_path": "logs/jobs/xxxx.log",
  "result": {
    "cookie_count": 8,
    "query_url": "/api/v1/publish/jobs/xxxx"
  }
}
```

常见失败或跳过：

| `code` | 说明 |
|---:|---|
| `404` | 任务或远程登录 session 不存在 |
| `409` | 任务没有远程登录 session，或 Cookie 已保存，或任务已进入发布流程 |
| `500` | 提取 Cookie 或继续发布时异常 |

### 5. 终止任务和清理账号数据

```http
POST /api/v1/publish/cancel
POST /api/v1/publish/cleanup
```

两个接口都使用相同请求体，并且需要 `Authorization: Bearer <PUBLISH_API_TOKEN>`：

```json
{
  "user_id": "user1",
  "platform": "toutiao"
}
```

- `cancel`：终止指定用户和平台当前正在执行或等待登录的最新任务，任务会变为 `failed`，失败原因是 `任务已被用户终止`。该接口不会删除 Cookie。
- `cleanup`：替代旧的 `clearcookie`。如果存在当前任务，会先执行终止逻辑，然后删除 `data/cookies/{platform}/{user_id}.json`，并尝试关闭关联的远程登录 session。
- `/api/v1/publish/clearcookie` 已删除，不再作为兼容入口保留。

## 运行期目录

- `data/cookies/{platform}/{user_id}.json`：长期保存用户 Cookie
- `data/remote_profiles/{session_id}/`：远程登录临时浏览器 profile
- `data/chrome_profile/`：发文内核浏览器 profile
- `logs/jobs/`：任务编排日志
- `logs/requests/`：发文内核请求日志
