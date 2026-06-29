# Browser Publish Service API Reference

版本：`2.0.0`

本文档描述当前代码中对外可用的 HTTP API。业务接口统一位于 `/api/v1` 前缀下，除 `/health` 外均需要 Bearer Token 鉴权。

## 1. 通用约定

### 1.1 Base URL

本地默认：

```text
http://127.0.0.1:8833
```

生产环境以部署域名为准。文档中的示例使用：

```text
{BASE_URL}=http://127.0.0.1:8833
```

### 1.2 鉴权

除 `GET /health` 外，所有 `/api/v1/*` 接口都需要：

```http
Authorization: Bearer <PUBLISH_API_TOKEN>
```

`PUBLISH_API_TOKEN` 来自服务端环境变量。

鉴权失败：

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer
```

```json
{
  "detail": "unauthorized"
}
```

### 1.3 Content-Type

有请求体的接口使用：

```http
Content-Type: application/json
```

### 1.4 时间格式

所有时间字段均为字符串，使用 ISO 8601 格式，例如：

```text
2026-06-29T08:00:00+00:00
```

### 1.5 通用错误结构

FastAPI/业务错误通常返回：

```json
{
  "detail": "错误说明"
}
```

请求体或路径参数校验失败时返回 `422`，结构为 FastAPI 标准 validation error：

```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "title"],
      "msg": "String should have at least 1 character",
      "input": ""
    }
  ]
}
```

任务失败时，部分业务响应体内会出现 `error` 对象：

| 字段 | 类型 | 必填 | 取值 | 含义 |
|---|---|:---:|---|---|
| `code` | string | 是 | `remote_login_failed` / `cookie_invalid` / `job_failed` | 稳定错误码，供调用方分支处理 |
| `detail` | string | 是 | 任意字符串 | 人类可读的失败原因 |

`error.code` 说明：

| code | 含义 | 常见处理 |
|---|---|---|
| `remote_login_failed` | 远程登录浏览器或 cookie 保存流程失败 | 提示稍后重试或联系运维 |
| `cookie_invalid` | 登录态/cookie 失效，或平台要求重新登录 | 引导用户打开 `live_url` 重新登录 |
| `job_failed` | 通用任务失败，包括平台发布失败、服务重启导致运行中任务失效等 | 展示 `detail`，必要时重新提交任务 |

## 2. 核心概念与枚举

### 2.1 Platform

`platform` 是字符串枚举。

| 值 | 类型 | 含义 |
|---|---|---|
| `toutiao` | string | 今日头条/头条号 |
| `sohu` | string | 搜狐号 |

### 2.2 channel_id

`channel_id` 是服务签发的渠道句柄，用于表示一个已连接的平台账号。

类型：`string`

格式限制：

```regex
^[A-Za-z0-9_-]{1,64}$
```

含义：

- 一个 `channel_id` 绑定一个平台账号和一份 cookie。
- 发文接口只传 `channel_id`，不再传平台账号密码或 cookie。
- 同一 `channel_id` 多篇发文会串行排队。
- 不同 `channel_id` 的发文可并发执行。

### 2.3 Job status

`status` 是任务内部状态，类型为 `string`。当前可能值：

| 值 | 类型 | 适用资源 | 含义 | 是否终态 |
|---|---|---|---|:---:|
| `queued` | string | publish job | 发文任务已创建，正在等待同一 `channel_id` 前序任务完成 | 否 |
| `checking_cookie` | string | publish job | 已被队列调度器领取，正在检查 cookie 或准备执行 | 否 |
| `cookie_ready` | string | publish job | cookie 可用的中间状态，兼容保留 | 否 |
| `starting_remote_login` | string | publish/login | 正在启动远程登录浏览器 | 否 |
| `waiting_cookie` | string | publish/login | 等待用户在 `live_url` 完成登录；publish job 会继续占用同 channel 队列 | 否 |
| `publishing` | string | publish job | 正在自动化发文 | 否 |
| `succeeded` | string | publish/login | 任务成功完成；publish job 成功时通常有 `article_url` | 是 |
| `failed` | string | publish/login | 任务失败，查看 `error` | 是 |
| `cancelled` | string | publish/login | 任务被取消 | 是 |

### 2.4 Channel status

`ChannelResponse.status` 是渠道状态，类型为 `string`。

| 值 | 类型 | 含义 |
|---|---|---|
| `pending` | string | 登录会话已创建，但尚未绑定真实平台账号 |
| `bound` | string | 已绑定平台账号，可用于发文 |
| `invalid` | string | 渠道存在但 cookie 可能失效 |

### 2.5 Channel publish status

`account_status` 是账号发文状态，类型为 `string`。

| 值 | 类型 | 含义 |
|---|---|---|
| `idle` | string | 当前 `channel_id` 没有未完成 publish job |
| `publishing` | string | 当前 `channel_id` 有未完成 publish job，可能正在发文、等待登录或排队 |

`publish_count` 是整数，表示该 `channel_id` 下未完成 publish job 数量。

计入：

- `queued`
- `checking_cookie`
- `cookie_ready`
- `starting_remote_login`
- `waiting_cookie`
- `publishing`

不计入：

- `succeeded`
- `failed`
- `cancelled`
- login-only 任务

## 3. 发文串行与并发语义

### 3.1 同一 channel_id 串行

同一个 `channel_id` 连续提交多篇文章时：

1. 第一篇创建后，如果没有同 channel 执行中任务，会被立即领取并进入 `checking_cookie` / `publishing` / `waiting_cookie`。
2. 第二篇及后续任务会保持 `queued`。
3. 当前任务进入终态 `succeeded` / `failed` / `cancelled` 后，系统自动启动同 channel 下一篇 `queued` 任务。
4. FIFO 顺序按任务创建时间 `created_at ASC`。

### 3.2 waiting_cookie 的队列含义

当某篇发文任务进入 `waiting_cookie`：

- 它仍然占用该 `channel_id`。
- 后续同 channel 发文任务继续保持 `queued`。
- 用户完成登录并保存 cookie 后，该任务继续发文。
- 只有该任务终态后，下一篇 queued 任务才会启动。

### 3.3 不同 channel_id 并发

不同 `channel_id` 拥有独立队列，互不等待。例如：

- `channel-a` 正在发文
- `channel-b` 可以同时发文

## 4. API 总览

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|:---:|---|
| `GET` | `/health` | 否 | 服务存活检查 |
| `GET` | `/api/v1/platforms` | 是 | 查询支持的平台 |
| `POST` | `/api/v1/jobs` | 是 | 创建发文任务 |
| `GET` | `/api/v1/jobs/{job_id}` | 是 | 查询发文任务 |
| `POST` | `/api/v1/jobs/{job_id}/save-cookie` | 是 | 发文任务登录后手动保存 cookie 并续发 |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | 是 | 取消发文任务 |
| `POST` | `/api/v1/login-sessions` | 是 | 创建独立登录会话 |
| `GET` | `/api/v1/login-sessions/{session_id}` | 是 | 查询登录会话 |
| `DELETE` | `/api/v1/login-sessions/{session_id}` | 是 | 取消登录会话 |
| `GET` | `/api/v1/channels/{channel_id}` | 是 | 查询渠道详情 |
| `DELETE` | `/api/v1/channels/{channel_id}` | 是 | 删除渠道 |
| `GET` | `/api/v1/channels/{channel_id}/publish-status` | 是 | 查询渠道发文状态和未完成发文数量 |

当前代码没有公开 `GET /api/v1/jobs` 列表接口，也没有公开日志读取接口。

## 5. Service API

### 5.1 GET /health

服务存活检查。

鉴权：不需要。

请求参数：无。

成功响应：`200 OK`

| 字段 | 类型 | 必填 | 取值 | 含义 |
|---|---|:---:|---|---|
| `status` | string | 是 | `ok` | 存活状态 |
| `service` | string | 是 | `publish` | 服务名 |

示例：

```json
{
  "status": "ok",
  "service": "publish"
}
```

## 6. Platform API

### 6.1 GET /api/v1/platforms

查询当前服务支持的平台列表。

鉴权：需要 Bearer Token。

请求参数：无。

成功响应：`200 OK`

| 字段 | 类型 | 必填 | 含义 |
|---|---|:---:|---|
| `platforms` | array<object> | 是 | 平台列表 |
| `platforms[].id` | string | 是 | 平台标识，枚举：`toutiao` / `sohu` |
| `platforms[].name` | string | 是 | 平台名称 |
| `platforms[].home_url` | string | 是 | 平台主页 URL |
| `platforms[].login_url` | string | 是 | 平台登录页 URL |

示例：

```json
{
  "platforms": [
    {
      "id": "toutiao",
      "name": "toutiao",
      "home_url": "https://mp.toutiao.com",
      "login_url": "https://mp.toutiao.com/auth/page/login"
    },
    {
      "id": "sohu",
      "name": "sohu",
      "home_url": "https://mp.sohu.com",
      "login_url": "https://mp.sohu.com"
    }
  ]
}
```

错误响应：

| HTTP 状态码 | 场景 | 响应 |
|---|---|---|
| `401` | 未携带 token 或 token 错误 | `{"detail":"unauthorized"}` |

## 7. Publish Job API

### 7.1 POST /api/v1/jobs

创建发文任务。接口立即返回，不等待发文完成。

鉴权：需要 Bearer Token。

Content-Type：`application/json`

请求体：

| 字段 | 类型 | 必填 | 限制/枚举 | 含义 |
|---|---|:---:|---|---|
| `channel_id` | string | 是 | `^[A-Za-z0-9_-]{1,64}$` | 发文渠道句柄 |
| `title` | string | 是 | 长度 `1..200` | 文章标题 |
| `content` | string | 是 | 最小长度 `1` | 文章正文 |
| `cover_image_url` | string 或 null | 否 | URL 字符串；代码未做 URL schema 强校验 | 封面图片 URL |

请求示例：

```json
{
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "title": "测试标题",
  "content": "文章正文",
  "cover_image_url": "https://example.com/cover.jpg"
}
```

成功响应：`202 Accepted`

响应体 `JobCreatedResponse`：

| 字段 | 类型 | 必填 | 取值/枚举 | 含义 |
|---|---|:---:|---|---|
| `job_id` | string | 是 | 32 位 uuid hex | 发文任务 ID |
| `channel_id` | string | 是 | channel id | 发文渠道句柄 |
| `status` | string | 是 | 见 Job status | 创建后的任务状态 |
| `live_url` | string | 是 | URL 或空字符串 | 仅需要用户登录时有值 |
| `error` | object 或 null | 否 | `ErrorInfo` | 仅失败时出现 |

可能成功状态：

| status | 含义 | 调用方下一步 |
|---|---|---|
| `queued` | 同一 `channel_id` 已有任务执行中，本任务已排队 | 轮询 `GET /jobs/{job_id}` |
| `checking_cookie` | 任务已被领取，正在检查 cookie | 轮询 `GET /jobs/{job_id}` |
| `publishing` | 正在自动化发文 | 轮询 `GET /jobs/{job_id}` |
| `waiting_cookie` | 需要用户登录，`live_url` 有值 | 打开 `live_url` 登录；登录成功后继续轮询或调用 save-cookie |
| `failed` | 创建后立即失败 | 查看 `error` |

示例：排队中

```json
{
  "job_id": "9c833a784f424c98aef6cc7d0d06a7f8",
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "status": "queued",
  "live_url": "",
  "error": null
}
```

示例：需要登录

```json
{
  "job_id": "9c833a784f424c98aef6cc7d0d06a7f8",
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "status": "waiting_cookie",
  "live_url": "https://example.com/vnc/remote-session-id/",
  "error": null
}
```

错误响应：

| HTTP 状态码 | 场景 | 响应 |
|---|---|---|
| `401` | token 缺失或错误 | `{"detail":"unauthorized"}` |
| `404` | `channel_id` 不存在 | `{"detail":"channel not found"}` 或 `{"detail":"渠道不存在，请先登录"}` |
| `422` | 请求体字段校验失败，如 `title` 为空、`channel_id` 非法 | FastAPI validation error |

### 7.2 GET /api/v1/jobs/{job_id}

查询发文任务状态、发布结果和错误信息。

鉴权：需要 Bearer Token。

Path 参数：

| 参数 | 类型 | 必填 | 含义 |
|---|---|:---:|---|
| `job_id` | string | 是 | 发文任务 ID |

成功响应：`200 OK`

响应体 `JobResponse`：

| 字段 | 类型 | 必填 | 取值/枚举 | 含义 |
|---|---|:---:|---|---|
| `job_id` | string | 是 | 任务 ID | 发文任务 ID |
| `status` | string | 是 | 见 Job status | 当前状态 |
| `channel_id` | string | 是 | channel id 或空字符串 | 发文渠道句柄 |
| `platform` | string | 是 | `toutiao` / `sohu` / 空字符串 | 平台标识 |
| `title` | string | 是 | 任意字符串 | 文章标题 |
| `cover_image_url` | string | 是 | URL 或空字符串 | 封面图片 URL |
| `live_url` | string | 是 | URL 或空字符串 | 登录或实时查看 URL；状态结束后可能为空 |
| `session_id` | string | 是 | 远程会话 ID 或空字符串 | 内部远程登录会话句柄 |
| `article_url` | string | 是 | URL 或空字符串 | 发布成功后的文章链接 |
| `error` | object 或 null | 否 | `ErrorInfo` | 仅失败状态下有意义 |
| `created_at` | string | 是 | ISO 8601 | 创建时间 |
| `updated_at` | string | 是 | ISO 8601 | 更新时间 |

状态含义：

| status | 返回含义 | 调用方建议 |
|---|---|---|
| `queued` | 同 channel 前序任务未完成 | 等待并继续轮询 |
| `checking_cookie` | 任务已启动，正在检查登录态 | 继续轮询 |
| `starting_remote_login` | 正在启动远程登录 | 继续轮询 |
| `waiting_cookie` | 等待用户登录，`live_url` 通常有值 | 打开 `live_url` 完成登录 |
| `publishing` | 正在发文 | 继续轮询 |
| `succeeded` | 发布成功 | 读取 `article_url` |
| `failed` | 发布失败 | 读取 `error.code` 和 `error.detail` |
| `cancelled` | 已取消 | 不再等待 |

示例：发布成功

```json
{
  "job_id": "9c833a784f424c98aef6cc7d0d06a7f8",
  "status": "succeeded",
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "platform": "toutiao",
  "title": "测试标题",
  "cover_image_url": "https://example.com/cover.jpg",
  "live_url": "",
  "session_id": "",
  "article_url": "https://www.toutiao.com/article/123456/",
  "error": null,
  "created_at": "2026-06-29T08:00:00+00:00",
  "updated_at": "2026-06-29T08:03:00+00:00"
}
```

示例：失败

```json
{
  "job_id": "9c833a784f424c98aef6cc7d0d06a7f8",
  "status": "failed",
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "platform": "toutiao",
  "title": "测试标题",
  "cover_image_url": "",
  "live_url": "",
  "session_id": "",
  "article_url": "",
  "error": {
    "code": "cookie_invalid",
    "detail": "cookie 已失效"
  },
  "created_at": "2026-06-29T08:00:00+00:00",
  "updated_at": "2026-06-29T08:01:00+00:00"
}
```

错误响应：

| HTTP 状态码 | 场景 | 响应 |
|---|---|---|
| `401` | token 缺失或错误 | `{"detail":"unauthorized"}` |
| `404` | 任务不存在，或该 ID 是 login session 而不是 publish job | `{"detail":"任务不存在"}` |
| `503` | 查询任务存储失败 | `{"detail":"查询任务状态失败"}` |

### 7.3 POST /api/v1/jobs/{job_id}/save-cookie

手动保存远程登录会话 cookie，并让发文任务继续执行。

适用场景：

- 发文任务处于 `waiting_cookie`。
- 用户已经在 `live_url` 中完成登录，但需要显式触发保存 cookie。

鉴权：需要 Bearer Token。

请求体：无。

Path 参数：

| 参数 | 类型 | 必填 | 含义 |
|---|---|:---:|---|
| `job_id` | string | 是 | 发文任务 ID |

成功响应：`200 OK`

响应体：同 `JobResponse`。

常见成功后状态：

| status | 含义 |
|---|---|
| `publishing` | cookie 保存成功，发文已继续 |
| `succeeded` | 任务已成功 |

错误响应：

| HTTP 状态码 | 场景 | 响应 |
|---|---|---|
| `401` | token 缺失或错误 | `{"detail":"unauthorized"}` |
| `404` | publish job 不存在，或远程会话不存在 | `{"detail":"任务不存在"}` 或 `{"detail":"remote login session not found"}` |
| `409` | job 没有远程登录会话、cookie 已保存、任务已进入不可保存状态 | `{"detail":"job has no remote login session"}` 等 |
| `500` | cookie 保存或恢复发文时服务端异常 | `{"detail":"错误说明"}` |

### 7.4 POST /api/v1/jobs/{job_id}/cancel

取消发文任务。

鉴权：需要 Bearer Token。

请求体：无。

Path 参数：

| 参数 | 类型 | 必填 | 含义 |
|---|---|:---:|---|
| `job_id` | string | 是 | 发文任务 ID |

成功响应：`200 OK`

响应体：同 `JobResponse`。

取消语义：

| 被取消任务状态 | 行为 |
|---|---|
| `queued` | 标记为 `cancelled`，不影响同 channel 当前执行中任务 |
| `publishing` / `waiting_cookie` / `checking_cookie` 等执行中状态 | 标记为 `cancelled`，清理远程会话/后台任务，并触发同 channel 下一篇 queued 任务 |
| `succeeded` / `failed` / `cancelled` | 返回 `409`，表示任务已结束 |

错误响应：

| HTTP 状态码 | 场景 | 响应 |
|---|---|---|
| `401` | token 缺失或错误 | `{"detail":"unauthorized"}` |
| `404` | publish job 不存在 | `{"detail":"任务不存在"}` |
| `409` | job 已终态 | `{"detail":"job already finished"}` |
| `500` | 取消过程异常 | `{"detail":"错误说明"}` |

## 8. Login Session API

### 8.1 POST /api/v1/login-sessions

创建独立登录会话。用户在远程浏览器中登录平台账号，登录成功后服务生成或绑定 `channel_id`。之后发文使用该 `channel_id`。

鉴权：需要 Bearer Token。

Content-Type：`application/json`

请求体：

| 字段 | 类型 | 必填 | 限制/枚举 | 含义 |
|---|---|:---:|---|---|
| `platform` | string | 否 | `toutiao` / `sohu`，默认 `toutiao` | 要登录的平台 |

请求示例：

```json
{
  "platform": "toutiao"
}
```

成功响应：`202 Accepted`

响应体 `LoginSessionCreatedResponse`：

| 字段 | 类型 | 必填 | 取值/枚举 | 含义 |
|---|---|:---:|---|---|
| `session_id` | string | 是 | job/session id | 登录会话 ID |
| `channel_id` | string | 是 | channel id | 本次登录绑定或将绑定的渠道 |
| `status` | string | 是 | 通常 `waiting_cookie` | 登录会话状态 |
| `live_url` | string | 是 | URL | 远程登录浏览器地址 |
| `error` | object 或 null | 否 | `ErrorInfo` | 仅失败时出现 |

示例：

```json
{
  "session_id": "9c833a784f424c98aef6cc7d0d06a7f8",
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "status": "waiting_cookie",
  "live_url": "https://example.com/vnc/remote-session-id/",
  "error": null
}
```

错误响应：

| HTTP 状态码 | 场景 | 响应 |
|---|---|---|
| `401` | token 缺失或错误 | `{"detail":"unauthorized"}` |
| `422` | `platform` 不在枚举范围内 | FastAPI validation error |
| `500` | 远程登录启动失败 | `{"detail":"错误说明"}` |

### 8.2 GET /api/v1/login-sessions/{session_id}

查询登录会话状态。

鉴权：需要 Bearer Token。

Path 参数：

| 参数 | 类型 | 必填 | 含义 |
|---|---|:---:|---|
| `session_id` | string | 是 | 登录会话 ID |

成功响应：`200 OK`

响应体 `LoginSessionResponse`：

| 字段 | 类型 | 必填 | 取值/枚举 | 含义 |
|---|---|:---:|---|---|
| `session_id` | string | 是 | session id | 登录会话 ID |
| `channel_id` | string | 是 | channel id 或空字符串 | 登录绑定渠道 |
| `status` | string | 是 | `waiting_cookie` / `succeeded` / `failed` / `cancelled` / 其他运行中状态 | 登录会话状态 |
| `platform` | string | 是 | `toutiao` / `sohu` / 空字符串 | 平台标识 |
| `live_url` | string | 是 | URL 或空字符串 | 远程登录浏览器地址 |
| `error` | object 或 null | 否 | `ErrorInfo` | 仅失败时出现 |
| `created_at` | string | 是 | ISO 8601 | 创建时间 |
| `updated_at` | string | 是 | ISO 8601 | 更新时间 |

状态含义：

| status | 含义 |
|---|---|
| `waiting_cookie` | 等待用户在 `live_url` 登录 |
| `succeeded` | 登录成功，cookie 已保存，`channel_id` 可用于发文 |
| `failed` | 登录失败，查看 `error` |
| `cancelled` | 登录会话已取消 |
| `starting_remote_login` | 正在启动远程浏览器 |

错误响应：

| HTTP 状态码 | 场景 | 响应 |
|---|---|---|
| `401` | token 缺失或错误 | `{"detail":"unauthorized"}` |
| `404` | 登录会话不存在，或该 ID 是 publish job 而不是 login session | `{"detail":"登录会话不存在"}` |
| `503` | 查询任务存储失败 | `{"detail":"查询任务状态失败"}` |

### 8.3 DELETE /api/v1/login-sessions/{session_id}

取消登录会话并释放远程浏览器资源。

鉴权：需要 Bearer Token。

请求体：无。

Path 参数：

| 参数 | 类型 | 必填 | 含义 |
|---|---|:---:|---|
| `session_id` | string | 是 | 登录会话 ID |

成功响应：`200 OK`

响应体：同 `LoginSessionResponse`。

常见成功状态：

```json
{
  "session_id": "9c833a784f424c98aef6cc7d0d06a7f8",
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "status": "cancelled",
  "platform": "toutiao",
  "live_url": "",
  "error": null,
  "created_at": "2026-06-29T08:00:00+00:00",
  "updated_at": "2026-06-29T08:01:00+00:00"
}
```

错误响应：

| HTTP 状态码 | 场景 | 响应 |
|---|---|---|
| `401` | token 缺失或错误 | `{"detail":"unauthorized"}` |
| `404` | 登录会话不存在 | `{"detail":"登录会话不存在"}` |
| `409` | 会话已终态 | `{"detail":"job already finished"}` |
| `500` | 取消过程异常 | `{"detail":"错误说明"}` |

## 9. Channel API

### 9.1 GET /api/v1/channels/{channel_id}

查询渠道状态。

鉴权：需要 Bearer Token。

Path 参数：

| 参数 | 类型 | 必填 | 限制 | 含义 |
|---|---|:---:|---|---|
| `channel_id` | string | 是 | `^[A-Za-z0-9_-]{1,64}$` | 渠道句柄 |

成功响应：`200 OK`

响应体 `ChannelResponse`：

| 字段 | 类型 | 必填 | 取值/枚举 | 含义 |
|---|---|:---:|---|---|
| `channel_id` | string | 是 | channel id | 渠道句柄 |
| `platform` | string | 是 | `toutiao` / `sohu` / 空字符串 | 平台标识 |
| `status` | string | 是 | `pending` / `bound` / `invalid` | 渠道状态 |
| `account_name` | string | 是 | 任意字符串 | 平台账号显示名 |
| `has_valid_cookie` | boolean | 是 | `true` / `false` | 当前 cookie 是否仍满足平台登录态判断 |
| `created_at` | string | 是 | ISO 8601 | 创建时间 |
| `updated_at` | string | 是 | ISO 8601 | 更新时间 |

示例：

```json
{
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "platform": "toutiao",
  "status": "bound",
  "account_name": "账号名称",
  "has_valid_cookie": true,
  "created_at": "2026-06-15T08:00:00+00:00",
  "updated_at": "2026-06-15T08:01:00+00:00"
}
```

错误响应：

| HTTP 状态码 | 场景 | 响应 |
|---|---|---|
| `401` | token 缺失或错误 | `{"detail":"unauthorized"}` |
| `404` | 渠道不存在 | `{"detail":"渠道不存在"}` |
| `422` | `channel_id` 格式非法 | `{"detail":"channel_id 不合法：只能包含字母、数字、'_'、'-'，长度 1-64"}` |

### 9.2 GET /api/v1/channels/{channel_id}/publish-status

查询渠道发文状态和未完成发文数量。该接口用于判断账号当前是否空闲，或有多少篇文章正在执行/排队。

鉴权：需要 Bearer Token。

Path 参数：

| 参数 | 类型 | 必填 | 限制 | 含义 |
|---|---|:---:|---|---|
| `channel_id` | string | 是 | `^[A-Za-z0-9_-]{1,64}$` | 渠道句柄 |

成功响应：`200 OK`

响应体 `ChannelPublishStatusResponse`：

| 字段 | 类型 | 必填 | 取值/枚举 | 含义 |
|---|---|:---:|---|---|
| `channel_id` | string | 是 | channel id | 渠道句柄 |
| `account_status` | string | 是 | `idle` / `publishing` | 账号发文状态 |
| `publish_count` | integer | 是 | `>= 0` | 未完成 publish job 数量 |

`account_status` 语义：

| account_status | publish_count | 含义 |
|---|---:|---|
| `idle` | `0` | 该渠道没有未完成发文任务，可立即提交新文章 |
| `publishing` | `>= 1` | 该渠道存在未完成发文任务，可能正在发文、等待登录或排队 |

示例：空闲

```json
{
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "account_status": "idle",
  "publish_count": 0
}
```

示例：一个执行中、两个排队中

```json
{
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "account_status": "publishing",
  "publish_count": 3
}
```

错误响应：

| HTTP 状态码 | 场景 | 响应 |
|---|---|---|
| `401` | token 缺失或错误 | `{"detail":"unauthorized"}` |
| `404` | 渠道不存在 | `{"detail":"渠道不存在"}` |
| `422` | `channel_id` 格式非法 | `{"detail":"channel_id 不合法：只能包含字母、数字、'_'、'-'，长度 1-64"}` |
| `503` | 任务状态查询失败 | `{"detail":"查询任务状态失败"}` |

### 9.3 DELETE /api/v1/channels/{channel_id}

删除渠道记录及其 cookie。

鉴权：需要 Bearer Token。

Path 参数：

| 参数 | 类型 | 必填 | 限制 | 含义 |
|---|---|:---:|---|---|
| `channel_id` | string | 是 | `^[A-Za-z0-9_-]{1,64}$` | 渠道句柄 |

成功响应：`200 OK`

响应体 `ChannelDeleteResponse`：

| 字段 | 类型 | 必填 | 取值 | 含义 |
|---|---|:---:|---|---|
| `channel_id` | string | 是 | channel id | 被删除的渠道 |
| `deleted` | boolean | 是 | `true` | 是否删除成功 |

示例：

```json
{
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "deleted": true
}
```

错误响应：

| HTTP 状态码 | 场景 | 响应 |
|---|---|---|
| `401` | token 缺失或错误 | `{"detail":"unauthorized"}` |
| `404` | 渠道不存在 | `{"detail":"渠道不存在"}` |
| `422` | `channel_id` 格式非法 | `{"detail":"channel_id 不合法：只能包含字母、数字、'_'、'-'，长度 1-64"}` |

## 10. 内部 viewer/proxy 入口

以下入口存在于服务中，但 `include_in_schema=False`，不作为第三方业务 API 主契约。调用方通常只需要使用业务接口返回的 `live_url`，不要自行拼接这些路径。

| 路径 | 用途 | 鉴权方式 |
|---|---|---|
| `/vnc/{session_id}/{path}` | 登录远程浏览器反向代理 | 依赖不可猜的 `session_id` |
| `/publish-viewer/{job_id}/{path}` | 发文实时查看反向代理 | 依赖不可猜的 `job_id` |
| `/scalar` | 本地 API 文档页面 | 未纳入业务 API 契约 |

这些入口可能返回：

| HTTP 状态码 | 含义 |
|---|---|
| `404` | session/viewer 不存在 |
| `410` | session/viewer 已结束 |

## 11. 推荐调用流程

### 11.1 首次接入账号并发文

1. 调用 `POST /api/v1/login-sessions` 创建登录会话。
2. 打开响应中的 `live_url`，由用户完成平台登录。
3. 轮询 `GET /api/v1/login-sessions/{session_id}`，直到 `status=succeeded`。
4. 读取响应中的 `channel_id`。
5. 调用 `POST /api/v1/jobs` 提交文章。
6. 轮询 `GET /api/v1/jobs/{job_id}`，直到 `status=succeeded` / `failed` / `cancelled`。
7. 成功时读取 `article_url`。

### 11.2 已有 channel_id 发文

1. 可选：调用 `GET /api/v1/channels/{channel_id}/publish-status` 查询当前队列状态。
2. 调用 `POST /api/v1/jobs` 提交文章。
3. 如果返回 `queued`，说明同账号前面还有文章，继续轮询 job。
4. 如果返回 `waiting_cookie`，打开 `live_url` 完成登录。
5. 轮询 `GET /api/v1/jobs/{job_id}` 到终态。

### 11.3 同账号多文章提交

可以连续调用 `POST /api/v1/jobs` 提交多篇文章。系统保证：

- 同一 `channel_id` 串行执行。
- 返回 `queued` 的任务已经进入队列，不需要调用方重试创建。
- 取消当前执行中的任务后，下一篇 queued 任务会自动启动。

## 12. curl 示例

### 创建登录会话

```bash
curl -X POST "{BASE_URL}/api/v1/login-sessions" \
  -H "Authorization: Bearer <PUBLISH_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"platform":"toutiao"}'
```

### 创建发文任务

```bash
curl -X POST "{BASE_URL}/api/v1/jobs" \
  -H "Authorization: Bearer <PUBLISH_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
    "title": "测试标题",
    "content": "文章正文",
    "cover_image_url": "https://example.com/cover.jpg"
  }'
```

### 查询发文任务

```bash
curl "{BASE_URL}/api/v1/jobs/9c833a784f424c98aef6cc7d0d06a7f8" \
  -H "Authorization: Bearer <PUBLISH_API_TOKEN>"
```

### 查询渠道发文状态

```bash
curl "{BASE_URL}/api/v1/channels/3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c/publish-status" \
  -H "Authorization: Bearer <PUBLISH_API_TOKEN>"
```

## 13. 版本注意事项

- 本文档以当前代码为准。
- `GET /api/v1/jobs` 任务列表接口当前未实现。
- 日志读取接口当前未公开；运维可在服务器侧查看 `logs/jobs/{YYYY-MM-DD}/{job_id}.log` 和 `logs/service.log`。
- 当前同 channel 串行保证基于单进程内锁和 SQLite job 状态。若未来部署多个 API worker，需要数据库原子领取或分布式锁来保证跨进程强一致。
