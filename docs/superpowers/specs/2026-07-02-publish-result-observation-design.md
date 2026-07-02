# Publish Result Observation and Terminal Failure Tool Design

## 背景

当前发文 agent 在点击最终发布按钮后过早进入 URL 回查。`app/platforms/toutiao/config.py` 和 `app/platforms/sohu/config.py` 的 prompt 多处要求“点击确认发布后不要等待页面成功提示，直接调用 `get_published_article_url`”。同时 `app/publishing/kernel.py` 在检测到确认发布点击后，会立即执行一次失败文本检测，然后等待固定时间并强制调用作品列表回查。

这个流程对“提交已成功但页面没有 URL”的场景有帮助，但对真实平台的发布结果处理过于僵硬：页面可能先出现成功弹窗、失败弹窗、字段校验提示、账号限制提示、验证码/风控提示，或者自动跳转到作品管理页。agent 应先观察页面结果并自行判断下一步，而不是把“点击确认发布”直接等同于“去作品列表找文章”。

另外，当 agent 识别出不可恢复异常时，目前通常会输出自然语言。`_parse_agent_outcome()` 会尝试从文本里抽取 `failure_reason`，但这是兜底路径，不稳定，也不能保证接口一定得到结构化失败原因。

还有一个容易漏掉的现实问题：发布后的 toast / 弹窗提示可能只展示 2-3 秒。`browser-use` 会在每个 step 开始时获取浏览器状态并请求截图，但这不是连续录像，也不是 DOM 事件监听；如果提示在两个 step 之间出现又消失，自动截图和通用 DOM state 都可能错过。因此本设计需要一个发布前安装、发布后读取的页面信号缓存机制。

## 目标

- 点击最终发布按钮后，agent 进入“发布结果观察阶段”，先判断页面信号，再决定修复、成功回查、失败终止或继续观察。
- prompt 只定义关键流程和判断责任，不把所有平台异常写死成枚举状态机。
- 新增结构化工具，让 agent 在发现不可恢复失败时立即终止，并把失败原因稳定交给程序。
- 新增发布结果信号监听/观察工具，为 agent 提供结构化页面证据，但不替 agent 做业务判断。
- 将发布 agent 的视觉输入默认配置改为 `BROWSER_USE_VISION=true`，让截图真正进入支持视觉的 LLM 输入。
- 保留现有 `get_published_article_url`，但只在“已判断成功但缺少 URL”时使用。
- 保持现有 API 响应契约：最终仍通过 job `result.failure_reason` / `error` / `status=failed` 暴露失败。
- 设计和实现必须面向 Docker 部署到 Linux 服务器；不得依赖本地桌面、Windows 路径、Windows shell 或手工浏览器环境。

## 非目标

- 不在本次设计中实现完整的平台规则引擎。
- 不把“今日发文上限”“分类必填”等所有异常写入硬编码分支。
- 不新增外部 HTTP API。
- 不改变远程登录、账号管理、队列串行调度和 cookie 存储模型。
- 不要求一次性重构整个 `app/publishing/kernel.py`。

## browser-use 依据

项目当前锁定 `browser-use 0.13.1`。本地接口确认：

- `Controller` 实际是 `Tools` 的兼容入口。
- 自定义工具通过 `@controller.action(...)` 注册。
- 工具可以返回 `ActionResult`。
- `ActionResult` 支持 `is_done`、`success`、`error`、`extracted_content`、`metadata`。
- `history.final_result()` 读取最后一个 action result 的 `extracted_content`。
- `history.is_successful()` 读取最后一个 done action result 的 `success`。

同时，`browser-use` 在每个 step 开始时会请求 `get_browser_state_summary(include_screenshot=True)`。但截图进入 LLM 消息的条件是 `use_vision is True`，不是“已经请求截图”本身。因此本项目应把 `.env.example`、README 和部署环境中的 `BROWSER_USE_VISION` 从 `auto` 调整为 `true`。`BROWSER_USE_VISION_DETAIL` 仍建议保留 `low`，优先控制图片负载。

因此，不可恢复异常终止工具应返回：

```python
ActionResult(
    is_done=True,
    success=False,
    extracted_content=json.dumps(
        {
            "success": False,
            "article_url": "",
            "failure_reason": reason,
            "publish_signal": "agent_terminal_failure",
            "evidence": evidence,
        },
        ensure_ascii=False,
    ),
    metadata={"publish_signal": "agent_terminal_failure"},
)
```

这会自然接入现有 `_parse_agent_outcome()` 的 JSON 解析路径，比要求 agent 最后手写 JSON 更稳定。

## 设计方案

采用“agent 判断 + 工具结构化”的方案。

### 核心边界

- agent 负责判断：页面当前结果是否成功、可恢复失败、不可恢复失败或不确定。
- `prepare_publish_observer` 负责监听：在点击最终发布按钮前，向当前页面安装 JS 监听器，缓存短暂出现的 toast / modal / error 文案。
- `observe_publish_result` 负责观察：点击发布后读取监听缓存、当前 DOM、当前 URL 和页面标题，并可短轮询几秒收集结果信号。
- `finish_publish_failed` 负责终止：当 agent 判断不可恢复失败时，结构化写入失败原因并停止任务。
- `get_published_article_url` 负责回查：仅在 agent 已经判断发布成功但没有直接拿到文章 URL 时使用。

这条边界保留 agent 的自主性，同时避免自然语言最终输出导致程序拿不到关键原因。

### 发布结果观察阶段

点击最终发布按钮后，agent 必须进入发布结果观察阶段。

规则：

- 不立刻调用 `get_published_article_url`。
- 不立刻调用 `done`。
- 不重复点击最终发布按钮，除非已经修复一个可恢复问题并准备重新提交。
- 点击最终发布按钮前，先调用 `prepare_publish_observer`，用于捕获 2-3 秒内自动消失的提示。
- 点击后优先调用 `observe_publish_result(wait_seconds=6)`，读取监听缓存和当前页面信号。
- 观察页面信号包括：弹窗、toast、字段校验提示、顶部/底部错误、按钮附近提示、URL 跳转、页面标题、是否进入作品管理/内容管理页面。
- 如果页面反馈不稳定，可以短暂等待并再次观察，但同一轮观察应有上限，避免无限等待。

观察阶段的结果由 agent 判断：

- 成功：页面表达已发布、提交成功、进入审核，或跳转/进入作品管理页，或出现本次文章的明确成功证据。
- 可恢复失败：页面提示有字段、选项、分类、栏目、封面、协议勾选等缺失，agent 可以在当前发布页面补齐后继续提交。
- 不可恢复失败：页面表达当前账号或内容在本流程内无法继续发布，例如账号限制、额度用完、无权限、验证码/风控、登录失效、内容被平台拒绝、网络长时间不可用等。
- 不确定：页面没有明确成功或失败信号，agent 应继续观察或读取更多页面证据；达到观察上限仍不确定时，返回失败原因“无法确认发布结果”。

prompt 中可以给非穷举例子，但必须避免把例子写成完整硬编码清单。重点是让 agent 依据页面原文和上下文判断。

## 新增工具

### prepare_publish_observer

用途：在点击最终发布按钮之前安装页面级信号监听器，捕获短暂出现并自动消失的发布结果提示。

建议签名：

```python
async def prepare_publish_observer(browser_session) -> ActionResult:
    ...
```

行为：

- 在当前页面执行 JS，初始化 `window.__publishResultSignals`。
- 使用 `MutationObserver` 监听 `document.body` 的 `childList`、`subtree`、`characterData` 变化。
- 当新增或变化节点文本疑似包含 toast / modal / alert / message / error / form validation 文案时，把文本、时间戳、URL、来源选择器或节点特征写入缓存。
- 缓存保留在页面内存中，直到页面刷新或跳转。若发生同源 SPA 跳转，缓存仍可用；若整页导航导致 JS 上下文重建，`observe_publish_result` 仍要从当前 DOM 补采一次。
- 多次调用必须幂等；已安装时返回已安装状态，不重复挂多个 observer。

返回 JSON：

```json
{
  "ok": true,
  "observer_installed": true,
  "signal_count": 0
}
```

Linux/Docker 约束：

- 监听器只能使用浏览器页面内标准 JS API，不依赖操作系统能力。
- 不写本地临时文件，不调用系统剪贴板，不依赖桌面通知或宿主机窗口系统。
- 通过 Playwright / CDP 在容器内 Chromium 页面执行，适配现有 Docker + Xvnc/Chromium 架构。

### observe_publish_result

用途：为 agent 提供结构化页面证据，并读取 `prepare_publish_observer` 捕获的短暂信号。

建议签名：

```python
async def observe_publish_result(wait_seconds: float = 6.0, browser_session=None) -> ActionResult:
    ...
```

返回 JSON 放入 `extracted_content`，并 `include_in_memory=True`。

建议字段：

```json
{
  "url": "https://...",
  "title": "页面标题",
  "visible_text_excerpt": "页面关键可见文本摘要",
  "captured_signals": [
    {
      "text": "今日发布的文章已达上限",
      "kind": "toast_or_message",
      "url": "https://...",
      "age_seconds": 1.2
    }
  ],
  "dialogs": ["弹窗或 modal 文案"],
  "toasts": ["toast 文案"],
  "form_errors": ["字段附近校验提示"],
  "button_area_text": "发布按钮附近文本",
  "management_page_hint": true,
  "article_title_visible": false,
  "wait_seconds": 6.0,
  "observed_at": "2026-07-02T12:00:00+08:00"
}
```

这个工具只收集证据，不返回 `success/failure` 判断。这样不会把平台规则锁死在程序里，也不会剥夺 agent 的判断空间。

实现策略：

- 进入工具后，在 `wait_seconds` 内每 200-500ms 短轮询一次。
- 每次轮询读取 `window.__publishResultSignals` 中的缓存信号。
- 读取 `session.get_current_page_url()`。
- 读取 `document.title`。
- 从 DOM 中抽取可见文本，优先 modal/toast/alert/message/error/form-item-explain 等常见提示区域。
- 识别当前 URL 或页面文本是否像作品管理/内容管理页，作为 hint，不作为最终判断。
- 控制文本长度，避免把整页内容塞回 LLM。
- 如果发布后页面发生整页跳转，监听缓存可能丢失；工具必须仍从新页面当前 DOM、URL、标题中提取证据。
- 如果监听缓存和当前 DOM 均为空，返回明确的 `signals_found=false`，让 agent 继续观察或按不确定处理。

### finish_publish_failed

用途：不可恢复失败的结构化终止工具。

建议签名：

```python
async def finish_publish_failed(
    reason: str,
    evidence: str = "",
    browser_session=None,
) -> ActionResult:
    ...
```

行为：

- `reason` 必填，优先使用页面原文。
- `evidence` 可选，用于放观察到的弹窗、URL、页面区域摘要。
- 返回 `ActionResult(is_done=True, success=False, extracted_content=<failure_json>)`。
- 不继续点击、不回查作品列表、不再等待。

返回 JSON：

```json
{
  "success": false,
  "article_url": "",
  "account": "",
  "failure_reason": "页面原始失败原因",
  "publish_signal": "agent_terminal_failure",
  "evidence": "可选证据"
}
```

### 可选：finish_publish_success

本次不强制新增。现有 `done(success=true)` 和 `get_published_article_url` 足够表达成功。若后续发现成功 JSON 仍不稳定，可以再引入 `finish_publish_success(article_url, evidence)`。

## Prompt 改造

需要删除或改写所有“点击发布后不要等待页面成功提示，直接调用 `get_published_article_url`”的硬规则。

建议替换为统一规则：

```text
点击最终“发布”或“确认发布”按钮后，进入发布结果观察阶段：

- 点击最终发布按钮前，必须先调用 prepare_publish_observer。
- 点击后不要立刻调用 get_published_article_url。
- 不要立刻 done。
- 不要重复点击发布按钮。
- 点击后调用 observe_publish_result(wait_seconds=6)，读取短暂提示缓存和当前页面证据。
- 你需要根据页面原文和上下文自行判断：成功、可恢复失败、不可恢复失败或不确定。

如果判断已经成功但页面没有直接给出文章 URL，再调用 get_published_article_url。

如果判断是可恢复失败，修复页面提示的问题后重新发布；同一问题最多修复 2 次。

如果判断是不可恢复失败，必须调用 finish_publish_failed(reason=..., evidence=...)。
reason 优先使用页面原文或最接近页面原文的失败原因。
调用 finish_publish_failed 后不要继续操作。
```

平台 prompt 可以补充平台差异，但只作为例子：

- 头条：发布成功可能跳到作品管理页或提示发布/审核成功。
- 搜狐：提交审核成功也属于成功；草稿不是成功。

## kernel 调度调整

当前 `on_step_end` 在确认发布点击后会执行自动 post-confirm lookup 并停止 agent。新设计应改为：

- 保留“确认发布已点击”的 trace 记录。
- 不再自动强制调用 `_lookup_published_article_url()`。
- 可以保留轻量 guard：如果程序在页面文本中看到明确不可恢复失败，可以记录日志，但不直接替 agent 做最终业务判断，除非这是已有安全兜底。
- 允许 agent 在后续 step 调用 `prepare_publish_observer`、`observe_publish_result`、修复字段或调用 `finish_publish_failed`。
- 仍保留正文完整性发布前 guard，因为这是安全性校验，不属于发布结果判断。

为了避免 agent 无限观察，建议在 `publish_guard` 中新增观察计数：

- `post_publish_observation_count`
- `post_publish_repair_count`

初版可以只通过 prompt 限制，不实现强制计数；但测试应覆盖工具终止路径。

## 结果解析

`_parse_agent_outcome()` 当前已支持解析 final JSON。`finish_publish_failed` 的 `extracted_content` 应直接被解析为失败结果。

建议补充两点：

- 若 `history.is_successful() is False` 且 final JSON 中有 `failure_reason`，优先使用 final JSON 的业务原因。
- `publish_signal` 应透传到 result，便于后续账号状态策略使用。

现有 `_build_post_confirm_lookup_result()` 可保留，但触发时机改为 agent 调用 `get_published_article_url` 或显式成功后兜底，而不是点击确认后强制触发。

## 测试策略

### 单元测试

- `prepare_publish_observer` 注入脚本幂等，重复调用不会安装多个 observer。
- 模拟 2 秒后消失的 toast：先安装 observer，再插入并删除 DOM 节点，随后 `observe_publish_result` 仍能从缓存读到文本。
- `finish_publish_failed` 返回 `ActionResult`：
  - `is_done=True`
  - `success=False`
  - `extracted_content` 是合法 JSON
  - JSON 中包含 `success=false`、`failure_reason`、`publish_signal=agent_terminal_failure`
- `_parse_agent_outcome()` 能从终止工具 JSON 中读到失败原因。
- prompt 不再包含“点击确认发布后直接调用 get_published_article_url”的硬规则。
- prompt 包含“发布结果观察阶段”“点击前调用 prepare_publish_observer”“不可恢复失败必须调用 finish_publish_failed”。
- `observe_publish_result` 的 DOM 提取逻辑对模拟页面返回 dialogs/toasts/form_errors。

### 集成级假对象测试

- 模拟 agent 调用 `finish_publish_failed` 后，`PublishService._run_agent()` 返回失败 result。
- 模拟 `PublishAgent._publish_with_cookie()` 接收到该 result 后，job 进入 `failed`，`error` 为工具传入原因。

### 回归风险测试

- 正文未写完整时仍被发布前 guard 阻止。
- 已成功但无 URL 的场景仍可通过 `get_published_article_url` 成功返回。
- 搜狐草稿命中仍不能被当成成功。

## 风险与缓解

- 风险：agent 不主动调用 `observe_publish_result`。
  - 缓解：prompt 中在发布后观察阶段明确要求调用该工具；工具描述写清楚“点击最终发布后用于读取页面结果信号”。

- 风险：短暂 toast 在 `observe_publish_result` 调用前消失。
  - 缓解：点击最终发布按钮前必须调用 `prepare_publish_observer`，通过 `MutationObserver` 缓存短暂文案。

- 风险：agent 判断不可恢复失败但不用终止工具，仍自然语言输出。
  - 缓解：prompt 写“不可恢复失败必须调用 `finish_publish_failed`”；测试检查 prompt 文案。

- 风险：程序不再自动 post-confirm lookup，成功链路变慢或偶尔错过。
  - 缓解：prompt 明确“判断成功但无 URL 时调用 `get_published_article_url`”；保留 URL 回查工具。

- 风险：观察工具返回过多文本导致上下文膨胀。
  - 缓解：DOM 提取分区摘要并限制长度，优先提示区域。

## 实施顺序

1. 在 `_build_publish_tools()` 中新增 `observe_publish_result` 和 `finish_publish_failed`。
2. 在 `_build_publish_tools()` 中新增 `prepare_publish_observer`，用页面内 `MutationObserver` 缓存短暂发布结果信号。
3. 修改 `.env.example`、README 和部署文档，将 `BROWSER_USE_VISION` 默认值改为 `true`，保留 `BROWSER_USE_VISION_DETAIL=low`。
4. 修改头条、搜狐 prompt，删除“直接调用 `get_published_article_url`”硬规则，加入发布结果观察阶段。
5. 调整 `kernel.py` 的 post-confirm guard，不再点击确认后强制查作品列表并停止 agent。
6. 补充 result 解析和 `publish_signal` 透传测试。
7. 运行相关单元测试与 `py_compile`。

## 验收标准

- 点击最终发布后，prompt 不再指示 agent 立即查作品列表。
- 点击最终发布前，prompt 指示 agent 安装发布结果监听器。
- 2-3 秒自动消失的 toast / 弹窗文案能被监听缓存捕获，并通过 `observe_publish_result` 返回给 agent。
- Docker/Linux 部署不依赖任何 Windows 路径、Windows shell、宿主机桌面通知或人工打开浏览器。
- `BROWSER_USE_VISION=true` 成为模板和文档里的发布 agent 默认配置。
- agent 有明确工具可用于不可恢复失败的结构化终止。
- 不可恢复失败能通过 job 查询接口稳定看到失败状态和原因。
- 成功但缺 URL 的场景仍能通过作品列表回查拿到 `article_url`。
- 现有正文写入和发布前正文完整性保护不回退。
