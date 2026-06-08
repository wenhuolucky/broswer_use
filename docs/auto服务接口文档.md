# Auto 自动化发文服务接口文档

## 1. 服务说明

`auto` 服务是头条自动化发文的一体化入口。调用方提交一次发文任务后，服务会自动完成：

- 创建发文任务
- 按 `user_id` 检查用户 Cookie
- Cookie 有效时直接后台发文
- Cookie 缺失或失效时启动远程登录
- 用户登录后保存 Cookie
- 继续执行原发文任务
- 查询任务进度和发布结果
- 支持只登录获取或刷新 Cookie，不触发发文

## 2. 基础信息

### 服务地址

本地开发：

```text
http://127.0.0.1:19000
```

服务器部署示例：

```text
http://47.242.205.13:8000
```

### 接口前缀

```text
/api/v1/auto
```

### OpenAPI 文档

```text
GET /docs
```

## 3. 接口鉴权

所有业务接口都必须携带我们认可的 `key`，否则不能进入服务。

### 推荐鉴权方式

统一使用请求头：

```http
X-API-Key: your-api-key
```

### 适用接口

以下接口都必须携带 `X-API-Key`：

| 方法 | 路径 |
|---|---|
| `GET` | `/api/v1/auto/health` |
| `POST` | `/api/v1/auto/login` |
| `POST` | `/api/v1/auto/publish` |
| `GET` | `/api/v1/auto/jobs/{job_id}` |
| `POST` | `/api/v1/auto/savecookie/{job_id}` |
| `POST` | `/api/v1/auto/jobs/{job_id}/cookies` |

### 鉴权失败

如果没有传 `X-API-Key`，或传入的 key 不在服务认可列表中，接口应直接拒绝请求。

建议返回 HTTP `401`：

```json
{
  "code": 401,
  "message": "无效的 API Key"
}
```

说明：

- 鉴权失败属于接口入口校验，不创建任务。
- 鉴权失败不返回 `job_id`。
- 鉴权失败和任务查询中的 `task_status=login_required` 不是同一个含义。

## 4. 通用约定

### HTTP 状态码

业务接口正常进入服务后，一般使用 HTTP `200` 返回业务结果。前端主要看响应体里的 `code` 和 `task_status`。

鉴权失败可以使用 HTTP `401` 直接返回。

### 业务 `code` 说明

| code | 含义 | 常见接口 |
|---:|---|---|
| `200` | 成功；任务创建成功或发布成功且拿到文章 URL | `publish`、`jobs`、`savecookie` |
| `202` | 任务执行中 | `jobs` |
| `212` | 文章发布成功，但未获取到文章 URL | `jobs` |
| `401` | 需要用户登录；或鉴权失败时表示 API Key 无效 | `jobs`、全部鉴权接口 |
| `404` | 任务或远程登录 session 不存在 | `jobs`、`savecookie`、`jobs/{job_id}/cookies` |
| `408` | 远程登录 session 已过期 | `jobs` |
| `409` | 重复保存 Cookie、任务已进入发布流程、无可保存 session | `savecookie` |
| `410` | 远程登录 session 已关闭 | `jobs` |
| `500` | 任务创建失败或发布失败 | `publish`、`jobs`、`savecookie` |
| `503` | 查询任务状态失败 | `jobs` |

### `task_status` 说明

| task_status | 含义 | 前端建议 |
|---|---|---|
| `running` | 后台执行中 | 继续轮询任务查询接口 |
| `login_required` | 需要用户登录 | 展示 `live_url`，引导用户扫码登录并观看远程浏览器 |
| `cookie_saved` | 只登录任务已保存 Cookie | 停止轮询，提示登录态已保存 |
| `published` | 发布成功 | 展示发布结果 |
| `published_without_url` | 发布成功，但未获取到文章 URL | 展示发布成功，同时提示链接暂未获取 |
| `failed` | 发布失败 | 展示失败原因 |
| `not_found` | 任务不存在 | 提示任务不存在 |
| `expired` | 远程登录 session 过期 | 提示用户重新发起任务 |
| `closed` | 远程登录 session 已关闭 | 提示用户重新发起任务 |
| `query_failed` | 查询任务失败 | 提示稍后重试 |

## 5. 接口列表

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/auto/health` | 健康检查 |
| `POST` | `/api/v1/auto/login` | 创建只登录任务，只获取或刷新 Cookie，不发文 |
| `POST` | `/api/v1/auto/publish` | 创建自动发文任务 |
| `GET` | `/api/v1/auto/jobs/{job_id}` | 查询任务状态和发布结果 |
| `POST` | `/api/v1/auto/savecookie/{job_id}` | 主动保存远程登录 Cookie 并继续发文 |
| `POST` | `/api/v1/auto/jobs/{job_id}/cookies` | 手动提交 Cookie，调试接口 |

## 6. 健康检查

### 请求

```http
GET /api/v1/auto/health
X-API-Key: your-api-key
```

### 返回示例

```json
{
  "status": "ok",
  "service": "auto"
}
```

## 7. 创建只登录任务

### 请求

```http
POST /api/v1/auto/login
Content-Type: application/json
X-API-Key: your-api-key
```

### 使用场景

该接口只用于让用户完成远程登录并保存 Cookie，不会触发自动发文。适合以下场景：

- 新用户首次授权登录
- 用户 Cookie 过期后提前刷新登录态
- 运维或客服协助用户单独完成账号登录
- 发文前预热登录态，减少正式发文时等待登录的时间

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `user_id` | string | 是 | 用户 ID，用于隔离 Cookie。不能为空 |
| `platform` | string | 否 | 登录平台，默认 `toutiao` |
| `force_refresh` | boolean | 否 | 是否强制重新登录并刷新 Cookie。默认 `true` |

### 请求示例

```json
{
  "user_id": "user1",
  "platform": "toutiao",
  "force_refresh": true
}
```

### 返回字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | number | 任务创建结果。`200` 成功，`500` 失败 |
| `message` | string | 创建结果说明 |
| `data.job_id` | string | 登录任务 ID |
| `data.task_status` | string | 登录任务状态 |
| `data.query_url` | string | 查询登录任务状态的接口地址 |
| `data.live_url` | string | 远程实时查看链接，用户通过该链接扫码登录并观看浏览器 |
| `data.reason` | string | 创建失败原因，仅失败时可能存在 |

### 返回示例：需要用户登录

```json
{
  "code": 200,
  "message": "登录任务创建成功，需要用户登录",
  "data": {
    "job_id": "login-job-1",
    "task_status": "login_required",
    "query_url": "/api/v1/auto/jobs/login-job-1",
    "live_url": "https://xxxxx.trycloudflare.com"
  }
}
```

### 返回示例：已有有效 Cookie 且不强制刷新

当 `force_refresh=false` 且用户已有有效 Cookie 时，可以直接返回：

```json
{
  "code": 200,
  "message": "登录态已存在",
  "data": {
    "job_id": "login-job-1",
    "task_status": "cookie_saved",
    "query_url": "/api/v1/auto/jobs/login-job-1",
    "live_url": ""
  }
}
```

### 返回示例：登录任务创建失败

```json
{
  "code": 500,
  "message": "登录任务创建失败",
  "data": {
    "job_id": "login-job-1",
    "task_status": "failed",
    "query_url": "/api/v1/auto/jobs/login-job-1",
    "live_url": "",
    "reason": "远程登录启动失败: 未找到 Chrome 或 Edge 浏览器"
  }
}
```

### 前端处理建议

- 收到 `task_status=login_required`：展示 `live_url`，提示用户扫码登录。
- 用户扫码完成后，可以调用 `POST /api/v1/auto/savecookie/{job_id}` 主动保存 Cookie。
- 保存成功后继续轮询 `query_url`，直到 `task_status=cookie_saved`。
- 只登录任务不会调用发文服务，也不会生成文章。

## 8. 创建自动发文任务

### 请求

```http
POST /api/v1/auto/publish
Content-Type: application/json
X-API-Key: your-api-key
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
| `code` | number | 任务创建结果。`200` 成功，`500` 失败 |
| `message` | string | 创建结果说明 |
| `data.job_id` | string | 任务 ID |
| `data.task_status` | string | 创建后的任务状态 |
| `data.query_url` | string | 查询任务状态的接口地址 |
| `data.live_url` | string | 远程实时查看链接。无论是否需要登录都应返回，便于用户全程观看浏览器操作 |
| `data.reason` | string | 创建失败原因，仅失败时可能存在 |

说明：`POST /api/v1/auto/publish` 是任务创建接口，只表示服务成功接收请求并创建任务，不表示文章已经发布完成。

### 返回示例：Cookie 存在，后台发文

```json
{
  "code": 200,
  "message": "任务创建成功，发布任务正在后台执行",
  "data": {
    "job_id": "job-1",
    "task_status": "running",
    "query_url": "/api/v1/auto/jobs/job-1",
    "live_url": "https://xxxxx.trycloudflare.com"
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
    "live_url": "https://xxxxx.trycloudflare.com"
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
    "live_url": "",
    "reason": "远程登录启动失败: 未找到 Chrome 或 Edge 浏览器"
  }
}
```

### 参数校验失败

如果缺少 `user_id`、`title`、`content`，FastAPI 会返回 HTTP `422`。

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

- 收到 `code=200`：保存 `job_id`。
- `data.task_status=running`：开始轮询 `data.query_url`。
- `data.live_url`：展示远程实时查看链接，让用户全程观看登录和发文过程。
- `data.task_status=login_required`：提示用户在 `data.live_url` 中扫码登录；同时继续轮询任务状态。
- 收到 `code=500`：任务创建失败，展示 `data.reason` 或 `message`。

## 9. 查询任务状态

### 请求

```http
GET /api/v1/auto/jobs/{job_id}
X-API-Key: your-api-key
```

### 路径参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `job_id` | string | 是 | 创建任务接口返回的任务 ID |

### 通用返回字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | number | 业务状态码 |
| `task_status` | string | 任务状态 |
| `message` | string | 状态说明 |
| `data.job_id` | string | 任务 ID |
| `data.live_url` | string | 远程实时查看链接。任务执行中或等待登录时返回 |

### 返回示例：执行中

```json
{
  "code": 202,
  "task_status": "running",
  "message": "发布任务正在后台执行",
  "data": {
    "job_id": "job-1",
    "live_url": "https://xxxxx.trycloudflare.com"
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
    "live_url": "https://xxxxx.trycloudflare.com"
  }
}
```

### 返回示例：只登录任务已保存 Cookie

```json
{
  "code": 200,
  "task_status": "cookie_saved",
  "message": "Cookie 保存成功",
  "data": {
    "job_id": "login-job-1",
    "user_id": "user1",
    "platform": "toutiao"
  }
}
```

### 返回示例：发布成功并获取到文章链接

```json
{
  "code": 200,
  "task_status": "published",
  "message": "文章发布成功",
  "data": {
    "job_id": "job-1",
    "user_id": "user1",
    "platform": "toutiao",
    "publish_result": {
      "account_name": "账号名",
      "platform_user_id": "platform-user-1",
      "article_title": "测试标题",
      "article_url": "https://www.toutiao.com/item/1/",
      "publish_signal": "post_publish_verification",
      "operation_time": "2026-06-05 10:30:00"
    }
  }
}
```

### 发布成功字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `data.job_id` | string | 任务 ID |
| `data.user_id` | string | 业务侧传入的用户 ID |
| `data.platform` | string | 发布平台 |
| `data.publish_result.account_name` | string | 平台账号名称 |
| `data.publish_result.platform_user_id` | string | 平台用户 ID |
| `data.publish_result.article_title` | string | 发文服务识别到的文章标题 |
| `data.publish_result.article_url` | string | 文章 URL。审核中 preview 链接会被归一化为头条文章链接 |
| `data.publish_result.publish_signal` | string | 发布成功信号 |
| `data.publish_result.operation_time` | string | 操作时间 |

### 返回示例：发布成功但未获取到文章链接

```json
{
  "code": 212,
  "task_status": "published_without_url",
  "message": "文章发布成功，但未获取到文章链接",
  "data": {
    "job_id": "job-1",
    "user_id": "user1",
    "platform": "toutiao",
    "publish_result": {
      "account_name": "",
      "platform_user_id": "",
      "article_title": "测试标题",
      "article_url": "",
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
- `task_status=login_required`：展示 `live_url`，提示用户扫码登录，并继续轮询。
- `task_status=cookie_saved`：只登录任务完成，停止轮询。
- `task_status=published`：停止轮询，展示发布结果。
- `task_status=published_without_url`：停止轮询，展示发布成功，同时提示文章链接暂未获取。
- `task_status=failed/not_found/expired/closed/query_failed`：停止轮询或提示用户重试。

## 10. 主动保存远程登录 Cookie

### 请求

```http
POST /api/v1/auto/savecookie/{job_id}
X-API-Key: your-api-key
```

### 使用场景

创建登录任务或发文任务后，如果返回 `live_url`，用户打开远程实时查看页面并扫码登录。默认情况下，用户关闭远程连接时服务会自动保存 Cookie。

如果前端希望用户登录完成后主动点击“我已登录”，可以调用此接口立即保存 Cookie，并继续原发文任务。

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
| `live_url` | string | 远程实时查看链接。无可用链接时为空字符串 |
| `log_file_path` | string | 任务日志路径，后续可按前端需要隐藏 |
| `result.cookie_count` | number | 保存到的 Cookie 数量 |
| `result.query_url` | string | 查询任务状态的接口 |

### 返回示例：保存成功并继续原任务

```json
{
  "code": 200,
  "job_id": "job-1",
  "status": "succeeded",
  "message": "Cookie 保存成功，发布任务已继续执行",
  "live_url": "https://xxxxx.trycloudflare.com",
  "log_file_path": "auto/logs/jobs/job-1.log",
  "result": {
    "cookie_count": 8,
    "query_url": "/api/v1/auto/jobs/job-1"
  }
}
```

说明：

- 如果原任务是发文任务，保存 Cookie 后继续发文。
- 如果原任务是只登录任务，保存 Cookie 后任务结束，不会发文。

### 返回示例：重复调用或任务已进入发布流程

```json
{
  "code": 409,
  "job_id": "job-1",
  "status": "publishing",
  "message": "cookie already saved",
  "live_url": "https://xxxxx.trycloudflare.com",
  "log_file_path": "auto/logs/jobs/job-1.log",
  "result": {}
}
```

### 幂等说明

该接口已做保护：

- job 已经进入 `publishing` 或 `succeeded` 时，不会重复发文。
- 同一个远程登录 session 完成一次后，再次触发会被跳过。
- 如果先调用 `savecookie`，随后用户又关闭远程连接，不会再次保存 Cookie 或重复发文。

## 11. 手动提交 Cookie 回调

### 请求

```http
POST /api/v1/auto/jobs/{job_id}/cookies
Content-Type: application/json
X-API-Key: your-api-key
```

### 使用场景

该接口主要用于调试。正常业务流程不建议前端调用。

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

## 12. 远程登录超时和特殊情况

### 用户一直不登录

当 `publish` 或 `jobs/{job_id}` 返回 `login_required` 后，前端应展示 `live_url`，并提示用户尽快扫码登录。

建议约定：

- 远程登录 session 默认保留 `3 分钟`。
- 用户超过 `3 分钟` 没有登录，服务可以关闭远程登录 session。
- 关闭后任务查询接口返回 `code=408`、`task_status=expired`。
- 前端收到 `expired` 后停止轮询，并提示用户重新创建任务。

### 用户关闭远程登录页面

如果用户关闭远程登录页面：

- 如果已经存在有效 Cookie，服务保存 Cookie 并继续发文。
- 如果没有有效 Cookie，任务查询接口可以返回 `code=410`、`task_status=closed`。
- 前端收到 `closed` 后停止轮询，并提示用户重新创建任务或重新打开远程查看链接。

### 用户登录后没有点击“我已登录”

当前支持两种保存 Cookie 触发方式：

- 用户关闭远程连接，服务自动保存 Cookie。
- 前端调用 `POST /api/v1/auto/savecookie/{job_id}` 主动保存 Cookie。

推荐前端提供“我已登录”按钮，用户扫码完成后主动调用 `savecookie`，这样不需要等待用户关闭远程连接。

### Cookie 过期或失效

如果本地已有 Cookie，但发文过程中发现未登录、登录页跳转或 Cookie 失效，服务会重新启动远程登录，任务状态回到 `login_required`，并返回新的 `live_url`。

## 13. 推荐前端流程

### 13.1 只登录流程

调用：

```text
POST /api/v1/auto/login
```

拿到：

```text
data.job_id
data.query_url
data.task_status
data.live_url
```

前端处理：

- `task_status=login_required`：展示 `live_url`，让用户扫码登录。
- 用户登录完成后，调用 `POST /api/v1/auto/savecookie/{job_id}`。
- 继续轮询 `GET /api/v1/auto/jobs/{job_id}`。
- `task_status=cookie_saved`：登录态保存成功，流程结束。
- 该流程不发文章。

### 13.2 创建发文任务

调用：

```text
POST /api/v1/auto/publish
```

请求头：

```http
X-API-Key: your-api-key
```

拿到：

```text
data.job_id
data.query_url
data.task_status
data.live_url
```

### 13.3 判断是否需要登录

- `task_status=running`：展示 `live_url`，让用户观看自动化发文过程，并进入轮询。
- `task_status=login_required`：展示 `live_url`，提示用户扫码登录，并进入轮询。

### 13.4 登录后主动保存 Cookie

用户扫码登录完成后，可以让用户点击“我已登录”，调用：

```text
POST /api/v1/auto/savecookie/{job_id}
```

调用后继续轮询：

```text
GET /api/v1/auto/jobs/{job_id}
```

### 13.5 查询最终结果

直到：

- `task_status=published`：成功，展示 `publish_result.article_url`。
- `task_status=published_without_url`：发布成功，但没有文章链接。
- `task_status=failed`：失败，展示 `data.reason`。
- `task_status=expired/closed`：登录 session 已不可用，提示用户重新创建任务。

## 14. Curl 示例

### 创建只登录任务

```bash
curl -X POST "http://127.0.0.1:19000/api/v1/auto/login" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "user_id": "user1",
    "platform": "toutiao",
    "force_refresh": true
  }'
```

### 创建任务

```bash
curl -X POST "http://127.0.0.1:19000/api/v1/auto/publish" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
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
curl "http://127.0.0.1:19000/api/v1/auto/jobs/job-1" \
  -H "X-API-Key: your-api-key"
```

### 主动保存 Cookie

```bash
curl -X POST "http://127.0.0.1:19000/api/v1/auto/savecookie/job-1" \
  -H "X-API-Key: your-api-key"
```

### 健康检查

```bash
curl "http://127.0.0.1:19000/api/v1/auto/health" \
  -H "X-API-Key: your-api-key"
```

## 15. PowerShell 示例

### 创建只登录任务

```powershell
$body = @{
  user_id = "user1"
  platform = "toutiao"
  force_refresh = $true
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:19000/api/v1/auto/login" `
  -Headers @{ "X-API-Key" = "your-api-key" } `
  -ContentType "application/json" `
  -Body $body
```

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
  -Headers @{ "X-API-Key" = "your-api-key" } `
  -ContentType "application/json" `
  -Body $body
```

### 查询任务

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://127.0.0.1:19000/api/v1/auto/jobs/job-1" `
  -Headers @{ "X-API-Key" = "your-api-key" }
```

### 主动保存 Cookie

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:19000/api/v1/auto/savecookie/job-1" `
  -Headers @{ "X-API-Key" = "your-api-key" }
```

## 16. 注意事项

- 所有业务接口必须携带 `X-API-Key`。
- `user_id` 必传，并且不能为空。
- `POST /api/v1/auto/login` 只保存或刷新 Cookie，不触发发文。
- `POST /api/v1/auto/publish` 只是创建任务，不代表发布已经完成。
- 发布进度和最终结果必须通过 `GET /api/v1/auto/jobs/{job_id}` 查询。
- 远程登录建议使用头条 App 扫码登录，手机号验证码登录容易触发滑块。
- 建议远程登录 session 超时时间设为 `3 分钟`，超时后返回 `408 expired`。
- 当前任务和远程登录 session 是内存态，Docker 部署不要开启多个 uvicorn worker。
- Cookie 和日志会写入运行期目录，Docker 部署时需要挂载 volume 持久化。
