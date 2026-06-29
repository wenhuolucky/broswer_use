# Channel Publish Status API Design

## 背景

当前服务使用 `channel_id` 作为账号句柄：一个 `channel_id` 绑定一个平台、一个平台账号和一份 cookie。发文任务创建时会把 `channel_id` 写入 `jobs` 记录，并且 `JobStore.list_jobs()` 已支持按 `channel_id`、`statuses`、`job_type` 查询任务。

现有 API 可以通过 `GET /api/v1/jobs/{job_id}` 查询单个发文任务状态，也可以通过 `GET /api/v1/channels/{channel_id}` 查询渠道基础信息，但调用方无法直接判断“这个账号当前是否空闲，是否已有发文任务占用”。因此需要新增一个以 channel 为入口的轻量查询接口。

## 目标

- 新增一个只读接口，通过 `channel_id` 查询账号的发文占用状态。
- 返回稳定、易分支的账号状态：`idle` 或 `busy`。
- 当账号忙碌时，返回当前占用该账号的 active publish job 摘要。
- 不改变现有 `GET /channels/{channel_id}` 和 `GET /jobs/{job_id}` 的响应契约。
- 不在本次设计中强制拦截同账号并发发文；该能力可后续复用同一判断逻辑接入 `PublishAgent.submit()`。

## 非目标

- 不新增按平台账号名、手机号、别名或 `native_key` 查询状态的接口。
- 不返回完整 job payload、文章正文、日志路径或内部错误细节。
- 不改变 job 状态机，不新增数据库表。
- 不处理跨进程强互斥；本接口只报告当前持久化任务状态。

## API

新增接口：

```http
GET /api/v1/channels/{channel_id}/publish-status
```

响应模型：

```json
{
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "account_status": "busy",
  "is_idle": false,
  "active_job": {
    "job_id": "9c833a784f424c98aef6cc7d0d06a7f8",
    "status": "publishing",
    "title": "测试标题",
    "created_at": "2026-06-29T08:00:00+00:00",
    "updated_at": "2026-06-29T08:01:00+00:00"
  }
}
```

空闲响应：

```json
{
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "account_status": "idle",
  "is_idle": true,
  "active_job": null
}
```

字段说明：

- `channel_id`: 请求的渠道句柄。
- `account_status`: 账号发文占用状态，仅为 `idle` 或 `busy`。
- `is_idle`: 便于调用方直接布尔判断；与 `account_status == "idle"` 保持一致。
- `active_job`: 忙碌时返回占用中的 publish job 摘要；空闲时为 `null`。
- `active_job.status`: 原始内部 job 状态，便于调用方知道是在等待登录还是实际发布。

错误响应：

- `404`: 渠道不存在，沿用现有文案 `渠道不存在`。
- `422`: `channel_id` 格式非法，复用 `require_valid_channel_id()`。
- `503`: 查询任务状态失败时返回服务不可用；仅在 store 异常时触发。

## 忙碌状态定义

从“这个账号现在能否安全提交另一篇文章”的角度定义 busy。只要同一 `channel_id` 存在 active publish job，就视为 busy。

active publish job 状态集合：

- `queued`
- `checking_cookie`
- `cookie_ready`
- `starting_remote_login`
- `waiting_cookie`
- `publishing`

其中 `waiting_cookie` 也算 busy，因为它属于某个发文任务的续登/补登流程；如果此时报告 idle，调用方可能再次提交同账号发文，造成并发发布或重复登录流程。

终态不算 busy：

- `succeeded`
- `failed`
- `cancelled`

如果同一 `channel_id` 理论上存在多个 active publish job，接口返回 `created_at` 最新的一条作为 `active_job`。这保持查询结果稳定，也能覆盖历史上已经发生过并发提交的情况。

## 组件设计

### Schema

在 `app/schemas/channels.py` 中新增：

- `ActivePublishJobSummary`
- `ChannelPublishStatusResponse`
- `channel_publish_status_from(channel_id, active_job)`

该 mapper 只负责响应结构，不直接查询 store。

### Store / Service

优先复用现有 `JobStore.list_jobs()`：

```python
job_store.list_jobs(
    channel_id=channel_id,
    statuses=ACTIVE_PUBLISH_STATUSES,
    job_type="publish",
    limit=1,
)
```

为了避免 route 直接了解过多业务状态，建议在 `PublishAgent` 中新增只读方法：

```python
def get_channel_publish_status(self, channel_id: str) -> Job | None:
    ...
```

该方法负责：

1. 确认渠道是否存在。
2. 查询 active publish job。
3. 返回 active job 或 `None`。

如果需要更清晰地表达 404，可拆成：

```python
def get_active_publish_job_for_channel(self, channel_id: str) -> tuple[Channel | None, Job | None]:
    ...
```

实现时按现有代码风格选择更简洁的一种。

### Route

在 `app/api/v1/channels.py` 增加：

```python
@router.get("/{channel_id}/publish-status", response_model=ChannelPublishStatusResponse)
async def get_channel_publish_status(channel_id: str):
    ...
```

路由层职责：

1. 调用 `require_valid_channel_id(channel_id)`。
2. 确认 channel 存在，不存在返回 `404`。
3. 调用 agent 查询 active publish job。
4. 使用 schema mapper 生成响应。

## 数据流

1. 调用方请求 `GET /api/v1/channels/{channel_id}/publish-status`。
2. API 校验 `channel_id` 格式。
3. API/agent 查询 `ChannelStore.get(channel_id)`，确认账号存在。
4. agent 使用 `JobStore.list_jobs()` 查询同一 `channel_id` 下 active 状态的 publish job。
5. 查询到 active job 时返回 `busy` 和 job 摘要；未查到时返回 `idle`。

## 并发与一致性

本接口基于持久化 job 状态进行读查询，不维护额外内存锁。单节点 SQLite 场景下，`JobStore` 已通过连接锁和 SQLite `busy_timeout` 处理基础并发读写。

该接口只能反映查询瞬间的状态，不能保证查询之后到提交新 job 之间没有其他请求插入。因此它适合调用方做调度前检查和 UI 展示，但不能单独作为强互斥机制。若后续要防止同账号并发发文，应在 `PublishAgent.submit()` 内部复用同一 active job 查询，并在创建新 job 前返回 `409 conflict`。

## 测试计划

新增或扩展针对 channel route / schema / agent 的测试：

- channel 不存在时，`GET /channels/{channel_id}/publish-status` 返回 `404`。
- channel 存在且没有 active publish job 时，返回 `account_status=idle`、`is_idle=true`、`active_job=null`。
- channel 存在且有 `publishing` publish job 时，返回 `busy` 和 job 摘要。
- `waiting_cookie` 的 publish job 也返回 `busy`。
- `succeeded`、`failed`、`cancelled` 的 publish job 不影响空闲判断。
- login job 即使状态 active，也不应让 publish status 变成 busy。
- 多个 active publish job 存在时，返回最新创建的一条。

验证命令沿用项目现有习惯：

```powershell
uv run pytest -q
uv run python -m py_compile app\api\v1\channels.py app\schemas\channels.py app\publishing\orchestrator.py app\jobs\store.py
```

## 后续扩展

- 在 `POST /api/v1/jobs` 创建发文任务前复用 active job 查询，存在 busy job 时返回 `409 conflict`。
- 可在响应中增加 `reason` 或 `can_publish`，但当前 `account_status` 与 `is_idle` 已能满足调度分支。
- 如未来需要账号维度列表，可新增批量接口，例如 `GET /channels/publish-status?channel_ids=...`，不影响本接口。
