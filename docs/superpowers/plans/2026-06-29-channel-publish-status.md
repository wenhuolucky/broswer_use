# Channel Publish Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /api/v1/channels/{channel_id}/publish-status` so callers can tell whether a channel/account is idle or occupied by an active publish job.

**Architecture:** Reuse `JobStore.list_jobs()` as the data source and keep channel route responsibilities thin. Add response models and mapper in `app/schemas/channels.py`, expose a read-only `PublishAgent` helper, wire a new `channels` route, and document the endpoint in `README.md`.

**Tech Stack:** FastAPI, Pydantic, existing synchronous `JobStore`/`ChannelStore`, pytest, uv.

---

## File Structure

- Modify `app/schemas/channels.py`: add publish-status response models and mapper.
- Modify `app/publishing/orchestrator.py`: add active publish status constants and a read-only helper for channel publish occupancy.
- Modify `app/api/v1/channels.py`: add `GET /{channel_id}/publish-status`.
- Modify `README.md`: add the endpoint to the API table and include a small query example.
- Create `tests/unit/test_channel_publish_status_api.py`: route-level and mapper behavior tests.

## Task 1: Write Failing Tests

**Files:**
- Create: `tests/unit/test_channel_publish_status_api.py`

- [ ] **Step 1: Add tests for idle, busy, terminal jobs, login jobs, and missing channels**

```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1 import channels as channels_route
from app.domain.channel import Channel, STATUS_CHANNEL_BOUND
from app.domain.job import Job, STATUS_FAILED, STATUS_PUBLISHING, STATUS_SUCCEEDED, STATUS_WAITING_COOKIE


class FakeAgent:
    def __init__(self, channel: Channel | None, active_job: Job | None):
        self.channel = channel
        self.active_job = active_job

    def get_channel(self, channel_id: str):
        return self.channel if self.channel and self.channel.channel_id == channel_id else None

    def get_active_publish_job_for_channel(self, channel_id: str):
        return self.active_job if self.active_job and self.active_job.payload.get("channel_id") == channel_id else None


def make_app(fake_agent: FakeAgent) -> FastAPI:
    original_agent = deps.agent
    original_route_agent = channels_route.agent
    deps.agent = fake_agent
    channels_route.agent = fake_agent
    app = FastAPI()
    app.include_router(channels_route.router)

    @app.on_event("shutdown")
    async def restore_agent():
        deps.agent = original_agent
        channels_route.agent = original_route_agent

    return app


def make_channel(channel_id: str = "channel123") -> Channel:
    return Channel(channel_id=channel_id, platform="toutiao", status=STATUS_CHANNEL_BOUND, account_name="账号名称")


def make_job(channel_id: str, status: str, job_type: str = "publish", title: str = "测试标题") -> Job:
    return Job(
        job_id=f"{status}-job",
        status=status,
        payload={"channel_id": channel_id, "platform": "toutiao", "title": title},
        type=job_type,
        created_at="2026-06-29T08:00:00+00:00",
        updated_at="2026-06-29T08:01:00+00:00",
    )


def test_channel_publish_status_returns_idle_when_no_active_publish_job():
    channel = make_channel()
    client = TestClient(make_app(FakeAgent(channel, None)))

    response = client.get("/channels/channel123/publish-status")

    assert response.status_code == 200
    assert response.json() == {
        "channel_id": "channel123",
        "account_status": "idle",
        "is_idle": True,
        "active_job": None,
    }


def test_channel_publish_status_returns_busy_with_active_publish_job_summary():
    channel = make_channel()
    job = make_job("channel123", STATUS_PUBLISHING)
    client = TestClient(make_app(FakeAgent(channel, job)))

    response = client.get("/channels/channel123/publish-status")

    assert response.status_code == 200
    assert response.json() == {
        "channel_id": "channel123",
        "account_status": "busy",
        "is_idle": False,
        "active_job": {
            "job_id": "publishing-job",
            "status": "publishing",
            "title": "测试标题",
            "created_at": "2026-06-29T08:00:00+00:00",
            "updated_at": "2026-06-29T08:01:00+00:00",
        },
    }


def test_channel_publish_status_treats_waiting_cookie_publish_job_as_busy():
    channel = make_channel()
    job = make_job("channel123", STATUS_WAITING_COOKIE)
    client = TestClient(make_app(FakeAgent(channel, job)))

    response = client.get("/channels/channel123/publish-status")

    assert response.status_code == 200
    assert response.json()["account_status"] == "busy"
    assert response.json()["active_job"]["status"] == "waiting_cookie"


def test_channel_publish_status_ignores_terminal_and_login_jobs_via_agent_helper():
    from app.jobs.store import JobStore
    from app.publishing.orchestrator import PublishAgent

    channel = make_channel()
    agent = PublishAgent(job_store=JobStore(path=""), remote_runner=object())
    agent.channel_store._channels[channel.channel_id] = channel
    agent.job_store.create({"channel_id": channel.channel_id, "platform": "toutiao", "title": "已成功"})
    succeeded = next(iter(agent.job_store._jobs.values()))
    agent.job_store.update(succeeded.job_id, status=STATUS_SUCCEEDED)
    agent.job_store.create({"channel_id": channel.channel_id, "platform": "toutiao", "title": "已失败"})
    failed = [job for job in agent.job_store._jobs.values() if job.status != STATUS_SUCCEEDED][0]
    agent.job_store.update(failed.job_id, status=STATUS_FAILED)
    agent.job_store.create({"channel_id": channel.channel_id, "platform": "toutiao", "job_type": "login_only"})
    login_job = [job for job in agent.job_store._jobs.values() if job.type == "login"][0]
    agent.job_store.update(login_job.job_id, status=STATUS_WAITING_COOKIE)

    assert agent.get_active_publish_job_for_channel(channel.channel_id) is None


def test_channel_publish_status_returns_latest_active_publish_job():
    from app.jobs.store import JobStore
    from app.publishing.orchestrator import PublishAgent

    channel = make_channel()
    agent = PublishAgent(job_store=JobStore(path=""), remote_runner=object())
    agent.channel_store._channels[channel.channel_id] = channel
    older = agent.job_store.create({"channel_id": channel.channel_id, "platform": "toutiao", "title": "旧任务"})
    agent.job_store.update(older.job_id, status=STATUS_WAITING_COOKIE)
    newer = agent.job_store.create({"channel_id": channel.channel_id, "platform": "toutiao", "title": "新任务"})
    agent.job_store.update(newer.job_id, status=STATUS_PUBLISHING)

    active = agent.get_active_publish_job_for_channel(channel.channel_id)

    assert active is not None
    assert active.job_id == newer.job_id
    assert active.payload["title"] == "新任务"


def test_channel_publish_status_returns_404_for_missing_channel():
    client = TestClient(make_app(FakeAgent(None, None)))

    response = client.get("/channels/missing/publish-status")

    assert response.status_code == 404
    assert response.json() == {"detail": "渠道不存在"}
```

- [ ] **Step 2: Run tests and confirm they fail because feature is missing**

Run:

```powershell
uv run pytest tests/unit/test_channel_publish_status_api.py -q
```

Expected: failures or import errors mentioning missing `get_active_publish_job_for_channel` or missing `/publish-status` route.

## Task 2: Implement Schema, Agent Helper, and Route

**Files:**
- Modify: `app/schemas/channels.py`
- Modify: `app/publishing/orchestrator.py`
- Modify: `app/api/v1/channels.py`

- [ ] **Step 1: Add channel publish-status response models and mapper**

Add `ActivePublishJobSummary`, `ChannelPublishStatusResponse`, and `channel_publish_status_from()` to `app/schemas/channels.py`. Include them in `__all__`.

- [ ] **Step 2: Add active publish job helper to `PublishAgent`**

In `app/publishing/orchestrator.py`, define `ACTIVE_PUBLISH_STATUSES` from queued/checking/cookie/start/waiting/publishing statuses. Add:

```python
def get_active_publish_job_for_channel(self, channel_id: str):
    jobs = self.job_store.list_jobs(
        channel_id=channel_id,
        statuses=ACTIVE_PUBLISH_STATUSES,
        job_type="publish",
        limit=1,
    )
    return jobs[0] if jobs else None
```

- [ ] **Step 3: Add route**

In `app/api/v1/channels.py`, add the `GET /{channel_id}/publish-status` route. It must validate channel id, return `404` when the channel is missing, catch store exceptions as `503 查询任务状态失败`, and map the active job into the response.

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
uv run pytest tests/unit/test_channel_publish_status_api.py -q
```

Expected: all tests in the new file pass.

## Task 3: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add endpoint to the API table**

Add `GET /api/v1/channels/{channel_id}/publish-status` after `GET /api/v1/channels/{channel_id}` with description `查询渠道发文占用状态（idle/busy）`.

- [ ] **Step 2: Add a short curl example**

Add a short example showing:

```bash
curl http://127.0.0.1:8833/api/v1/channels/3f9a2b1c8d4e4f0a9b2c1d3e4f5a6b7c/publish-status \
  -H "Authorization: Bearer <PUBLISH_API_TOKEN>"
```

and mention `waiting_cookie` counts as busy for publish occupancy.

## Task 4: Final Verification and Commit

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run target tests**

```powershell
uv run pytest tests/unit/test_channel_publish_status_api.py -q
```

- [ ] **Step 2: Run full tests**

```powershell
uv run pytest -q
```

- [ ] **Step 3: Run compile check**

```powershell
uv run python -m py_compile app\api\v1\channels.py app\schemas\channels.py app\publishing\orchestrator.py app\jobs\store.py
```

- [ ] **Step 4: Review diff**

```powershell
git diff --check
git status --short --branch
git diff -- app\api\v1\channels.py app\schemas\channels.py app\publishing\orchestrator.py README.md tests\unit\test_channel_publish_status_api.py
```

- [ ] **Step 5: Commit**

```powershell
git add app\api\v1\channels.py app\schemas\channels.py app\publishing\orchestrator.py README.md
git add -f tests\unit\test_channel_publish_status_api.py docs\superpowers\plans\2026-06-29-channel-publish-status.md
git commit -m "feat: add channel publish status API" -m "Why: callers need to determine whether a channel/account is idle or already occupied by an active publish job before scheduling work." -m "What: add the channel publish-status endpoint, active publish job lookup, response schemas, tests, and README documentation." -m "Outcome: integrations can query idle/busy state without changing existing channel or job response contracts."
```
