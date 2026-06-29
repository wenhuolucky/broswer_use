# Channel Serial Publish Queue Design

## 背景

当前发文任务以 `channel_id` 作为账号句柄。`POST /api/v1/jobs` 会在 `PublishAgent.submit()` 中创建 job；如果该渠道 cookie 有效，立即通过 `asyncio.create_task()` 启动后台发文；如果 cookie 缺失或失效，则启动远程登录，登录完成后继续发文。

现状允许同一个 `channel_id` 同时提交多篇文章并并发执行。用户的新要求是：同一个 `channel_id` 下多次发文必须串行执行，后续任务排队等待；不同 `channel_id` 之间仍可并发。`GET /api/v1/channels/{channel_id}/publish-status` 也要从“返回当前 active job 摘要”调整为“返回空闲或发文中，并返回未完成发文数量”。

## 可行性结论

可以实现，且不需要新增数据库表。

原因：

- `jobs` 表已经持久化 `channel_id`、`type`、`status`、`created_at`，并有 `idx_jobs_channel_created` 索引。
- `STATUS_QUEUED` 已存在，可作为同账号串行队列里的等待状态。
- `JobStore.list_jobs()` 已支持按 `channel_id`、`statuses`、`job_type` 查询并按 `created_at DESC` 排序。
- `PublishAgent` 已集中负责 submit、后台发文调度、远程登录恢复、取消和启动时 stale job 清理，是合适的串行调度边界。

需要注意：当前服务设计是单进程 SQLite 自部署。设计会保证单进程内的同 channel 串行；如果未来跑多个 API 进程，需要数据库级“领取任务”原子更新或分布式锁，本次不覆盖。

## 目标

- 同一 `channel_id` 同一时间最多只有一个 publish job 处于实际执行流程。
- 同一 `channel_id` 后续 publish job 保持 `queued`，按创建时间先进先出启动。
- 一个 publish job 结束后自动启动同一 `channel_id` 的下一篇。
- 不同 `channel_id` 的 publish job 仍然可并发。
- `/channels/{channel_id}/publish-status` 只表达两种账号状态：
  - `idle`
  - `publishing`
- `/channels/{channel_id}/publish-status` 返回该 channel 当前未完成发文数量。

## 非目标

- 不把同 channel 重复提交改成 409 报错。
- 不新增用户可见的“暂停队列、重排队列、删除队列中单项”接口。
- 不新增跨进程分布式锁。
- 不改变 `GET /api/v1/jobs/{job_id}` 的核心响应结构。
- 不改变 login-only 会话的并发模型。

## 状态定义

### Job 内部状态

复用现有状态：

- `queued`: 已创建但尚未开始执行，作为同 channel 队列等待态。
- `checking_cookie`: 该 job 已被串行调度器领取，正在检查 cookie 或准备进入执行流程。
- `starting_remote_login`: 正在为该 job 启动远程登录。
- `waiting_cookie`: 该 job 等待用户在远程浏览器中完成登录。
- `publishing`: 该 job 正在调用自动化发文内核。
- `succeeded` / `failed` / `cancelled`: 终态，不再占用队列。

`cookie_ready` 当前代码里没有明显主路径使用，但仍可归入“占用中”集合，保持兼容。

### Channel 对外状态

`GET /api/v1/channels/{channel_id}/publish-status` 返回：

空闲：

```json
{
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "account_status": "idle",
  "publish_count": 0
}
```

发文中或排队中：

```json
{
  "channel_id": "3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c",
  "account_status": "publishing",
  "publish_count": 3
}
```

`publish_count` 统计同一 `channel_id` 下所有未完成 publish job，包含：

- `queued`
- `checking_cookie`
- `cookie_ready`
- `starting_remote_login`
- `waiting_cookie`
- `publishing`

不包含：

- `succeeded`
- `failed`
- `cancelled`
- `login` 类型 job

## 串行调度设计

### 核心思路

`queued` 成为真正的等待态。`submit()` 创建 job 后不再无条件启动；它先判断同一 `channel_id` 是否已有正在执行的 publish job。

执行中集合：

- `checking_cookie`
- `cookie_ready`
- `starting_remote_login`
- `waiting_cookie`
- `publishing`

等待中集合：

- `queued`

未完成集合：

- 执行中集合 + 等待中集合

调度规则：

1. `submit()` 创建新 job，初始状态为 `queued`。
2. 如果同一 channel 没有执行中 publish job，则把这个 job 领取为执行中：更新为 `checking_cookie` 并进入 cookie 检查 / 登录 / 发文流程。
3. 如果同一 channel 已有执行中 publish job，则保持 `queued`，返回创建成功。调用方可通过 job 查询看到 `status=queued`。
4. 任意 publish job 进入终态后，调度器查找同 channel 最早创建的 `queued` publish job 并启动它。

### 为什么不用直接在 `submit()` 里 await 队列

`POST /jobs` 的契约是“立即返回不阻塞”。因此排队任务应该快速返回 `job_id/status=queued`，由后台调度器在轮到它时执行，而不是让 HTTP 请求一直挂起。

### FIFO 顺序

队列按 `created_at ASC` 启动。当前 `list_jobs()` 是 `created_at DESC`，所以需要新增一个专用查询方法，避免在调用方手动反转造成语义不清。

建议在 `JobStore` 增加：

```python
def list_jobs(..., newest_first: bool = True) -> list[Job]
```

或更聚焦地增加：

```python
def next_queued_publish_job(channel_id: str) -> Job | None
```

推荐第二种，范围更小、语义更清楚。

## 组件设计

### JobStore

新增只读 helper：

- `count_jobs(channel_id, statuses, job_type="publish") -> int`
- `next_queued_publish_job(channel_id) -> Job | None`

也可以用现有 `list_jobs()` 组合实现，但计数接口能避免把一批 job 取到内存后再 `len()`。考虑当前规模小，第一版可先用 `list_jobs(..., limit=500)` 计数，若后续队列变长再优化。

### PublishAgent

新增状态集合：

- `EXECUTING_PUBLISH_STATUSES`
- `QUEUED_PUBLISH_STATUSES`
- `UNFINISHED_PUBLISH_STATUSES`

调整职责：

- `submit()`:
  - 创建 job 后调用 `_maybe_start_queued_publish(channel_id)`。
  - 如果 job 被立即启动，返回 `status=checking_cookie` 或后续已更新状态。
  - 如果排队等待，返回 `status=queued`，message 可为“任务已排队，等待同账号上一任务完成”。

- `_maybe_start_queued_publish(channel_id)`:
  - 使用 channel 级 `asyncio.Lock` 防止同进程内两个请求同时领取队列。
  - 如果该 channel 已有执行中 publish job，直接返回。
  - 找到最早 `queued` publish job，启动它。

- `_start_queued_publish(job_id)`:
  - 更新 job 为 `checking_cookie`。
  - 如果 cookie 有效，调用 `_schedule_publish(job_id)`。
  - 如果 cookie 无效，调用 `_start_remote_login(job_id, ...)`。

- `_on_background_publish_done(job_id, task)`:
  - 当前逻辑清理 task 和 viewer 后，再读取 job 的 `channel_id`。
  - 对终态或异常失败，都触发 `_schedule_next_for_channel(channel_id)`。

- `resume_after_cookie(job_id, cookies)`:
  - 登录完成后该 job 自身进入 `publishing` 并启动发文。
  - 不启动同 channel 下一篇，直到当前 job 发文终态。

- `cancel_job(job_id)`:
  - 如果取消的是正在执行的 job，取消后启动下一篇。
  - 如果取消的是 `queued` job，只标记 `cancelled`，不需要启动下一篇，因为当前执行中任务仍在跑。

### Channel publish-status schema

修改 `ChannelPublishStatusResponse`：

移除或弃用：

- `is_idle`
- `active_job`

新增：

- `publish_count: int`

返回示例：

```json
{
  "channel_id": "channel123",
  "account_status": "publishing",
  "publish_count": 2
}
```

兼容性取舍：这是对刚新增接口的 breaking change，但接口还很新，且用户明确要求新语义。README 需要同步更新。

## 并发与一致性

### 单进程保证

同一 `channel_id` 的调度必须通过 channel 级 `asyncio.Lock` 串行化。锁只保护“检查是否已有执行中任务 + 领取下一篇 queued job + 启动执行流程”的短临界区。

建议结构：

```python
self._channel_publish_locks: dict[str, asyncio.Lock] = {}
```

通过 helper 获取：

```python
def _publish_lock_for_channel(self, channel_id: str) -> asyncio.Lock:
    ...
```

### 重启恢复

当前 `close_stale_running_jobs_after_restart()` 会把 `queued` 也视作 stale 并标记 failed。引入真正队列后，需要调整：

- 运行中状态：`checking_cookie`、`cookie_ready`、`starting_remote_login`、`waiting_cookie`、`publishing`
- `queued` 不应在重启时失败，因为它只是等待任务。

服务启动后应为每个存在 `queued` publish job 的 channel 调用一次 `_maybe_start_queued_publish(channel_id)`，以恢复队列执行。

如果不做启动恢复，重启后所有 queued job 会永久停留。这个恢复逻辑可以放在 server startup 调用 `agent.resume_queued_publish_jobs_after_restart()`。

### 竞态边界

在单进程内，channel lock 能避免两个请求同时启动同一 channel 两个 job。

在多进程部署下，两个进程可能同时领取 queued job。本项目 README 和 SQLite 注释都更偏单进程自部署，因此本次设计明确不覆盖多进程强一致。若未来需要，应改为数据库原子领取，例如 `UPDATE ... WHERE status='queued' AND NOT EXISTS (...) RETURNING ...`。

## API 行为

### POST /api/v1/jobs

同 channel 连续提交时：

第一篇：

```json
{
  "job_id": "job1",
  "channel_id": "channel123",
  "status": "checking_cookie",
  "live_url": ""
}
```

第二篇：

```json
{
  "job_id": "job2",
  "channel_id": "channel123",
  "status": "queued",
  "live_url": ""
}
```

如果第一篇需要登录，第二篇仍保持 `queued`，直到第一篇登录并发布完成后才启动。

不同 channel 的 job 不互相等待。

### GET /api/v1/channels/{channel_id}/publish-status

只返回 channel 级概览，不返回单个 active job 摘要：

- `publish_count=0`: `account_status=idle`
- `publish_count>0`: `account_status=publishing`

如果调用方想看每个 job 的细节，仍使用 `/jobs/{job_id}`。

## 测试计划

新增或调整测试覆盖：

- 同 channel 第一个 job 会从 `queued` 被调度到 `checking_cookie` / `publishing`。
- 同 channel 第二个 job 保持 `queued`，不会调用 publish adapter。
- 当前一个 job 成功后，同 channel 下一篇自动启动。
- 当前一个 job 失败后，同 channel 下一篇自动启动。
- 取消正在执行的 job 后，同 channel 下一篇自动启动。
- 取消 queued job 不影响当前执行中的 job。
- 不同 channel 的 job 可以同时进入执行中。
- `/channels/{channel_id}/publish-status` 在无未完成 publish job 时返回 `idle` 和 `publish_count=0`。
- `/channels/{channel_id}/publish-status` 在一个执行中、两个 queued 时返回 `publishing` 和 `publish_count=3`。
- login-only job 不计入 `publish_count`。
- 重启清理不会把 `queued` publish job 标记 failed；启动恢复会启动每个 channel 的下一篇 queued job。

验证命令：

```powershell
uv run pytest tests/unit/test_channel_publish_status_api.py -q
uv run pytest tests/unit/test_channel_serial_publish_queue.py -q
uv run pytest -q
uv run python -m py_compile app\api\v1\channels.py app\schemas\channels.py app\publishing\orchestrator.py app\jobs\store.py app\server.py
```

## README 更新

README 需要同步说明：

- 同一 `channel_id` 多次提交发文会排队串行执行。
- 不同 `channel_id` 仍可并发。
- `/publish-status` 返回 `idle/publishing` 和 `publish_count`。
- `publish_count` 包含执行中和排队中的 publish job，不包含终态和 login-only job。

## 风险与缓解

- 风险：`queued` 语义改变，旧逻辑把它当“马上执行前的短暂状态”。
  缓解：更新 mapper/README/test，并确保 queued job 可通过 `/jobs/{job_id}` 查询。

- 风险：重启后 queued job 停滞。
  缓解：新增启动恢复方法并在 FastAPI startup 中调用。

- 风险：同 channel login 流程卡住会阻塞后续队列。
  缓解：这是串行语义的自然结果；调用方可以取消卡住的 job，取消后调度下一篇。

- 风险：多进程部署下仍可能竞态。
  缓解：文档明确当前保证范围是单进程；未来再做数据库原子领取。
