# Auto 自动化发文服务接口文档

## 1. 服务说明

`auto` 服务是头条自动化发文的一体化入口。调用方只需要提交一次发文任务，服务会自动完成：

- 创建发文任务
- 按 `user_id` 检查用户 Cookie
- Cookie 有效时直接后台发文
- Cookie 缺失或失效时启动远程登录
- 用户登录后保存 Cookie
- 继续执行原发文任务
- 查询任务进度和发布结果

## 2. 基础信息

### 本地开发地址

```text
http://127.0.0.1:19000
```

### 服务器部署地址示例

```text
http://服务器IP:端口
```

例如当前服务器如果映射到 `8000`：

```text
http://47.242.205.13:8000
```

### OpenAPI 文档

```text
GET /docs
```

完整地址示例：

```text
http://127.0.0.1:19000/docs
```

### 接口前缀

```text
/api/v1/auto
```

## 3. 通用约定

### HTTP 状态码

当前接口一般使用 HTTP `200` 返回业务结果。前端主要看响应体里的 `code` 和 `task_status`。

### `code` 说明

| code | 含义 | 常见接口 |
|---:|---|---|
| `200` | 成功；任务创建成功或发布成功 | `publish`、`jobs`、`savecookie` |
| `202` | 任务执行中 | `jobs` |
| `401` | 需要用户登录 | `jobs` |
| `404` | 任务或远程登录 session 不存在 | `jobs`、`savecookie`、`jobs/{job_id}/cookies` |
| `408` | 预留：远程登录 session 已过期 | `jobs` |
| `409` | 重复保存 Cookie、任务已进入发布流程、无可保存 session | `savecookie` |
| `410` | 预留：远程登录 session 已关闭 | `jobs` |
| `500` | 任务创建失败或发布失败 | `publish`、`jobs`、`savecookie` |
| `503` | 查询任务状态失败 | `jobs` |

### `task_status` 说明

| task_status | 含义 | 前端建议 |
|---|---|---|
| `running` | 后台执行中 | 继续轮询任务查询接口 |
| `login_required` | 需要用户登录 | 展示 `login_url`，引导用户扫码登录 |
| `published` | 发布成功 | 展示文章信息和 `article_url` |
| `failed` | 发布失败 | 展示失败原因 |
| `not_found` | 任务不存在 | 提示任务不存在或刷新列表 |
| `expired` | 远程登录 session 过期 | 预留状态，后续可重新发起任务 |
| `closed` | 远程登录 session 关闭 | 预留状态，后续可重新发起任务 |
| `query_failed` | 查询任务失败 | 提示稍后重试 |

## 4. 接口列表

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/auto/health` | 健康检查 |
| `POST` | `/api/v1/auto/publish` | 创建自动发文任务 |
| `GET` | `/api/v1/auto/jobs/{job_id}` | 查询任务状态和发布结果 |
| `POST` | `/api/v1/auto/savecookie/{job_id}` | 主动保存远程登录 Cookie 并继续发文 |
| `POST` | `/api/v1/auto/jobs/{job_id}/cookies` | 手动提交 Cookie，调试接口 |

## 5. 健康检查

### 请求

```http
GET /api/v1/auto/health
```

### 返回示例

```json
{
  "status": "ok",
  "service": "auto"
}
```

### 前端用途

用于判断服务是否启动成功。

## 6. 创建自动发文任务

### 请求

```http
POST /api/v1/auto/publish
Content-Type: application/json
```

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `user_id` | string | 是 | 用户 ID，用于隔离 Cookie。不能为空 |
| `platform` | string | 否 | 发布平台，默认 `toutiao` |
| `title` | string | 是 | 文章标题，不能为空，最长 200 字符 |
| `content` | string | 是 | 文章正文，不能为空 |
| `cover_image_url` | string/null | 否 | 封面图片 URL |

### 请求示例

```json
{
  "user_id": "user1",
  "platform": "toutiao",
  "title": "测试标题",
  "content": "这里是文章正文内容。",
  "cover_image_url": "https://example.com/cover.jpg"
}
```

### 返回字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | number | 任务创建结果。当前只按 `200` 成功、`500` 失败处理 |
| `message` | string | 创建结果说明 |
| `data.job_id` | string | 任务 ID |
| `data.task_status` | string | 创建后的任务状态 |
| `data.query_url` | string | 查询任务状态的接口地址 |
| `data.login_url` | string | 远程登录地址。无需登录时为空字符串 |
| `data.remote_session_id` | string | 远程登录 session ID。无需登录时为空字符串 |
| `data.log_file_path` | string | 当前任务日志文件路径 |
| `data.reason` | string | 创建失败原因，仅失败时可能存在 |

### 返回示例：Cookie 存在，后台发文

```json
{
  "code": 200,
  "message": "任务创建成功，发布任务正在后台执行",
  "data": {
    "job_id": "job-1",
    "task_status": "running",
    "query_url": "/api/v1/auto/jobs/job-1",
    "login_url": "",
    "remote_session_id": "",
    "log_file_path": "auto/logs/jobs/job-1.log"
  }
}
```

### 返回示例：需要用户登录

```json
{
  "code": 200,
  "message": "任务创建成功，需要用户登录",
  "data": {
    "job_id": "job-1",
    "task_status": "login_required",
    "query_url": "/api/v1/auto/jobs/job-1",
    "login_url": "https://xxxxx.trycloudflare.com",
    "remote_session_id": "session-1",
    "log_file_path": "auto/logs/jobs/job-1.log"
  }
}
```

### 返回示例：任务创建失败

```json
{
  "code": 500,
  "message": "任务创建失败",
  "data": {
    "job_id": "job-1",
    "task_status": "failed",
    "query_url": "/api/v1/auto/jobs/job-1",
    "login_url": "",
    "remote_session_id": "",
    "log_file_path": "auto/logs/jobs/job-1.log",
    "reason": "远程登录启动失败: 未找到 Chrome 或 Edge 浏览器"
  }
}
```

### 参数校验失败

如果缺少 `user_id`、`title`、`content`，FastAPI 会返回 HTTP `422`。

示例：

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "user_id"],
      "msg": "Field required"
    }
  ]
}
```

### 前端处理建议

- 收到 `code=200`：表示任务已创建，保存 `job_id`。
- `data.task_status=running`：开始轮询 `data.query_url`。
- `data.task_status=login_required`：展示 `data.login_url`，让用户扫码登录；同时继续轮询任务状态。
- 收到 `code=500`：任务创建失败，展示 `data.reason` 或 `message`。

## 7. 查询任务状态

### 请求

```http
GET /api/v1/auto/jobs/{job_id}
```

### 路径参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `job_id` | string | 是 | 创建任务接口返回的任务 ID |

### 返回字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | number | 业务状态码 |
| `task_status` | string | 任务状态 |
| `message` | string | 状态说明 |
| `data.job_id` | string | 任务 ID |

不同状态下 `data` 会包含不同字段。

### 返回示例：执行中

```json
{
  "code": 202,
  "task_status": "running",
  "message": "发布任务正在后台执行",
  "data": {
    "job_id": "job-1"
  }
}
```

### 返回示例：需要登录

```json
{
  "code": 401,
  "task_status": "login_required",
  "message": "需要用户登录或 Cookie 已失效",
  "data": {
    "job_id": "job-1",
    "login_url": "https://xxxxx.trycloudflare.com"
  }
}
```

### 返回示例：发布成功

```json
{
  "code": 200,
  "task_status": "published",
  "message": "文章发布成功",
  "data": {
    "job_id": "job-1",
    "user_id": "user1",
    "platform": "toutiao",
    "title": "测试标题",
    "cover_image_url": "https://example.com/cover.jpg",
    "article_url": "https://www.toutiao.com/item/1/",
    "publish_result": {
      "success": true,
      "account_name": "账号名",
      "platform_user_id": "platform-user-1",
      "article_title": "测试标题",
      "publish_signal": "post_publish_verification",
      "operation_time": "2026-06-05 10:30:00"
    }
  }
}
```

### 发布成功字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `data.user_id` | string | 业务侧传入的用户 ID |
| `data.platform` | string | 发布平台 |
| `data.title` | string | 业务侧传入的文章标题 |
| `data.cover_image_url` | string | 业务侧传入的封面 URL，没有则为空字符串 |
| `data.article_url` | string | 文章 URL。审核中 preview 链接会被归一化为头条文章链接 |
| `data.publish_result.success` | boolean | 发文服务是否成功 |
| `data.publish_result.account_name` | string | 平台账号名称 |
| `data.publish_result.platform_user_id` | string | 平台用户 ID |
| `data.publish_result.article_title` | string | 发文服务识别到的文章标题 |
| `data.publish_result.publish_signal` | string | 发布成功信号 |
| `data.publish_result.operation_time` | string | 操作时间 |

### 返回示例：发布成功但未获取到文章链接

```json
{
  "code": 200,
  "task_status": "published",
  "message": "文章发布成功，但未获取到文章链接",
  "data": {
    "job_id": "job-1",
    "user_id": "user1",
    "platform": "toutiao",
    "title": "测试标题",
    "cover_image_url": "",
    "article_url": "",
    "publish_result": {
      "success": true,
      "account_name": "",
      "platform_user_id": "",
      "article_title": "测试标题",
      "publish_signal": "",
      "operation_time": ""
    }
  }
}
```

### 返回示例：发布失败

```json
{
  "code": 500,
  "task_status": "failed",
  "message": "发布失败",
  "data": {
    "job_id": "job-1",
    "reason": "浏览器异常"
  }
}
```

### 返回示例：任务不存在

```json
{
  "code": 404,
  "task_status": "not_found",
  "message": "任务不存在",
  "data": {
    "job_id": "missing-job"
  }
}
```

### 返回示例：远程登录 session 过期

```json
{
  "code": 408,
  "task_status": "expired",
  "message": "浏览器 session 已过期",
  "data": {
    "job_id": "job-1"
  }
}
```

### 返回示例：远程登录 session 已关闭

```json
{
  "code": 410,
  "task_status": "closed",
  "message": "浏览器 session 已关闭",
  "data": {
    "job_id": "job-1"
  }
}
```

### 返回示例：查询失败

```json
{
  "code": 503,
  "task_status": "query_failed",
  "message": "查询任务状态失败",
  "data": {
    "job_id": "job-1"
  }
}
```

### 前端轮询建议

- 创建任务成功后，每 2 到 5 秒调用一次 `GET /api/v1/auto/jobs/{job_id}`。
- `task_status=running`：继续轮询。
- `task_status=login_required`：展示登录链接，并继续轮询。
- `task_status=published`：停止轮询，展示发布结果。
- `task_status=failed/not_found/expired/closed/query_failed`：停止轮询或提示用户重试。

## 8. 主动保存远程登录 Cookie

### 请求

```http
POST /api/v1/auto/savecookie/{job_id}
```

### 使用场景

创建任务后，如果返回 `login_url`，用户打开远程登录页面并扫码登录。默认情况下，用户关闭远程连接时服务会自动保存 Cookie。  
如果前端希望用户登录完成后主动点击“我已登录”按钮，可以调用此接口立即保存 Cookie，并继续原发文任务。

### 路径参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `job_id` | string | 是 | 创建任务接口返回的任务 ID |

### 返回字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | number | 保存结果 |
| `job_id` | string | 任务 ID |
| `status` | string | 当前任务状态 |
| `message` | string | 保存结果说明 |
| `login_url` | string | 当前为空字符串 |
| `log_file_path` | string | 任务日志路径 |
| `result.cookie_count` | number | 保存到的 Cookie 数量 |
| `result.query_url` | string | 查询任务状态的接口 |

### 返回示例：保存成功

```json
{
  "code": 200,
  "job_id": "job-1",
  "status": "succeeded",
  "message": "Cookie 保存成功，发布任务已继续执行",
  "login_url": "",
  "log_file_path": "auto/logs/jobs/job-1.log",
  "result": {
    "cookie_count": 8,
    "query_url": "/api/v1/auto/jobs/job-1"
  }
}
```

### 返回示例：重复调用或任务已进入发布流程

```json
{
  "code": 409,
  "job_id": "job-1",
  "status": "publishing",
  "message": "cookie already saved",
  "login_url": "",
  "log_file_path": "auto/logs/jobs/job-1.log",
  "result": {}
}
```

### 返回示例：任务不存在

```json
{
  "code": 404,
  "job_id": "job-1",
  "status": "failed",
  "message": "job not found",
  "login_url": "",
  "log_file_path": "",
  "result": {}
}
```

### 返回示例：任务没有远程登录 session

```json
{
  "code": 409,
  "job_id": "job-1",
  "status": "running",
  "message": "job has no remote login session",
  "login_url": "",
  "log_file_path": "auto/logs/jobs/job-1.log",
  "result": {}
}
```

### 返回示例：远程登录 session 不存在

```json
{
  "code": 404,
  "job_id": "job-1",
  "status": "failed",
  "message": "remote login session not found",
  "login_url": "",
  "log_file_path": "auto/logs/jobs/job-1.log",
  "result": {}
}
```

### 幂等说明

该接口已做保护：

- job 已经进入 `publishing` 或 `succeeded` 时，不会重复发文。
- 同一个远程登录 session 完成一次后，再次触发会被跳过。
- 如果先调用 `savecookie`，随后用户又关闭远程连接，不会再次保存 Cookie 或重复发文。

## 9. 手动提交 Cookie 回调

### 请求

```http
POST /api/v1/auto/jobs/{job_id}/cookies
Content-Type: application/json
```

### 使用场景

该接口主要用于调试。正常业务流程不需要前端调用。

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `cookies` | array | 是 | Cookie 列表，至少 1 条 |

### 请求示例

```json
{
  "cookies": [
    {
      "name": "sessionid",
      "value": "xxx",
      "domain": ".toutiao.com",
      "path": "/"
    }
  ]
}
```

### 返回说明

返回结构与 `savecookie` 类似，底层会保存 Cookie 并继续发文。

## 10. 推荐前端流程

### 10.1 创建任务

调用：

```text
POST /api/v1/auto/publish
```

拿到：

```text
data.job_id
data.query_url
data.task_status
data.login_url
```

### 10.2 判断是否需要登录

- `task_status=running`：直接进入轮询。
- `task_status=login_required`：展示 `login_url`，提示用户扫码登录。

### 10.3 登录后主动保存 Cookie

用户扫码登录完成后，可以让用户点击“我已登录”，调用：

```text
POST /api/v1/auto/savecookie/{job_id}
```

调用后继续轮询：

```text
GET /api/v1/auto/jobs/{job_id}
```

### 10.4 查询最终结果

直到：

- `task_status=published`：成功，展示 `article_url`。
- `task_status=failed`：失败，展示 `data.reason`。

## 11. Curl 示例

### 创建任务

```bash
curl -X POST "http://127.0.0.1:19000/api/v1/auto/publish" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user1",
    "platform": "toutiao",
    "title": "测试标题",
    "content": "这里是文章正文内容。",
    "cover_image_url": "https://example.com/cover.jpg"
  }'
```

### 查询任务

```bash
curl "http://127.0.0.1:19000/api/v1/auto/jobs/job-1"
```

### 主动保存 Cookie

```bash
curl -X POST "http://127.0.0.1:19000/api/v1/auto/savecookie/job-1"
```

### 健康检查

```bash
curl "http://127.0.0.1:19000/api/v1/auto/health"
```

## 12. PowerShell 示例

### 创建任务

```powershell
$body = @{
  user_id = "user1"
  platform = "toutiao"
  title = "测试标题"
  content = "这里是文章正文内容。"
  cover_image_url = "https://example.com/cover.jpg"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:19000/api/v1/auto/publish" `
  -ContentType "application/json" `
  -Body $body
```

### 查询任务

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://127.0.0.1:19000/api/v1/auto/jobs/job-1"
```

### 主动保存 Cookie

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:19000/api/v1/auto/savecookie/job-1"
```

## 13. 注意事项

- `user_id` 必传，并且不能为空。
- `POST /api/v1/auto/publish` 只是创建任务，不代表发布已经完成。
- 发布进度和最终结果必须通过 `GET /api/v1/auto/jobs/{job_id}` 查询。
- 远程登录建议使用头条 App 扫码登录，手机号验证码登录容易触发滑块。
- 生产环境建议给接口增加鉴权，或仅允许内网访问。
- 当前任务和远程登录 session 是内存态，Docker 部署不要开启多个 uvicorn worker。
- Cookie 和日志会写入运行期目录，Docker 部署时需要挂载 volume 持久化。
