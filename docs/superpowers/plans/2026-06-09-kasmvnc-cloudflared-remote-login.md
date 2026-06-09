# KasmVNC Cloudflared Remote Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace only the remote-login streaming layer with KasmVNC/noVNC exposed through cloudflared while keeping the publish/login/savecookie APIs and Cookie storage model unchanged.

**Architecture:** `RemoteLoginRunner` remains the facade used by `PublishAgent`, but its real session startup allocates a display slot, starts KasmVNC/Xvnc, launches Chromium on that display, mounts a `/vnc/{session_id}` reverse proxy, and returns a cloudflared URL pointing at the FastAPI service. Cookie extraction and `CookieStore` persistence continue to use the existing `save_session_cookies()` flow.

**Tech Stack:** FastAPI, aiohttp/websocket proxying via FastAPI + websockets/httpx, Playwright, KasmVNC/Xvnc, cloudflared, pytest.

---

### Task 1: Add streaming primitives

**Files:**
- Create: `app/streaming/__init__.py`
- Create: `app/streaming/display_pool.py`
- Create: `app/streaming/kasmvnc.py`
- Test: `tests/test_kasmvnc_streaming.py`

- [ ] Write tests for slot allocation/release and KasmVNC command construction.
- [ ] Verify tests fail because the modules do not exist.
- [ ] Implement `Slot`, `DisplayPool`, `StreamProcess`, and `start_stream()`.
- [ ] Verify streaming tests pass.

### Task 2: Add VNC proxy

**Files:**
- Create: `app/api/vnc_proxy.py`
- Modify: `app/server.py`
- Test: `tests/test_vnc_proxy.py`

- [ ] Write tests that unauthorized `/vnc/{session_id}/` requests return 401 and authorized requests proxy to the registered web port.
- [ ] Verify tests fail because the proxy route does not exist.
- [ ] Implement token verification and HTTP/WebSocket reverse proxy helpers.
- [ ] Mount the route in `app/server.py`.
- [ ] Verify proxy tests pass.

### Task 3: Replace remote-login real session startup

**Files:**
- Modify: `app/remote/login.py`
- Test: `tests/test_remote_login_kasmvnc.py`
- Update: `tests/test_cloudflared_tunnel.py`

- [ ] Write tests that `RemoteLoginRunner.start()` creates a KasmVNC session and returns a cloudflared `/vnc/{session_id}/` URL.
- [ ] Write tests that `save_session_cookies()` extracts cookies from the session browser and cleans up stream, tunnel, browser, and display slot.
- [ ] Verify tests fail against the existing CDP screencast implementation.
- [ ] Refactor `RemoteLoginSession` to include display slot, stream process, vnc token, and tunnel base URL.
- [ ] Implement KasmVNC startup inside `_start_real_session()`.
- [ ] Keep `_start_cloudflared_tunnel()` behavior compatible with existing tests.
- [ ] Verify remote-login tests pass.

### Task 4: Add configuration and Docker support

**Files:**
- Modify: `app/core/config.py`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml` if needed
- Test: `tests/test_config_paths.py` or new `tests/test_remote_login_config.py`

- [ ] Write tests for new environment defaults and parsing.
- [ ] Verify tests fail before config additions.
- [ ] Add KasmVNC/display/cloudflared URL configuration.
- [ ] Add KasmVNC dependencies to the Dockerfile while preserving cloudflared.
- [ ] Verify config tests pass.

### Task 5: Full verification and local commit

**Files:**
- All modified files

- [ ] Run targeted tests for streaming, proxy, remote login, and existing publish/live URL behavior.
- [ ] Run the full test suite.
- [ ] Inspect `git diff` to confirm public publish API fields are unchanged.
- [ ] Commit locally on `dev`.
