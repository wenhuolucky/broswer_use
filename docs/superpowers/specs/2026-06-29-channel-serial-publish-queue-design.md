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

## 设计主线

本设计的主功能是“同一账号发文串行队列”，`publish-status` 只是队列状态的外部查询视图。

实施时必须先保证调度语义：

- 同一 `channel_id` 下，任意时刻最多只有一个 publish job 被调度到执行流程。
- 同一 `channel_id` 下，后续 publish job 不失败、不覆盖、不并发，保持 `queued` 等待。
- 当前 publish job 进入终态后，系统自动领取同一 `channel_id` 最早创建的 `queued` publish job。
- 不同 `channel_id` 使用不同队列，互不等待，仍可并发执行。

`GET /channels/{channel_id}/publish-status` 的 `publish_count` 必须从这个队列状态派生，而不是单独维护计数。这样接口不会和真实队列状态漂移。

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

`queued` 成为真正的等待态。`submit()` 创建 job 后不再无条件启动；它只负责持久化任务并触发一次“尝试调度”。调度器再判断同一 `channel_id` 是否已有正在执行的 publish job。

队列以 `channel_id` 为分区键，不新增独立队列表。`jobs` 表就是队列存储：

- `type='publish'`
- `channel_id='<账号渠道 id>'`
- `status='queued'`
- `created_at` 用于 FIFO 顺序

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
2. `submit()` 调用 `_maybe_start_queued_publish(channel_id)`，但 HTTP 请求不等待排队任务完成。
3. `_maybe_start_queued_publish(channel_id)` 在 channel 级锁内检查是否已有执行中 publish job。
4. 如果已有执行中 job，新 job 保持 `queued`，`POST /jobs` 返回创建成功，调用方可通过 `/jobs/{job_id}` 看到 `status=queued`。
5. 如果没有执行中 job，调度器领取同 channel 最早的 `queued` job，把它更新为 `checking_cookie`。
6. 被领取的 job 释放 channel 锁后进入 cookie 检查、远程登录或后台发文。
7. 任意 publish job 进入终态后，调度器再次调用 `_maybe_start_queued_publish(channel_id)`，启动下一篇。
8. 如果当前 job 转入 `waiting_cookie`，它仍占用该 channel 队列；后续 job 不启动，直到该 job 登录后发文并进入终态，或被取消。

### 状态机

单个 publish job 的状态流转：

```text
queued
  -> checking_cookie
      -> publishing
          -> succeeded | failed | cancelled
      -> starting_remote_login
          -> waiting_cookie
              -> publishing
                  -> succeeded | failed | cancelled
```

关键约束：

- 只有 `queued` job 可以被队列调度器领取。
- 领取动作的第一步必须把 job 更新为 `checking_cookie`，这样同 channel 的下一次调度检查会把它视为执行中。
- `waiting_cookie` 也算执行中，因为账号正在为当前文章恢复登录；不能让下一篇抢先启动。
- 只有 `succeeded`、`failed`、`cancelled` 会释放 channel 队列占用。

### 为什么不用直接在 `submit()` 里 await 队列

`POST /jobs` 的契约是“立即返回不阻塞”。因此排队任务应该快速返回 `job_id/status=queued`，由后台调度器在轮到它时执行，而不是让 HTTP 请求一直挂起。

### 同一 channel 连续提交三篇

假设同一 `channel_id=channel123` 连续提交 A、B、C 三篇文章：

1. 提交 A：创建 `jobA(status=queued)`，调度器发现该 channel 无执行中 job，领取 A，A 变为 `checking_cookie`，随后进入 `publishing` 或 `waiting_cookie`。
2. 提交 B：创建 `jobB(status=queued)`，调度器发现 A 仍在执行中，B 保持 `queued`。
3. 提交 C：创建 `jobC(status=queued)`，调度器发现 A 仍在执行中，C 保持 `queued`。
4. 查询 `publish-status`：返回 `account_status=publishing`，`publish_count=3`。
5. A 成功、失败或被取消后进入终态，调度器领取最早的 queued job，也就是 B。
6. B 进入终态后，调度器领取 C。
7. C 进入终态后，队列为空；查询 `publish-status` 返回 `idle` 和 `publish_count=0`。

如果 A 需要远程登录，B 和 C 仍保持 `queued`。A 完成登录后继续发文；只有 A 发文终态才会启动 B。

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

新增队列查询 helper：

- `next_queued_publish_job(channel_id) -> Job | None`
- `has_executing_publish_job(channel_id) -> bool`
- `count_unfinished_publish_jobs(channel_id) -> int`
- `queued_publish_channel_ids() -> list[str]`

职责说明：

- `next_queued_publish_job(channel_id)` 按 `created_at ASC` 返回同 channel 最早的 `queued` publish job。
- `has_executing_publish_job(channel_id)` 只检查执行中集合，不包含 `queued`。
- `count_unfinished_publish_jobs(channel_id)` 用于 `publish-status`，统计执行中集合 + `queued`。
- `queued_publish_channel_ids()` 用于服务启动恢复，找出所有存在 queued publish job 的 channel。

第一版可以在内存 store 和 SQLite store 都直接实现这些 helper。SQLite 侧优先使用 SQL 聚合和 `ORDER BY created_at ASC LIMIT 1`，避免把大量历史 job 拉到应用层。内存 store 使用同样排序规则，保证测试行为和 SQLite 行为一致。

### PublishAgent

新增状态集合：

- `EXECUTING_PUBLISH_STATUSES`
- `QUEUED_PUBLISH_STATUSES`
- `UNFINISHED_PUBLISH_STATUSES`

调整职责：

- `submit()`:
  - 创建 job，保持初始 `queued`。
  - 写入 job 日志路径。
  - 调用 `_maybe_start_queued_publish(channel_id)`。
  - 如果 job 被立即启动，返回 `status=checking_cookie` 或后续已更新状态。
  - 如果排队等待，返回 `status=queued`，message 可为“任务已排队，等待同账号上一任务完成”。

- `_maybe_start_queued_publish(channel_id)`:
  - 使用 channel 级 `asyncio.Lock` 防止同进程内两个请求同时领取队列。
  - 如果该 channel 已有执行中 publish job，直接返回。
  - 找到最早 `queued` publish job。
  - 在锁内把该 job 更新为 `checking_cookie`，完成“领取”。
  - 释放锁后启动 cookie 检查 / 登录 / 发文流程。

- `_begin_claimed_publish_job(job_id)`:
  - 只处理已经从 `queued` 领取到 `checking_cookie` 的 job。
  - 确认 job 当前处于 `checking_cookie`，并继续进入执行准备。
  - 如果 cookie 有效，调用 `_schedule_publish(job_id)`。
  - 如果 cookie 无效，调用 `_start_remote_login(job_id, ...)`。
  - 如果启动远程登录失败并把 job 置为 `failed`，必须触发同 channel 下一篇调度。

- `_on_background_publish_done(job_id, task)`:
  - 当前逻辑清理 task 和 viewer 后，再读取 job 的 `channel_id`。
  - 如果 task 异常退出并把 job 标记为 `failed`，触发同 channel 下一篇调度。
  - 如果 `_publish_with_cookie()` 因 cookie 失效转入 `waiting_cookie`，该 job 未终态，不触发下一篇。
  - 如果 job 最终为 `succeeded`、`failed`、`cancelled`，触发同 channel 下一篇调度。

- `resume_after_cookie(job_id, cookies)`:
  - 登录完成后该 job 自身进入 `publishing` 并启动发文。
  - 不启动同 channel 下一篇，直到当前 job 发文终态。

- `cancel_job(job_id)`:
  - 如果取消的是正在执行的 job，取消后启动下一篇。
  - 如果取消的是 `queued` job，只标记 `cancelled`，不需要启动下一篇，因为当前执行中任务仍在跑。

建议伪代码：

```python
async def _maybe_start_queued_publish(self, channel_id: str) -> str | None:
    async with self._publish_lock_for_channel(channel_id):
        if self.job_store.has_executing_publish_job(channel_id):
            return None
        next_job = self.job_store.next_queued_publish_job(channel_id)
        if next_job is None:
            return None
        self.job_store.update(next_job.job_id, status=STATUS_CHECKING_COOKIE)
        claimed_job_id = next_job.job_id

    await self._begin_claimed_publish_job(claimed_job_id)
    return claimed_job_id
```

锁内只做“检查 + 领取”，不执行远程登录或真实发文。这样同 channel 的领取是串行的，不同 channel 的长任务仍可并发。

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

不能把整个发文流程放进锁内。真实发文、远程登录等待、用户手动登录都可能持续很久；如果锁覆盖这些长流程，会让同 channel 的取消、查询、恢复逻辑难以推进，也会增加死锁风险。正确边界是：用 job 状态表达“占用中”，用锁保护领取动作。

`_channel_publish_locks` 是进程内结构，channel 数量随运行增长。第一版可以不做锁清理；若后续 channel 数量很大，再增加“队列为空且锁未被占用时清理”的优化。

### 重启恢复

当前 `close_stale_running_jobs_after_restart()` 会把 `queued` 也视作 stale 并标记 failed。引入真正队列后，需要调整：

- 运行中状态：`checking_cookie`、`cookie_ready`、`starting_remote_login`、`waiting_cookie`、`publishing`
- `queued` 不应在重启时失败，因为它只是等待任务。

服务启动后应为每个存在 `queued` publish job 的 channel 调用一次 `_maybe_start_queued_publish(channel_id)`，以恢复队列执行。

如果不做启动恢复，重启后所有 queued job 会永久停留。这个恢复逻辑可以放在 server startup 调用 `agent.resume_queued_publish_jobs_after_restart()`。

启动恢复顺序：

1. `close_stale_running_jobs_after_restart()` 只失败执行中 publish/login job，不处理 `queued`。
2. `queued_publish_channel_ids()` 找出还有 queued publish job 的 channel。
3. 对每个 channel 调用 `_maybe_start_queued_publish(channel_id)`。
4. 每个 channel 最多启动一个 queued job；同 channel 的后续 queued job 仍等待前一个终态。

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

接口层不返回 409。排队也是创建成功：

- 立即被领取：响应里的 `status` 可为 `checking_cookie`、`waiting_cookie` 或 `publishing`，取决于提交返回前状态推进到哪一步。
- 未被领取：响应里的 `status=queued`。
- 两者都代表 `POST /jobs` 创建成功，后续进度以 `/jobs/{job_id}` 为准。

### GET /api/v1/channels/{channel_id}/publish-status

只返回 channel 级概览，不返回单个 active job 摘要：

- `publish_count=0`: `account_status=idle`
- `publish_count>0`: `account_status=publishing`

如果调用方想看每个 job 的细节，仍使用 `/jobs/{job_id}`。

## 测试计划

测试重点必须覆盖队列调度本身，而不是只测 `publish-status` 响应。

队列调度测试：

- 同 channel 第一个 job 会从 `queued` 被调度到 `checking_cookie` / `publishing`。
- 同 channel 第二个 job 保持 `queued`，不会调用 publish adapter。
- 当前一个 job 成功后，同 channel 下一篇自动启动。
- 当前一个 job 失败后，同 channel 下一篇自动启动。
- 当前一个 job 因 cookie 失效进入 `waiting_cookie` 时，同 channel 下一篇不启动。
- 当前一个 job 完成远程登录并继续发文后，同 channel 下一篇仍等待当前 job 终态。
- 取消正在执行的 job 后，同 channel 下一篇自动启动。
- 取消 queued job 不影响当前执行中的 job。
- 不同 channel 的 job 可以同时进入执行中。

FIFO 测试：

- 同 channel 连续创建 A、B、C，A 终态后启动 B，不跳到 C。
- B 取消后启动 C。
- A、B、C 的 `created_at` 顺序是唯一排序依据，不按标题或 job_id 排序。

接口状态测试：

- `/channels/{channel_id}/publish-status` 在无未完成 publish job 时返回 `idle` 和 `publish_count=0`。
- `/channels/{channel_id}/publish-status` 在一个执行中、两个 queued 时返回 `publishing` 和 `publish_count=3`。
- login-only job 不计入 `publish_count`。

重启恢复测试：

- 重启清理不会把 `queued` publish job 标记 failed；启动恢复会启动每个 channel 的下一篇 queued job。
- 同一个 channel 有多个 queued job 时，启动恢复只启动最早一篇。
- 两个不同 channel 各有 queued job 时，启动恢复会各启动一篇。

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
