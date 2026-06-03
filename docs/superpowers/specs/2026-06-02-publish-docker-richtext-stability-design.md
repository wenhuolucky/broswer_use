# Publish Docker Rich Text Stability Design

## Metadata

- Date: 2026-06-02
- Scope: `publish_service` rich-text publish path in local and Docker runtime
- Goal: make the Dockerized Toutiao publish flow use the same effective rich-text path as local runs, and stop hidden fallback behavior from masking root causes
- Non-goals:
  - do not redesign the phase-1 Docker API shape
  - do not add cross-account concurrency in this change
  - do not restore body-image upload as a separate feature
  - do not package cookie acquisition / login service

## Problem Statement

Current behavior diverges between local runs and the Dockerized wrapper for the same article and the same Toutiao account.

Observed facts:

1. local runs can complete with the intended path:
   - write HTML into browser clipboard
   - paste via `Ctrl+V`
   - Toutiao editor renders headings, lists, bold text, and images through the paste flow
2. Docker runs can fail much earlier in the content import path:
   - browser clipboard write fails with `JavaScript execution error: Uncaught`
   - the agent silently degrades to direct `editor.innerHTML = ...`
   - Toutiao then reports errors such as `图片uri非法`, `保存失败`, and `段落字数过多`
3. the worktree used to build the Docker service is not fully aligned with the current local `publish_service` code
4. the problematic article content is not well-structured Markdown; its generated HTML contains no `<p>` or `<br>` and is effectively one long text blob with inline `<img>`

This means the current failure is not a pure Docker packaging problem. It is a combination of:

- code-version drift between local and Docker build source
- runtime-path drift between clipboard paste and DOM injection
- weak Markdown normalization for pseudo-Markdown input

## Design Summary

This change fixes the issue in three coordinated layers:

1. source alignment
   - make the Docker worktree build from the same `publish_service` behavior that local testing has already validated
2. import-path hardening
   - enforce a single Toutiao rich-text import path: browser clipboard HTML write plus `Ctrl+V`
   - remove silent fallback to direct editor DOM injection for Toutiao
3. Markdown structure normalization
   - pre-normalize pseudo-Markdown long text into real block structure before HTML generation so Toutiao receives valid paragraph-level rich text instead of one oversized paragraph

The result should be:

- local and Docker runs use the same effective content-import contract
- Docker failures become explicit and diagnosable instead of mutating into a different path
- generated HTML better matches Toutiao editor constraints

## Why This Shape

There are three possible approaches.

### Approach 1: Docker-only environment tweaks

Keep current content generation and agent behavior, and only try to fix Linux container clipboard support.

Pros:

- smallest code diff
- fastest to try

Cons:

- does not fix source drift
- does not fix malformed pseudo-Markdown
- still leaves hidden fallback behavior in place
- future regressions remain hard to explain

### Approach 2: force DOM injection as the official path

Accept that clipboard is fragile in Docker and standardize on `innerHTML` injection.

Pros:

- simpler browser interaction
- less dependence on clipboard permissions

Cons:

- directly contradicts the current Toutiao prompt contract
- bypasses the editor's normal rich-text paste handling
- strongly correlates with the current image and paragraph validation failures

### Approach 3: unify the content path and fail loudly

Align source code, keep the clipboard paste path as the only supported Toutiao rich-text import method, and normalize weak Markdown before conversion.

Pros:

- matches current local success path
- preserves editor-native rich-text ingestion
- makes container failures observable instead of hidden
- addresses both environment drift and content-structure drift

Cons:

- requires changes across prompt, service, and tests
- may surface clipboard-runtime failures earlier instead of masking them

### Recommendation

Use Approach 3.

The current evidence already shows that the main local-vs-Docker difference is not “Docker cannot publish”, but that Docker currently falls off the intended path and enters a less compatible one. The correct response is to preserve the intended path and make its prerequisites explicit.

## Detailed Design

## 1. Source Alignment

### Current issue

The Docker wrapper is built from `C:\program001\browser_use_demo\.worktrees\publish-docker-phase1`, while the local validated behavior exists in `C:\program001\browser_use_demo`.

At minimum, the current drift includes:

- richer cookie normalization in local `publish_service/publish_service.py`
- different LLM wrapper selection
- different cookie parsing behavior
- different browser setup robustness

### Design

Before touching behavior, explicitly align the Docker worktree copy of the following files with the current local versions that represent the accepted source of truth:

- `publish_service/publish_service.py`
- `publish_service/deepseek_llm.py`
- `publish_service/fix_cookie.py`
- any tests required to prove the behavior

The Docker wrapper under `publish_docker/` remains separate, but it must call a `PublishService` implementation that matches the local verified code path.

### Expected outcome

- when the same request is submitted locally and through Docker, both flows start from the same parsing and browser-launch behavior
- future comparisons become meaningful because “different source version” is removed as a variable

## 2. Toutiao Rich-Text Import Path Hardening

### Current issue

The prompt already says the Toutiao path must be:

- write HTML to browser clipboard
- focus the editor
- paste with `Ctrl+V`

However, when clipboard write fails in Docker, the agent currently falls back to directly mutating editor DOM. That produces a visually plausible editor state but not the same semantic state as native paste.

### Design

For Toutiao rich-text publishing, define a single supported import contract:

1. convert Markdown to HTML
2. write HTML into browser clipboard using the existing clipboard script
3. paste through `Ctrl+V`
4. verify the editor now contains block-level rich content

If step 2 or step 3 fails, the run must stop with an explicit failure reason instead of degrading to `innerHTML`.

Required changes:

- strengthen prompt wording in `platforms/toutiao.py`
- explicitly forbid fallback to direct DOM/`innerHTML` insertion for Toutiao
- add result-validation wording so the agent must verify real paste outcome, not just visible text
- update service-side result parsing to preserve the clipboard failure reason

### Failure contract

Representative explicit failures:

- `clipboard_html_write_failed`
- `richtext_paste_failed`
- `richtext_structure_not_rendered`

The exact JSON shape can follow the current result schema, but the failure reason must remain machine-readable and log-visible.

### Expected outcome

- Docker failures stop earlier and more honestly
- no more “looks pasted but later fails in preview for unclear reasons” caused by unsupported injection
- local and Docker paths become behaviorally equivalent

## 3. Markdown Structure Normalization

### Current issue

The problematic article is labeled as Markdown, but its generated HTML contains:

- `p_count = 0`
- `br_count = 0`
- `img_count = 2`

This means the source text is not being interpreted as structured Markdown. It is mostly continuous prose with inline image tags and numbered phrases embedded in one block.

Toutiao then interprets the content as oversized paragraph material and can reject it with `段落字数过多`.

### Design

Introduce a normalization pass before `markdown_to_html()` for pseudo-Markdown input.

The normalization pass should:

1. preserve valid Markdown syntax when the content is already well-formed
2. split long plain-text blocks into paragraphs using conservative heuristics
3. force blank-line separation around image lines
4. convert inline “1. ... 2. ... 3. ...” sequences into actual list blocks only when confidently detected
5. preserve network image URLs exactly as-is

### Heuristics

Use low-risk rules only:

- if a line contains only image syntax, surround it with blank lines
- if a long line exceeds a paragraph threshold and contains Chinese full-stop segmentation opportunities, split into multiple logical paragraphs
- if numbered items appear as repeated `N.` segments within one line, convert them into multiline ordered-list entries only when at least two consecutive items are detected
- do not invent headings, emphasis, or list structure when no clear signal exists

### Output goal

The generated HTML should contain real block structure where appropriate:

- `<p>` for prose paragraphs
- `<ol>/<li>` or `<ul>/<li>` for real list-like content
- preserved `<img src="https://...">` for network images

### Expected outcome

- better compatibility with Toutiao paragraph validation
- consistent rendering of headings, lists, line breaks, and emphasis when the input is close to Markdown but not fully valid

## Component Changes

### `publish_service/publish_service.py`

- align with the local validated implementation
- ensure failure reasons from clipboard and paste stages are preserved
- keep `body_image_instruction=""` and the current single-path image policy

### `publish_service/markdown_to_rich.py`

- add a pre-normalization step before Markdown-to-HTML conversion
- add tests for pseudo-Markdown long text, network images, headings, bold text, and ordered lists

### `platforms/toutiao.py`

- keep the single-path prompt
- tighten instructions so DOM injection is explicitly disallowed for Toutiao
- require verification of paste-rendered structure rather than visual text presence alone

### `publish_docker/`

- no API redesign
- no worker-model redesign
- rebuild only after source alignment and tests pass

## Logging and Diagnostics

The current logs are useful but still allow ambiguous interpretation. Add structured logging around the rich-text import path:

- whether clipboard permission grant succeeded
- clipboard write method and result
- whether `Ctrl+V` was sent
- whether block tags were observed after paste
- whether the run terminated due to explicit rich-text import failure

This should be enough to answer:

- did clipboard write succeed
- did paste happen
- did Toutiao render rich blocks
- did failure happen before preview or during preview validation

## Testing Plan

## Unit Tests

1. `markdown_to_html()` preserves network images after normalization
2. pseudo-Markdown long text produces paragraph-level HTML instead of a single blob
3. list-like inline numbering can become ordered-list HTML when safe
4. Toutiao prompt still contains the clipboard-plus-paste contract and explicitly excludes DOM injection

## Integration Tests

1. existing Docker API tests continue to pass
2. existing DeepSeek wrapper tests continue to pass
3. publish-service Markdown behavior tests are expanded to cover pseudo-Markdown normalization

## Manual Verification

Run the same article through:

1. local `publish_service`
2. Dockerized `publish_docker`

Verify that:

- both use the same `PublishService` source behavior
- both log clipboard write and `Ctrl+V`
- neither falls back to `innerHTML`
- generated HTML contains expected block tags

## Risks

1. Linux container clipboard support may still be unstable for some Chrome/runtime combinations
   - this is acceptable because the system should now fail explicitly instead of mutating behavior
2. Markdown normalization heuristics may over-split some valid prose
   - keep rules conservative and cover them with tests
3. Toutiao editor behavior may change over time
   - explicit logging makes future regressions diagnosable

## Out of Scope

- concurrent publishing redesign
- remote browser visibility tooling
- restoring body-image upload
- multi-platform prompt redesign beyond Toutiao

## Success Criteria

This design is considered successful when all of the following are true:

1. Docker and local runs use aligned `PublishService` behavior
2. Toutiao rich-text publishing no longer silently falls back to DOM injection
3. pseudo-Markdown long-form input produces paragraph-capable HTML
4. the same article can be compared across local and Docker without source-version ambiguity
5. logs clearly identify whether failure is due to clipboard, paste, structure, or downstream Toutiao validation
