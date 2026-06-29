# Channel Serial Publish Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make multiple publish submissions for the same `channel_id` run serially through a FIFO queue while different `channel_id`s can still publish concurrently.

**Architecture:** Use the existing `jobs` table as the queue store: `queued` publish jobs wait, executing publish statuses occupy the channel, and terminal statuses release it. `JobStore` exposes focused queue helpers; `PublishAgent` owns channel-level in-process locks and advances the next queued job when the current one finishes.

**Tech Stack:** FastAPI, Pydantic, SQLite/in-memory `JobStore`, asyncio, pytest, `uv`.

---

### Task 1: JobStore Queue Helpers

**Files:**
- Modify: `app/jobs/store.py`
- Create: `tests/unit/test_job_store_publish_queue.py`

- [ ] Write failing tests for FIFO queued lookup, executing detection, unfinished count, and queued channel ids.
- [ ] Run `uv run pytest tests/unit/test_job_store_publish_queue.py -q` and confirm the helper methods are missing.
- [ ] Implement helper methods for both in-memory and SQLite stores.
- [ ] Re-run `uv run pytest tests/unit/test_job_store_publish_queue.py -q`.

### Task 2: PublishAgent Same-Channel Serial Scheduling

**Files:**
- Modify: `app/publishing/orchestrator.py`
- Create/modify: `tests/unit/test_channel_serial_publish_queue.py`

- [ ] Write failing tests showing first same-channel job starts and second same-channel job stays `queued`.
- [ ] Write failing tests showing different channels can start independently.
- [ ] Implement channel locks, executing/queued/unfinished status sets, `_maybe_start_queued_publish()`, and `_begin_claimed_publish_job()`.
- [ ] Re-run serial queue tests.

### Task 3: Queue Advancement After Terminal States

**Files:**
- Modify: `app/publishing/orchestrator.py`
- Modify: `tests/unit/test_channel_serial_publish_queue.py`

- [ ] Write failing tests for success, failure, exception, and cancel of executing job starting the next queued job.
- [ ] Write failing tests proving `waiting_cookie` does not start the next queued job.
- [ ] Implement terminal-state advancement hooks.
- [ ] Re-run serial queue tests.

### Task 4: Restart Recovery

**Files:**
- Modify: `app/publishing/orchestrator.py`
- Modify: `app/server.py`
- Modify: `tests/unit/test_channel_serial_publish_queue.py`

- [ ] Write failing tests showing restart cleanup does not fail `queued` publish jobs.
- [ ] Write failing tests showing startup recovery starts one queued publish job per channel.
- [ ] Implement `resume_queued_publish_jobs_after_restart()` and call it during lifespan startup.
- [ ] Re-run serial queue tests.

### Task 5: Publish Status API And README

**Files:**
- Modify: `app/api/v1/channels.py`
- Modify: `app/schemas/channels.py`
- Modify: `app/publishing/orchestrator.py`
- Modify: `tests/unit/test_channel_publish_status_api.py`
- Modify: `README.md`

- [ ] Update failing API tests to expect `account_status=idle|publishing` and `publish_count`.
- [ ] Implement response schema and mapper changes.
- [ ] Update README endpoint documentation and examples.
- [ ] Re-run publish-status tests.

### Task 6: Full Verification

**Files:**
- Verify all changed files.

- [ ] Run `uv run pytest tests/unit/test_job_store_publish_queue.py -q`.
- [ ] Run `uv run pytest tests/unit/test_channel_serial_publish_queue.py -q`.
- [ ] Run `uv run pytest tests/unit/test_channel_publish_status_api.py -q`.
- [ ] Run `uv run pytest -q`.
- [ ] Run `uv run python -m py_compile app\api\v1\channels.py app\schemas\channels.py app\publishing\orchestrator.py app\jobs\store.py app\server.py`.
- [ ] Run `git diff --check`.
