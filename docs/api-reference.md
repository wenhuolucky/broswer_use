# Browser Publish Service API Reference

版本：`2.0.0`

本文档描述当前代码中对外可用的 HTTP API。业务接口统一位于 `/api/v1` 前缀下，除 `/health` 外均需要 Bearer Token 鉴权。

交互式文档：

- Scalar：`GET /scalar`
- OpenAPI JSON：`GET /openapi.json`

Scalar 页面来自实时 OpenAPI schema，已包含中文接口摘要、参数说明、请求/响应字段说明、枚举值、必填项和默认值。本文档用于补充流程语义、状态说明和集成注意事项；若两者冲突，以当前代码生成的 OpenAPI 为准。

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

### 2.6 Account status

`article_accounts.status` 是账号持久状态，类型为 `string`。它只记录业务可用性，不记录正在发文/排队等运行时状态。

| 值 | 类型 | 含义 | 是否可用 |
|---|---|---|:---:|
| `normal` | string | 正常 | 是 |
| `warning` | string | 连续失败达到警告阈值，需人工关注 | 是 |
| `muted` | string | 疑似封号、禁言、无发布权限 | 否 |
| `disabled` | string | 人工禁用 | 否 |

可用账号定义：`status` 不是 `disabled` 且不是 `muted`。`warning` 仍算可用。

账号运行时发文状态不写入 `article_accounts.status`，需要通过 `channel_id` 查询 `/api/v1/channels/{channel_id}/publish-status`，或在账号列表中使用 `include_runtime=true` 展示。

### 2.7 Account group scope

账号接口使用调用方传入的 `group_id` 做租户隔离。服务端 `.env` 不配置 `GROUP_ID` / `GROUP_TEXT`。

规则：

- `group_id` 是账号接口必填参数。
- `group_text` 是可选展示文本，不参与查询过滤和唯一性判断。
- 同一个 `platform + phone` 可以存在于不同 `group_id` 中。
- 查询、修改、删除账号时只访问请求 `group_id` 下的账号。
- 保存/重新绑定账号时，唯一键语义为 `group_id + platform + phone`。
- `phone` 必须是 11 位且以 `1` 开头。

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
| `GET` | `/api/v1/accounts/all` | 是 | 列出指定 `group_id` 下的所有账号 |
| `GET` | `/api/v1/accounts/available` | 是 | 列出指定 `group_id` 下的可用账号 |
| `GET` | `/api/v1/accounts/{platform}/{phone}` | 是 | 查询指定账号详情 |
| `PUT` | `/api/v1/accounts/{platform}/{phone}` | 是 | 保存或重新绑定账号到 channel |
| `PATCH` | `/api/v1/accounts/{platform}/{phone}` | 是 | 修改账号手机号、状态、失败次数等持久字段 |
| `DELETE` | `/api/v1/accounts/{platform}/{phone}` | 是 | 删除账号并清理关联 channel/cookie/proxy assignment |

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
| `channel_id` | string | 是 | `^[A-Za-z0-9_-]{1,64}$` | 发文渠道句柄，登录会话签发 |
| `title` | string | 是 | 长度 `1..200` | 文章标题 |
| `content` | string | 是 | 最小长度 `1` | 文章正文，不能为空 |
| `cover_image_url` | string 或 null | 否 | URL 字符串；代码未做 URL schema 强校验 | 封面图片 URL；不传或传 `null` 时不设置封面 |

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
| `live_url` | string | 是 | URL 或空字符串 | 远程浏览器地址；`waiting_cookie` 时为登录 URL，`publishing` 时可能为发文实时查看 URL，其他状态通常为空 |
| `error` | object 或 null | 否 | `ErrorInfo` | 仅失败时出现 |

注意：`POST /api/v1/jobs` 返回较早，若状态为 `publishing`，创建响应中的 `live_url` 可能暂时为空；发文实时查看入口生成后，可通过 `GET /api/v1/jobs/{job_id}` 读取最新 `live_url`。

创建响应中 `status` 的常见值：

注意：Cookie 检查和等待登录状态请使用带 `ing` 的完整状态名：`checking_cookie`、`waiting_cookie`。

| status | 含义 | 调用方下一步 |
|---|---|---|
| `queued` | 同一 `channel_id` 已有任务执行中，本任务已排队 | 轮询 `GET /jobs/{job_id}` |
| `checking_cookie` | 任务已被领取，正在检查 cookie | 轮询 `GET /jobs/{job_id}` |
| `publishing` | 正在自动化发文；`live_url` 生成后可用于实时查看发文过程 | 轮询 `GET /jobs/{job_id}` |
| `waiting_cookie` | 需要用户登录，`live_url` 有值 | 打开 `live_url` 登录；登录成功后继续轮询或调用 save-cookie |
| `failed` | 创建后立即失败，例如远程登录启动失败 | 查看 `error` |

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
| `live_url` | string | 是 | URL 或空字符串 | 远程浏览器地址；`waiting_cookie` 时为登录 URL，`publishing` 时可能为发文实时查看 URL，终态通常为空 |
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
| `publishing` | 正在发文；`live_url` 可能为发文实时查看入口 | 继续轮询 |
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
| `platform` | string | 否 | 枚举：`toutiao` / `sohu`；默认 `toutiao` | 要登录的平台；不传时登录 `toutiao` |

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


## 10. Account API

Account API 只管理 `article_accounts` 表中的账号绑定、分组隔离和持久状态。登录、发文、任务查询、任务取消仍使用已有 Login Session / Job / Channel API。

账号接口的业务错误使用稳定结构：

```json
{
  "detail": {
    "code": "account_not_found",
    "message": "请求分组下没有该账号",
    "extra": {}
  }
}
```

常见错误码：

| HTTP 状态码 | code | 场景 |
|---|---|---|
| `400` | `missing_group_id` | `group_id` 为空字符串 |
| `400` | `invalid_phone` | `phone` 或 `new_phone` 不是 11 位且以 1 开头 |
| `400` | `invalid_account_status` | `status` 不是 `normal` / `warning` / `muted` / `disabled` |
| `400` | `empty_account_patch` | PATCH 没有任何可修改字段 |
| `404` | `account_not_found` | 请求 `group_id` 下没有该账号 |
| `404` | `channel_not_found` | 保存绑定时 `channel_id` 不存在 |
| `409` | `channel_platform_mismatch` | path 中的 `platform` 与 channel 绑定的平台不一致 |
| `409` | `account_phone_exists` | 同一 `group_id + platform` 下新手机号已存在 |
| `409` | `account_busy` | 删除账号时仍有未完成 publish job，且 `force=false` |
| `422` | FastAPI validation error | 缺少必填字段，或 `platform` 不在枚举范围内，只能是 `toutiao` / `sohu` |
| `503` | `account_store_unavailable` | MySQL 账号存储不可用 |
| `503` | `publish_status_unavailable` | 查询运行时发文状态失败 |

### 10.1 GET /api/v1/accounts/all

列出指定 `group_id` 下的所有账号。

鉴权：需要 Bearer Token。

Query 参数：

| 参数 | 类型 | 必填 | 默认 | 含义 |
|---|---|:---:|---|---|
| `group_id` | string | 是 | 无 | 账号分组/租户 id |
| `platform` | Platform enum 或 null | 否 | null | 按平台过滤，只能是 `toutiao` / `sohu` |
| `status` | string 或 null | 否 | null | 按持久账号状态过滤 |
| `group_text` | string 或 null | 否 | null | 响应展示兜底，不参与过滤 |
| `include_channel` | boolean | 否 | `true` | 是否附带 channel 摘要 |
| `include_runtime` | boolean | 否 | `false` | 是否实时查询发文状态 |
| `limit` | integer | 否 | `100` | 分页大小，服务端限制 `1..500` |
| `offset` | integer | 否 | `0` | 分页偏移 |

成功响应：`200 OK`

响应体 `AccountListResponse`：

| 字段 | 类型 | 必填 | 含义 |
|---|---|:---:|---|
| `group_id` | string | 是 | 请求分组 id |
| `group_text` | string | 是 | 展示文本；请求未传时取首个账号的 `group_text` 或空字符串 |
| `count` | integer | 是 | 本页返回数量 |
| `limit` | integer | 是 | 分页大小 |
| `offset` | integer | 是 | 分页偏移 |
| `accounts` | array<object> | 是 | 账号列表 |

`AccountResponse` 字段：

| 字段 | 类型 | 必填 | 含义 |
|---|---|:---:|---|
| `id` | integer | 是 | `article_accounts.id` |
| `platform` | Platform enum | 是 | 平台标识，`toutiao` / `sohu` |
| `phone` | string | 是 | 11 位手机号账号标识 |
| `phone_masked` | string | 是 | 脱敏手机号 |
| `channel_id` | string | 是 | 绑定的 channel id，对应表字段 `channel` |
| `status` | string | 是 | 持久账号状态 |
| `consecutive_failures` | integer | 是 | 连续失败次数 |
| `group_id` | string | 是 | 分组 id |
| `group_text` | string | 是 | 展示文本 |
| `created_at` | string | 是 | 创建时间 |
| `updated_at` | string | 是 | 更新时间 |
| `channel` | object 或 null | 是 | `include_channel=true` 时的 channel 摘要；channel 不存在时为 null |
| `runtime` | object 或 null | 是 | `include_runtime=true` 时的运行时发文状态 |

`channel` 摘要字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `channel_id` | string | 渠道句柄 |
| `platform` | Platform enum | channel 绑定平台，`toutiao` / `sohu` |
| `status` | string | channel 状态 |
| `account_name` | string | 平台账号显示名 |
| `has_valid_cookie` | boolean | cookie 是否有效 |

`runtime` 字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `account_status` | string | `idle` / `publishing` |
| `publish_count` | integer | 未完成 publish job 数量 |
| `is_idle` | boolean | `publish_count == 0` |

示例：

```json
{
  "group_id": "TianQW",
  "group_text": "测试组002",
  "count": 1,
  "limit": 100,
  "offset": 0,
  "accounts": [
    {
      "id": 1,
      "platform": "toutiao",
      "phone": "19015896790",
      "phone_masked": "190****6790",
      "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
      "status": "normal",
      "consecutive_failures": 0,
      "group_id": "TianQW",
      "group_text": "测试组002",
      "created_at": "2026-06-30T08:00:00+00:00",
      "updated_at": "2026-06-30T08:00:00+00:00",
      "channel": {
        "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
        "platform": "toutiao",
        "status": "bound",
        "account_name": "账号名称",
        "has_valid_cookie": true
      },
      "runtime": {
        "account_status": "idle",
        "publish_count": 0,
        "is_idle": true
      }
    }
  ]
}
```

### 10.2 GET /api/v1/accounts/available

列出指定 `group_id` 下的可用账号。

可用账号只按持久状态判断：

```text
status != disabled && status != muted
```

因此 `normal` 和 `warning` 都会返回。channel 缺失、cookie 无效、正在发文、队列忙碌不影响是否出现在本接口；这些属于 channel/runtime 状态。

Query 参数同 `GET /api/v1/accounts/all`，但没有 `status` 过滤。

响应结构同 `GET /api/v1/accounts/all`。

### 10.3 GET /api/v1/accounts/{platform}/{phone}

查询指定 `group_id` 下的单个账号。

鉴权：需要 Bearer Token。

Path 参数：

| 参数 | 类型 | 必填 | 限制 | 含义 |
|---|---|:---:|---|---|
| `platform` | Platform enum | 是 | `toutiao` / `sohu` | 平台标识 |
| `phone` | string | 是 | `^1\\d{10}$` | 账号手机号 |

Query 参数：

| 参数 | 类型 | 必填 | 默认 | 含义 |
|---|---|:---:|---|---|
| `group_id` | string | 是 | 无 | 账号分组/租户 id |
| `include_channel` | boolean | 否 | `true` | 是否附带 channel 摘要 |
| `include_runtime` | boolean | 否 | `false` | 是否实时查询发文状态 |

成功响应：`200 OK`，响应体为单个 `AccountResponse`。

错误响应：

| HTTP 状态码 | code | 场景 |
|---|---|---|
| `400` | `missing_group_id` | `group_id` 为空字符串 |
| `400` | `invalid_phone` | `phone` 非 11 位手机号 |
| `404` | `account_not_found` | 请求 `group_id` 下没有该账号 |
| `422` | FastAPI validation error | 缺少必填 `group_id`，或 `platform` 不在枚举范围内 |

### 10.4 PUT /api/v1/accounts/{platform}/{phone}

保存或重新绑定账号记录。通常在 `POST /api/v1/login-sessions` 登录成功并拿到 `channel_id` 后调用。

鉴权：需要 Bearer Token。

Content-Type：`application/json`

Path 参数同 `GET /api/v1/accounts/{platform}/{phone}`。

请求体 `AccountUpsertRequest`：

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|:---:|---|---|
| `group_id` | string | 是 | 无 | 账号分组/租户 id |
| `group_text` | string | 否 | `group_id` | 展示文本，不参与过滤 |
| `channel_id` | string | 是 | 无 | 已存在 channel id |
| `status` | string | 否 | `normal` | 持久账号状态 |
| `reset_failures` | boolean | 否 | `true` | 是否把 `consecutive_failures` 重置为 0 |
| `consecutive_failures` | integer | 否 | `0` | 不重置时写入的连续失败次数 |

请求示例：

```json
{
  "group_id": "TianQW",
  "group_text": "测试组002",
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "status": "normal",
  "reset_failures": true
}
```

成功响应：`200 OK`，响应体为 `AccountResponse`。

校验规则：

- `phone` 必须是 11 位且以 `1` 开头。
- `group_id` 必填且没有默认值；缺少时返回 `422`，空字符串返回 `400 missing_group_id`。
- `channel_id` 必须存在。
- path 中的 `platform` 必须等于 channel 绑定的平台；否则返回 `409 channel_platform_mismatch`，不会写入账号表。
- upsert 范围只限 `group_id + platform + phone`。

### 10.5 PATCH /api/v1/accounts/{platform}/{phone}

修改账号持久字段。修改手机号、禁用/启用、标记 warning/muted、重置失败次数都使用本接口。

鉴权：需要 Bearer Token。

Content-Type：`application/json`

Path 参数同 `GET /api/v1/accounts/{platform}/{phone}`。

请求体 `AccountPatchRequest`：

| 字段 | 类型 | 必填 | 含义 |
|---|---|:---:|---|
| `group_id` | string | 是 | 账号分组/租户 id |
| `group_text` | string 或 null | 否 | 更新展示文本；空字符串按未提供处理 |
| `new_phone` | string 或 null | 否 | 新手机号；空字符串按未提供处理 |
| `status` | string 或 null | 否 | 新持久状态；空字符串按未提供处理 |
| `reset_failures` | boolean 或 null | 否 | `true` 时把 `consecutive_failures` 置 0 |
| `consecutive_failures` | integer 或 null | 否 | 显式设置连续失败次数，必须 `>=0` |

请求示例：禁用账号

```json
{
  "group_id": "TianQW",
  "status": "disabled"
}
```

请求示例：恢复账号并清零失败次数

```json
{
  "group_id": "TianQW",
  "status": "normal",
  "reset_failures": true
}
```

请求示例：修改手机号

```json
{
  "group_id": "TianQW",
  "new_phone": "19015896791"
}
```

请求示例：API Client 里未使用的可选字段可以为空字符串，后端会按未提供处理

```json
{
  "group_id": "TianQW",
  "group_text": "测试组002",
  "new_phone": "",
  "status": "",
  "reset_failures": true,
  "consecutive_failures": 0
}
```

成功响应：`200 OK`，响应体为 `AccountResponse`。

规则：

- path 中的 `platform + phone` 必须先在请求 `group_id` 下存在。
- `group_id` 必填且没有默认值；缺少时返回 `422`，空字符串返回 `400 missing_group_id`。
- `new_phone` 非空时必须是 11 位且以 `1` 开头。
- 同一 `group_id + platform + new_phone` 已存在时返回 `409 account_phone_exists`。
- `status=normal` 且未显式传 `reset_failures` 时，后端默认清零失败次数。

### 10.6 DELETE /api/v1/accounts/{platform}/{phone}

删除账号记录，并按参数清理关联 channel/cookie/proxy assignment。

鉴权：需要 Bearer Token。

Path 参数同 `GET /api/v1/accounts/{platform}/{phone}`。

Query 参数：

| 参数 | 类型 | 必填 | 默认 | 含义 |
|---|---|:---:|---|---|
| `group_id` | string | 是 | 无 | 账号分组/租户 id |
| `force` | boolean | 否 | `false` | 有未完成 publish job 时是否强制取消后删除 |
| `delete_channel` | boolean | 否 | `true` | 是否删除关联 channel/cookie |

默认删除语义：

- 请求 `group_id` 下必须存在该账号。
- `group_id` 必填且没有默认值；缺少时返回 `422`，空字符串返回 `400 missing_group_id`。
- 如果关联 channel 有未完成 publish job，`force=false` 返回 `409 account_busy`，不删除。
- `force=true` 时，后端会尽量取消未完成 publish job。
- 删除 `article_accounts` 记录。
- `delete_channel=true` 时删除关联 channel/cookie。
- 尝试解除 proxy assignment。
- 历史 jobs/logs 保留。

成功响应：`200 OK`

响应体 `AccountDeleteResponse`：

| 字段 | 类型 | 必填 | 含义 |
|---|---|:---:|---|
| `platform` | Platform enum | 是 | 平台标识，`toutiao` / `sohu` |
| `phone` | string | 是 | 被删除账号手机号 |
| `phone_masked` | string | 是 | 脱敏手机号 |
| `group_id` | string | 是 | 分组 id |
| `group_text` | string | 是 | 展示文本 |
| `deleted` | boolean | 是 | 是否删除账号表记录 |
| `channel_id` | string | 是 | 原绑定 channel id |
| `channel_deleted` | boolean | 是 | 是否删除 channel/cookie |
| `proxy_unassigned` | boolean | 是 | 是否解除 proxy assignment |
| `cancelled_jobs` | array<string> | 是 | 强制删除时被取消的 job id |
| `cleanup_warnings` | array<string> | 是 | 清理过程中的非致命告警 |

示例：

```json
{
  "platform": "toutiao",
  "phone": "19015896790",
  "phone_masked": "190****6790",
  "group_id": "TianQW",
  "group_text": "测试组002",
  "deleted": true,
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "channel_deleted": true,
  "proxy_unassigned": false,
  "cancelled_jobs": [],
  "cleanup_warnings": []
}
```

## 11. 内部 viewer/proxy 入口

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

## 12. 推荐调用流程

### 12.1 首次接入账号并保存账号

1. 调用 `POST /api/v1/login-sessions` 创建登录会话。
2. 打开响应中的 `live_url`，由用户完成平台登录。
3. 轮询 `GET /api/v1/login-sessions/{session_id}`，直到 `status=succeeded`。
4. 读取响应中的 `channel_id`。
5. 调用 `PUT /api/v1/accounts/{platform}/{phone}`，传 `group_id` 和 `channel_id`，保存账号绑定。
6. 调用 `GET /api/v1/accounts/{platform}/{phone}?group_id=...` 取回 `channel_id`。
7. 调用 `POST /api/v1/jobs` 提交文章。
8. 轮询 `GET /api/v1/jobs/{job_id}`，直到 `status=succeeded` / `failed` / `cancelled`。
9. 成功时读取 `article_url`。

### 12.2 已有账号按 phone 发文

1. 调用 `GET /api/v1/accounts/{platform}/{phone}?group_id=...` 查询账号，读取 `channel_id`。
2. 可选：调用 `GET /api/v1/channels/{channel_id}/publish-status` 查询当前队列状态。
3. 调用 `POST /api/v1/jobs` 提交文章。
4. 如果返回 `queued`，说明同账号前面还有文章，继续轮询 job。
5. 如果返回 `waiting_cookie`，打开 `live_url` 完成登录。
6. 轮询 `GET /api/v1/jobs/{job_id}` 到终态。

### 12.3 同账号多文章提交

可以连续调用 `POST /api/v1/jobs` 提交多篇文章。系统保证：

- 同一 `channel_id` 串行执行。
- 返回 `queued` 的任务已经进入队列，不需要调用方重试创建。
- 取消当前执行中的任务后，下一篇 queued 任务会自动启动。

## 13. curl 示例

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

### 保存账号绑定

```bash
curl -X PUT "{BASE_URL}/api/v1/accounts/toutiao/19015896790" \
  -H "Authorization: Bearer <PUBLISH_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "group_id": "TianQW",
    "group_text": "测试组002",
    "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
    "status": "normal",
    "reset_failures": true
  }'
```

### 查询账号详情

```bash
curl "{BASE_URL}/api/v1/accounts/toutiao/19015896790?group_id=TianQW&include_runtime=true" \
  -H "Authorization: Bearer <PUBLISH_API_TOKEN>"
```

### 列出可用账号

```bash
curl "{BASE_URL}/api/v1/accounts/available?group_id=TianQW&platform=toutiao" \
  -H "Authorization: Bearer <PUBLISH_API_TOKEN>"
```

### 禁用账号

```bash
curl -X PATCH "{BASE_URL}/api/v1/accounts/toutiao/19015896790" \
  -H "Authorization: Bearer <PUBLISH_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "group_id": "TianQW",
    "status": "disabled"
  }'
```

### 删除账号

```bash
curl -X DELETE "{BASE_URL}/api/v1/accounts/toutiao/19015896790?group_id=TianQW&force=true" \
  -H "Authorization: Bearer <PUBLISH_API_TOKEN>"
```

## 14. 版本注意事项

- 本文档以当前代码为准。
- 当前同 channel 串行保证基于单进程内锁和 SQLite job 状态。若未来部署多个 API worker，需要数据库原子领取或分布式锁来保证跨进程强一致。
