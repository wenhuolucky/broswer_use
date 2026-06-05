# Auto Publish Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated `auto` service that exposes one publish API, automatically obtains a user's cookie remotely when missing, stores it by `user_id`, and then publishes without requiring callers to submit cookies.

**Architecture:** Add a new `auto/` package only. The package owns its data, logs, FastAPI routes, job state, cookie store, remote-cookie orchestration wrapper, and publish-service adapter. Existing `src/`, `api/publish/`, and `tools/browser_test/` code remains untouched; `auto` imports stable public classes where possible and copies behavior only inside `auto` if behavior must diverge.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, asyncio, existing `api.publish.publish_service.PublishService`, existing platform cookie validation via `src.platforms`.

---

## File Structure

- Create `auto/__init__.py`: marks package.
- Create `auto/models.py`: Pydantic request/response models and job status constants.
- Create `auto/settings.py`: paths and runtime defaults scoped to `auto`.
- Create `auto/cookie_store.py`: load/save/validate cookies under `auto/data/cookies/{platform}/{user_id}.json`.
- Create `auto/remote_cookie/__init__.py`: marks package.
- Create `auto/remote_cookie/remote_login_runner.py`: isolated async runner facade for remote cookie collection. Initial implementation is dependency-injected and testable; production hook returns login URL and later saves cookies through a completion callback.
- Create `auto/adapters/__init__.py`: marks package.
- Create `auto/adapters/publish_service.py`: adapter over existing `api.publish.publish_service.PublishService.publish()`.
- Create `auto/job_store.py`: in-memory job registry with lock-protected updates.
- Create `auto/publish_agent.py`: orchestration state machine.
- Create `auto/api.py`: FastAPI router.
- Create `auto/server.py`: FastAPI app entrypoint.
- Create tests under `tests/auto/` for cookie store, publish agent paths, and API routing.

## Task 1: Models and Cookie Store

**Files:**
- Create: `auto/__init__.py`
- Create: `auto/models.py`
- Create: `auto/settings.py`
- Create: `auto/cookie_store.py`
- Test: `tests/auto/test_cookie_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/auto/test_cookie_store.py` with tests that assert `user_id` is required by request models, cookies save under platform/user paths, and validation rejects empty or wrong-domain cookies.

- [ ] **Step 2: Run red test**

Run: `./.venv/Scripts/python.exe -m pytest tests/auto/test_cookie_store.py -v`
Expected: fail because `auto` modules do not exist.

- [ ] **Step 3: Implement minimal models and cookie store**

Implement request fields: `user_id`, `platform`, `title`, `content`, optional `cover_image_url`. Implement statuses: `queued`, `checking_cookie`, `cookie_ready`, `cookie_missing`, `starting_remote_login`, `waiting_cookie`, `publishing`, `succeeded`, `failed`.

- [ ] **Step 4: Run green test**

Run: `./.venv/Scripts/python.exe -m pytest tests/auto/test_cookie_store.py -v`
Expected: pass.

## Task 2: Job Store and Publish Adapter

**Files:**
- Create: `auto/job_store.py`
- Create: `auto/adapters/__init__.py`
- Create: `auto/adapters/publish_service.py`
- Test: `tests/auto/test_job_store_and_adapter.py`

- [ ] **Step 1: Write failing tests**

Test job creation, status updates, result persistence, and that publish adapter forwards title/content/cookie/request_id/cover URL to an injected publisher.

- [ ] **Step 2: Run red test**

Run: `./.venv/Scripts/python.exe -m pytest tests/auto/test_job_store_and_adapter.py -v`
Expected: fail because modules are missing.

- [ ] **Step 3: Implement minimal job store and adapter**

Use `uuid.uuid4()` job IDs, UTC ISO timestamps, and an injected publisher class for tests. Production default instantiates `PublishService` lazily.

- [ ] **Step 4: Run green test**

Run: `./.venv/Scripts/python.exe -m pytest tests/auto/test_job_store_and_adapter.py -v`
Expected: pass.

## Task 3: Remote Cookie Runner Facade

**Files:**
- Create: `auto/remote_cookie/__init__.py`
- Create: `auto/remote_cookie/remote_login_runner.py`
- Test: `tests/auto/test_remote_login_runner.py`

- [ ] **Step 1: Write failing tests**

Test that starting a remote login returns a `login_url`, marks the run active, and can complete by saving cookies through `CookieStore` without touching original `tools/browser_test` code.

- [ ] **Step 2: Run red test**

Run: `./.venv/Scripts/python.exe -m pytest tests/auto/test_remote_login_runner.py -v`
Expected: fail because runner does not exist.

- [ ] **Step 3: Implement minimal runner**

Implement a testable facade with `start(platform, user_id) -> RemoteLoginSession`. It should reserve a session ID and return a login URL from an injectable starter. Provide `complete_with_cookies(session_id, cookies)` for tests and future integration.

- [ ] **Step 4: Run green test**

Run: `./.venv/Scripts/python.exe -m pytest tests/auto/test_remote_login_runner.py -v`
Expected: pass.

## Task 4: Publish Agent State Machine

**Files:**
- Create: `auto/publish_agent.py`
- Test: `tests/auto/test_publish_agent.py`

- [ ] **Step 1: Write failing tests**

Test two behaviors: when cookie exists, agent publishes immediately; when missing, agent creates a remote login session and returns `waiting_cookie` plus `login_url`.

- [ ] **Step 2: Run red test**

Run: `./.venv/Scripts/python.exe -m pytest tests/auto/test_publish_agent.py -v`
Expected: fail because agent does not exist.

- [ ] **Step 3: Implement minimal state machine**

The agent creates a job, checks cookie, either calls publish adapter and stores result or starts remote login and stores login URL. Keep first version synchronous from API perspective for cookie-ready path and asynchronous-status for cookie-missing path.

- [ ] **Step 4: Run green test**

Run: `./.venv/Scripts/python.exe -m pytest tests/auto/test_publish_agent.py -v`
Expected: pass.

## Task 5: FastAPI API and Server

**Files:**
- Create: `auto/api.py`
- Create: `auto/server.py`
- Test: `tests/auto/test_auto_api.py`

- [ ] **Step 1: Write failing tests**

Use FastAPI `TestClient` to verify `POST /api/v1/auto/publish`, `GET /api/v1/auto/jobs/{job_id}`, and `GET /api/v1/auto/health` exist. Verify missing `user_id` returns 422.

- [ ] **Step 2: Run red test**

Run: `./.venv/Scripts/python.exe -m pytest tests/auto/test_auto_api.py -v`
Expected: fail because API does not exist.

- [ ] **Step 3: Implement API**

Create `app = FastAPI(title="Auto Publish Service")`, include router at `/api/v1/auto`, and wire a module-level `PublishAgent` with local stores.

- [ ] **Step 4: Run green test**

Run: `./.venv/Scripts/python.exe -m pytest tests/auto/test_auto_api.py -v`
Expected: pass.

## Task 6: Full Verification

**Files:**
- No new files.

- [ ] **Step 1: Run auto tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/auto -v`
Expected: all auto tests pass.

- [ ] **Step 2: Verify original tests are not broken by imports**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_publish_docker_api.py tests/test_deepseek_llm_wrapper.py -v`
Expected: pass.

- [ ] **Step 3: Smoke test OpenAPI import**

Run: `./.venv/Scripts/python.exe -c "from auto.server import app; print(app.title)"`
Expected output: `Auto Publish Service`.

- [ ] **Step 4: Report startup command**

Use: `./.venv/Scripts/python.exe -m uvicorn auto.server:app --host 127.0.0.1 --port 19000`.

## Self-Review

- Spec coverage: user_id required, isolated `auto` directory, cookie-present path, cookie-missing remote login path, publish-service injection, and no original code modification are covered.
- Placeholder scan: no TBD/TODO requirements remain.
- Type consistency: request fields and job statuses are consistent across tasks.
