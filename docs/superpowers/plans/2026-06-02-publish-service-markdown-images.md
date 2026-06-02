# Publish Service Markdown Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the old正文图片 upload path and keep Markdown network-image HTML intact through the publish-service pipeline.

**Architecture:** The service will use a single正文 flow: Markdown is converted directly into HTML, pasted into the Toutiao editor, and expected to carry links plus network-image tags. Old `body_images` request handling, prompt instructions, and docs are removed so there is only one supported正文图片 path.

**Tech Stack:** Python, FastAPI, browser-use, unittest

---

### Task 1: Lock the new behavior with regression tests

**Files:**
- Create: `publish_service/test_markdown_rich_behavior.py`

- [ ] **Step 1: Write the failing tests**
- [ ] **Step 2: Run `python -m unittest publish_service.test_markdown_rich_behavior -v` and confirm failure**
- [ ] **Step 3: Keep the tests as the regression harness for implementation**

### Task 2: Keep Markdown image HTML intact

**Files:**
- Modify: `publish_service/markdown_to_rich.py`
- Modify: `publish_service/publish_service.py`

- [ ] **Step 1: Fix Markdown image conversion ordering so `![alt](url)` becomes `<img>` instead of `!<a ...>`**
- [ ] **Step 2: Remove server-side `<img>` stripping from the publish-service path**
- [ ] **Step 3: Remove obsolete helper code if no longer used**

### Task 3: Remove正文图片 upload behavior from prompt and service API

**Files:**
- Modify: `platforms/toutiao.py`
- Modify: `publish_service/service_api.py`
- Modify: `publish_service/models.py`

- [ ] **Step 1: Remove `body_image_paths` behavior from the prompt and describe single-path HTML paste**
- [ ] **Step 2: Remove `body_images` parsing, temp-file handling, and docs from the API**
- [ ] **Step 3: Remove request/schema descriptions that still advertise正文图片 local upload**

### Task 4: Update docs and test helpers

**Files:**
- Modify: `publish_service/README.md`
- Modify: `publish_service/test_data/README.md`
- Modify: `publish_service/test_data/run_test.py`
- Modify: `publish_service/test_browser_clipboard.py`

- [ ] **Step 1: Remove all正文图片 upload references**
- [ ] **Step 2: Document network-URL-only Markdown images**
- [ ] **Step 3: Keep cover upload examples intact**

### Task 5: Verify

**Files:**
- Verify only

- [ ] **Step 1: Run `python -m unittest publish_service.test_markdown_rich_behavior -v`**
- [ ] **Step 2: Run a broader import-level check if needed**
- [ ] **Step 3: Review diffs for lingering `body_images` or `strip_images_from_html` references**
