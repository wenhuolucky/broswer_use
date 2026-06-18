from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any


_MAX_TEXT = 240


def compact_text(value: Any, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def quote_value(value: Any, limit: int = _MAX_TEXT) -> str:
    return '"' + compact_text(value, limit).replace('"', "'") + '"'


@dataclass
class AgentTrace:
    step_line: str
    think_line: str
    action_line: str
    search_line: str
    result_line: str
    page_line: str
    repeat_warnings: list[str] = field(default_factory=list)
    action_type: str = ""
    action_signature: str = ""
    intent: str = "未识别"


@dataclass
class AgentTraceState:
    repeat_window: int = 8
    recent_actions: deque[dict[str, Any]] = field(default_factory=deque)
    repeat_counts: dict[str, int] = field(default_factory=dict)

    def remember(self, item: dict[str, Any]) -> None:
        self.recent_actions.append(item)
        while len(self.recent_actions) > self.repeat_window:
            old = self.recent_actions.popleft()
            sig = str(old.get("signature", "") or "")
            if sig in self.repeat_counts:
                self.repeat_counts[sig] = max(0, self.repeat_counts[sig] - 1)
                if self.repeat_counts[sig] == 0:
                    self.repeat_counts.pop(sig, None)
        sig = str(item.get("signature", "") or "")
        if sig:
            self.repeat_counts[sig] = self.repeat_counts.get(sig, 0) + 1


def model_output_to_dict(model_output: Any) -> dict[str, Any]:
    if model_output is None:
        return {}
    if isinstance(model_output, dict):
        return model_output
    if hasattr(model_output, "model_dump"):
        return model_output.model_dump(exclude_none=True, mode="json")
    return {
        "thinking": getattr(model_output, "thinking", None),
        "evaluation_previous_goal": getattr(model_output, "evaluation_previous_goal", None),
        "memory": getattr(model_output, "memory", None),
        "next_goal": getattr(model_output, "next_goal", None),
        "current_plan_item": getattr(model_output, "current_plan_item", None),
        "plan_update": getattr(model_output, "plan_update", None),
        "action": getattr(model_output, "action", None),
    }


def results_to_dicts(results: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for result in results or []:
        if isinstance(result, dict):
            out.append(result)
        elif hasattr(result, "model_dump"):
            out.append(result.model_dump(exclude_none=True, mode="json"))
        else:
            out.append(
                {
                    "extracted_content": getattr(result, "extracted_content", None),
                    "long_term_memory": getattr(result, "long_term_memory", None),
                    "error": getattr(result, "error", None),
                }
            )
    return out


def first_action(model_output: dict[str, Any]) -> dict[str, Any]:
    actions = model_output.get("action") or []
    if isinstance(actions, dict):
        return actions
    if isinstance(actions, list) and actions:
        action = actions[0]
        if isinstance(action, dict):
            return action
        if hasattr(action, "model_dump"):
            return action.model_dump(exclude_none=True, mode="json")
    return {}


def normalize_action(action: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    if not action:
        return "none", {}, "none"
    key = next(iter(action.keys()))
    params = action.get(key) or {}
    if not isinstance(params, dict):
        params = {}

    action_type = key
    if key.endswith("_action"):
        action_type = key.removesuffix("_action")

    if key == "navigate":
        signature = f"navigate:{params.get('url', '')}"
    elif key == "click":
        signature = f"click:index={params.get('index', '')}"
    elif key == "scroll":
        signature = f"scroll:{params.get('down', '')}:{params.get('pages', '')}"
    elif key == "find_elements":
        signature = f"find_elements:{params.get('selector', '')}"
    elif key == "wait":
        signature = f"wait:{params.get('seconds', '')}"
    elif key == "input":
        signature = f"input:index={params.get('index', '')}"
    elif key == "upload_file":
        signature = f"upload_file:index={params.get('index', '')}"
    elif key == "get_published_article_url":
        signature = "tool:get_published_article_url"
    elif key == "done":
        signature = "done"
    else:
        signature = f"{key}:{compact_text(params, 80)}"
    return action_type, params, signature


def infer_intent(model_output: dict[str, Any], action_type: str, params: dict[str, Any]) -> tuple[str, str, str]:
    haystack = " ".join(
        compact_text(model_output.get(k), 500)
        for k in ("thinking", "evaluation_previous_goal", "memory", "next_goal")
    )
    selector = str(params.get("selector", "") or "")
    haystack_with_params = f"{haystack} {selector}".lower()

    if any(marker in haystack_with_params for marker in ("分类", "category", "发文设置")):
        return "查找分类/发文设置", selector or "分类|category|发文设置", "medium"
    if any(marker in haystack_with_params for marker in ("封面", "cover", "确定")):
        return "处理封面/确认弹窗", selector or "封面|cover|确定", "medium"
    if any(marker in haystack_with_params for marker in ("确认发布", "预览并发布", "publish")):
        return "查找发布按钮", selector or "确认发布|预览并发布|publish", "medium"
    if "article_url" in haystack_with_params or "作品" in haystack_with_params:
        return "获取发布后文章链接", selector or "article_url|作品", "medium"
    if action_type == "find_elements" and selector:
        return "查找页面元素", selector, "low"
    if action_type == "wait":
        return "等待页面变化", str(params.get("seconds", "") or ""), "low"
    return "未识别", "", "low"


def action_params_text(action_type: str, params: dict[str, Any]) -> str:
    if action_type == "scroll":
        return f"direction={'down' if params.get('down') else 'up'} pages={params.get('pages', '')}"
    if action_type == "click":
        return f"index={params.get('index', '')}"
    if action_type == "find_elements":
        return f"selector={quote_value(params.get('selector', ''), 180)} max_results={params.get('max_results', '')}"
    if action_type == "wait":
        return f"seconds={params.get('seconds', '')}"
    if action_type == "input":
        text = str(params.get("text", "") or "")
        return f"index={params.get('index', '')} text_len={len(text)} text_preview={quote_value(text, 80)}"
    if action_type == "upload_file":
        return f"index={params.get('index', '')} path={quote_value(params.get('path', ''), 160)}"
    if action_type == "navigate":
        return f"url={quote_value(params.get('url', ''), 180)}"
    if action_type == "get_published_article_url":
        return f"title={quote_value(params.get('title', ''), 120)}"
    if action_type == "done":
        return f"text={quote_value(params.get('text', ''), 180)}"
    return compact_text(params, 180)


def result_summary(results: list[dict[str, Any]]) -> tuple[str, str, str]:
    status = "ok"
    extracted_parts: list[str] = []
    error_parts: list[str] = []
    for result in results:
        if result.get("error"):
            status = "error"
            error_parts.append(str(result.get("error")))
        for key in ("extracted_content", "long_term_memory"):
            value = result.get(key)
            if value:
                extracted_parts.append(str(value))
    if not extracted_parts and status == "ok":
        status = "empty"
    return status, compact_text(" | ".join(extracted_parts), 240), compact_text(" | ".join(error_parts), 240)


def progress_state(page_state: dict[str, Any], previous: dict[str, Any] | None) -> str:
    if previous is None:
        return "unknown"
    keys = ("url", "editor_text_length", "probe_found", "editor_source")
    for key in keys:
        if page_state.get(key) != previous.get(key):
            return "yes"
    return "no"


def detect_repeats(
    *,
    step_number: int,
    state: AgentTraceState,
    signature: str,
    action_type: str,
    intent: str,
    progress: str,
) -> list[str]:
    warnings: list[str] = []
    count = state.repeat_counts.get(signature, 0)
    if action_type == "click" and count >= 2:
        warnings.append(
            f"[AgentRepeat] step={step_number} level=warning pattern=same_click "
            f"count={count} target={quote_value(signature, 120)} progress={progress}"
        )
    if action_type == "find_elements" and count >= 2:
        warnings.append(
            f"[AgentRepeat] step={step_number} level=warning pattern=selector_not_found "
            f"count={count} target={quote_value(signature, 160)} intent={quote_value(intent, 80)} progress={progress}"
        )

    recent = list(state.recent_actions)
    scroll_count = sum(1 for item in recent if item.get("action_type") == "scroll")
    same_url = len({item.get("url") for item in recent if item.get("url")}) <= 1
    if action_type == "scroll" and scroll_count >= 3 and same_url and progress == "no":
        warnings.append(
            f"[AgentRepeat] step={step_number} level=warning pattern=scroll_loop "
            f"count={scroll_count} window={state.repeat_window} intent={quote_value(intent, 80)} progress={progress}"
        )
    return warnings


def build_step_trace(
    *,
    step_number: int,
    model_output: Any,
    results: Any,
    page_state: dict[str, Any],
    duration_seconds: float | None,
    state: AgentTraceState,
) -> AgentTrace:
    output = model_output_to_dict(model_output)
    result_dicts = results_to_dicts(results)
    action = first_action(output)
    action_type, params, signature = normalize_action(action)
    intent, query, confidence = infer_intent(output, action_type, params)

    previous_page = state.recent_actions[-1].get("page_state") if state.recent_actions else None
    progress = progress_state(page_state, previous_page)
    duration = f"{duration_seconds:.2f}s" if duration_seconds is not None else "unknown"
    url = str(page_state.get("url", "") or "")
    step_line = (
        f"[AgentStep] step={step_number} phase=running duration={duration} "
        f"url={url} action={action_type} progress={progress}"
    )
    think_line = (
        f"[AgentThink] step={step_number} "
        f"previous={quote_value(output.get('evaluation_previous_goal', ''), 180)} "
        f"next={quote_value(output.get('next_goal', ''), 180)} "
        f"memory={quote_value(output.get('memory', ''), 180)} "
        f"thinking={quote_value(output.get('thinking', ''), 240)}"
    )
    action_line = (
        f"[AgentAction] step={step_number} type={action_type} "
        f"{action_params_text(action_type, params)} reason={quote_value(intent, 100)}"
    )
    search_line = (
        f"[AgentSearch] step={step_number} intent={quote_value(intent, 100)} "
        f"query={quote_value(query, 180)} source=agent_output confidence={confidence}"
    )
    if action_type == "find_elements":
        search_line += f" selector={quote_value(params.get('selector', ''), 180)}"

    status, extracted, error = result_summary(result_dicts)
    result_line = (
        f"[AgentResult] step={step_number} status={status} "
        f"extracted={quote_value(extracted, 240)} error={quote_value(error, 180)}"
    )
    page_line = (
        f"[AgentPage] step={step_number} url={url} "
        f"editor_source={page_state.get('editor_source', 'none')} "
        f"editor_len={page_state.get('editor_text_length', 0) or 0} "
        f"probe_found={str(bool(page_state.get('probe_found', False))).lower()} "
        f"preview={quote_value(page_state.get('editor_text_preview', ''), 120)}"
    )

    state.remember(
        {
            "step": step_number,
            "signature": signature,
            "action_type": action_type,
            "intent": intent,
            "url": url,
            "page_state": dict(page_state),
        }
    )
    repeat_warnings = detect_repeats(
        step_number=step_number,
        state=state,
        signature=signature,
        action_type=action_type,
        intent=intent,
        progress=progress,
    )

    return AgentTrace(
        step_line=step_line,
        think_line=think_line,
        action_line=action_line,
        search_line=search_line,
        result_line=result_line,
        page_line=page_line,
        repeat_warnings=repeat_warnings,
        action_type=action_type,
        action_signature=signature,
        intent=intent,
    )


def step_duration_seconds(history_item: Any) -> float | None:
    metadata = getattr(history_item, "metadata", None)
    if metadata is None:
        return None
    duration = getattr(metadata, "duration_seconds", None)
    if duration is None:
        return None
    try:
        return float(duration)
    except (TypeError, ValueError):
        return None


def log_step_trace(
    *,
    logger: Any,
    step_number: int,
    history_item: Any,
    page_state: dict[str, Any],
    state: AgentTraceState,
) -> AgentTrace:
    trace = build_step_trace(
        step_number=step_number,
        model_output=getattr(history_item, "model_output", None),
        results=getattr(history_item, "result", None) or [],
        page_state=page_state,
        duration_seconds=step_duration_seconds(history_item),
        state=state,
    )
    logger.info(trace.step_line)
    logger.info(trace.think_line)
    logger.info(trace.action_line)
    logger.info(trace.search_line)
    logger.info(trace.result_line)
    logger.info(trace.page_line)
    for warning in trace.repeat_warnings:
        logger.warning(warning)
    return trace


def llm_trace_line(
    *,
    call: int,
    step: int | None,
    duration_seconds: float,
    model: str,
    usage: dict[str, int],
) -> str:
    step_text = step if step is not None else "unknown"
    return (
        f"[AgentLLM] call={call} step={step_text} duration={duration_seconds:.2f}s "
        f"model={model} input_tokens={usage.get('prompt_tokens', 0)} "
        f"output_tokens={usage.get('completion_tokens', 0)} total_tokens={usage.get('total_tokens', 0)}"
    )
