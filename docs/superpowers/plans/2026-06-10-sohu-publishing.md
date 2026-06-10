# Sohu Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `platform=sohu` publishing support while keeping the public API unchanged and preserving Toutiao behavior.

**Architecture:** Split platform-specific publishing behavior behind the existing adapter. Keep the current execution mechanics reusable, route `toutiao` to the existing service, add a new Sohu service and Sohu URL normalization, and require `article_url` for success on both platforms.

**Tech Stack:** Python, FastAPI, Playwright/browser-use, pytest, existing CookieStore/JobStore/RemoteLoginRunner.

---

### Task 1: Sohu URL Normalization

**Files:**
- Modify: `app/utils/urls.py`
- Test: `tests/test_sohu_url.py`

- [ ] **Step 1: Write failing tests**

Add tests:

```python
from app.utils.urls import normalize_article_url, normalize_sohu_article_url


def test_normalize_sohu_preview_url_to_mobile_url():
    url = "https://mp.sohu.com/h5/v2/newsPreview?id=1020931946&type=article"
    assert normalize_sohu_article_url(url, "122702850") == "https://m.sohu.com/a/1020931946_122702850?sec=wd"


def test_normalize_sohu_mobile_url_passthrough():
    url = "https://m.sohu.com/a/1020931946_122702850?sec=wd"
    assert normalize_sohu_article_url(url, "122702850") == url


def test_normalize_sohu_preview_without_account_id_returns_original():
    url = "https://mp.sohu.com/h5/v2/newsPreview?id=1020931946&type=article"
    assert normalize_sohu_article_url(url, "") == url


def test_normalize_article_url_routes_by_platform():
    sohu_url = "https://mp.sohu.com/h5/v2/newsPreview?id=1020931946&type=article"
    assert normalize_article_url("sohu", sohu_url, account_id="122702850") == "https://m.sohu.com/a/1020931946_122702850?sec=wd"
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
python -m pytest tests\test_sohu_url.py -q
```

Expected: import errors for missing Sohu URL helpers.

- [ ] **Step 3: Implement helpers**

Add `normalize_sohu_article_url(url, account_id)` and `normalize_article_url(platform, url, account_id="")` in `app/utils/urls.py`.

- [ ] **Step 4: Verify tests pass**

Run:

```bash
python -m pytest tests\test_sohu_url.py -q
```

Expected: all tests pass.

### Task 2: Sohu Account ID Configuration

**Files:**
- Create: `app/platforms/sohu.py`
- Test: `tests/test_sohu_platform.py`

- [ ] **Step 1: Write failing tests**

Add tests:

```python
from app.platforms.sohu import SohuPlatform


def test_sohu_account_id_prefers_user_map(monkeypatch):
    monkeypatch.setenv("SOHU_ACCOUNT_ID_MAP", "user1:111,user2:222")
    monkeypatch.setenv("SOHU_ACCOUNT_ID", "999")
    assert SohuPlatform().account_id_for_user("user2") == "222"


def test_sohu_account_id_uses_global_fallback(monkeypatch):
    monkeypatch.delenv("SOHU_ACCOUNT_ID_MAP", raising=False)
    monkeypatch.setenv("SOHU_ACCOUNT_ID", "999")
    assert SohuPlatform().account_id_for_user("user1") == "999"


def test_sohu_account_id_returns_empty_when_unconfigured(monkeypatch):
    monkeypatch.delenv("SOHU_ACCOUNT_ID_MAP", raising=False)
    monkeypatch.delenv("SOHU_ACCOUNT_ID", raising=False)
    assert SohuPlatform().account_id_for_user("user1") == ""
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
python -m pytest tests\test_sohu_platform.py -q
```

Expected: missing module or class.

- [ ] **Step 3: Implement `SohuPlatform`**

Create `SohuPlatform(PlatformConfig)` with Sohu URLs, auth domains, account id lookup, and a Sohu prompt skeleton.

- [ ] **Step 4: Verify tests pass**

Run:

```bash
python -m pytest tests\test_sohu_platform.py -q
```

Expected: all tests pass.

### Task 3: Platform-Aware Adapter

**Files:**
- Modify: `app/publishing/adapter.py`
- Modify: `app/publishing/agent.py`
- Test: `tests/test_publish_adapter_platform.py`

- [ ] **Step 1: Write failing tests**

Add tests that verify `PublishServiceAdapter.publish(platform="sohu", ...)` routes to a Sohu publisher factory and that `PublishAgent` passes `request.platform` to the adapter.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
python -m pytest tests\test_publish_adapter_platform.py -q
```

Expected: `platform` is not accepted or not routed.

- [ ] **Step 3: Implement platform routing**

Change adapter `publish()` to accept `platform`. Select Toutiao or Sohu publisher by platform. Pass `request.platform` from `PublishAgent._publish_with_cookie()`.

- [ ] **Step 4: Verify tests pass**

Run:

```bash
python -m pytest tests\test_publish_adapter_platform.py -q
```

Expected: all tests pass.

### Task 4: Preserve Toutiao Behavior During Service Split

**Files:**
- Modify: `app/publishing/service.py`
- Modify: `app/publishing/toutiao_service.py`
- Test: `tests/test_publish_url_tool.py`
- Test: `tests/test_publish_live_url.py`

- [ ] **Step 1: Add compatibility tests if needed**

Existing tests must continue importing `PublishService` and `AutoToutiaoPublishService`.

- [ ] **Step 2: Refactor minimally**

Keep `PublishService` as a compatibility alias for Toutiao behavior. Add overridable methods for platform config and result URL extraction so Sohu can override without duplicating the full execution flow.

- [ ] **Step 3: Verify Toutiao tests pass**

Run:

```bash
python -m pytest tests\test_publish_url_tool.py tests\test_publish_live_url.py -q
```

Expected: existing Toutiao behavior remains green.

### Task 5: Sohu Publishing Service

**Files:**
- Create: `app/publishing/sohu_service.py`
- Modify: `app/publishing/adapter.py`
- Test: `tests/test_sohu_publish_service.py`

- [ ] **Step 1: Write failing tests**

Add tests for:

```python
def test_sohu_service_extracts_and_normalizes_preview_url(monkeypatch):
    ...


def test_sohu_service_requires_url_for_success(monkeypatch):
    ...
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
python -m pytest tests\test_sohu_publish_service.py -q
```

Expected: missing service or URL extraction logic.

- [ ] **Step 3: Implement `AutoSohuPublishService`**

Subclass the shared service. Override platform config, Sohu prompt, URL matching, URL normalization, and success finalization so Sohu success requires a normalized URL.

- [ ] **Step 4: Verify tests pass**

Run:

```bash
python -m pytest tests\test_sohu_publish_service.py -q
```

Expected: all tests pass.

### Task 6: API Result URL Normalization by Platform

**Files:**
- Modify: `app/api/routes.py`
- Test: `tests/test_api_platform_urls.py`

- [ ] **Step 1: Write failing tests**

Add tests that `_published_job_data()` normalizes Sohu preview URLs using `SOHU_ACCOUNT_ID` and leaves Toutiao normalization intact.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
python -m pytest tests\test_api_platform_urls.py -q
```

Expected: Sohu URL is not normalized.

- [ ] **Step 3: Implement platform-aware normalization**

Use `normalize_article_url(platform, url, account_id=...)` in `_published_job_data()`.

- [ ] **Step 4: Verify tests pass**

Run:

```bash
python -m pytest tests\test_api_platform_urls.py -q
```

Expected: all tests pass.

### Task 7: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
python -m pytest tests\test_sohu_url.py tests\test_sohu_platform.py tests\test_publish_adapter_platform.py tests\test_sohu_publish_service.py tests\test_api_platform_urls.py tests\test_publish_url_tool.py tests\test_llm_config.py tests\test_remote_cdp_ipv4.py tests\test_remote_display_config.py -q
```

Expected: all pass.

- [ ] **Step 2: Compile modified Python files**

Run:

```bash
python -m py_compile app\utils\urls.py app\platforms\sohu.py app\publishing\adapter.py app\publishing\agent.py app\publishing\service.py app\publishing\toutiao_service.py app\publishing\sohu_service.py app\api\routes.py
```

Expected: exit code 0.

- [ ] **Step 3: Check whitespace**

Run:

```bash
git diff --check
```

Expected: no output.
