from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Any

from app.core.config import (
    AGENT_VERBOSE_LOG_ENABLED,
    AGENT_VERBOSE_LOG_MAX_CHARS,
    AGENT_VERBOSE_LOG_MESSAGE_MAX_CHARS,
)


_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|secret|password|token|key)\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
]


def redact_sensitive_text(value: str) -> str:
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    return text


def truncate_text(value: Any, max_chars: int) -> str:
    text = redact_sensitive_text(str(value))
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n[truncated original_chars={len(text)} max_chars={max_chars}]"


def message_to_text(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content", ""))
    content = getattr(message, "content", None)
    if content is not None:
        return str(content)
    return str(message)


def message_type_name(message: Any) -> str:
    if isinstance(message, dict):
        return "dict"
    return type(message).__name__


def message_role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "role", "") or "")


class AgentDiagnosticsLogger:
    def __init__(
        self,
        logger,
        *,
        enabled: bool = AGENT_VERBOSE_LOG_ENABLED,
        max_chars: int = AGENT_VERBOSE_LOG_MAX_CHARS,
        message_max_chars: int = AGENT_VERBOSE_LOG_MESSAGE_MAX_CHARS,
    ):
        self.logger = logger
        self.enabled = enabled
        self.max_chars = max_chars
        self.message_max_chars = message_max_chars

    def log_llm_call(
        self,
        *,
        call_id: int,
        model: str,
        output_format: str,
        messages: list[Any],
        output: Any,
        usage: dict[str, int],
        elapsed_ms: int,
    ) -> None:
        if not self.enabled:
            return
        self.logger.info(
            "[AgentLLM:BEGIN] call=%s model=%s output_format=%s message_count=%s",
            call_id,
            model,
            output_format,
            len(messages),
        )
        for index, message in enumerate(messages):
            raw_text = message_to_text(message)
            role = message_role(message)
            role_part = f" role={role}" if role else ""
            self.logger.info(
                "[AgentLLM:INPUT] call=%s message_index=%s type=%s%s chars=%s",
                call_id,
                index,
                message_type_name(message),
                role_part,
                len(str(raw_text)),
            )
            self.logger.info(truncate_text(raw_text, self.message_max_chars))
        output_text = self._safe_serialize(output)
        self.logger.info("[AgentLLM:OUTPUT] call=%s chars=%s", call_id, len(output_text))
        self.logger.info(truncate_text(output_text, self.max_chars))
        self.logger.info(
            "[AgentLLM:TOKEN] call=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s elapsed_ms=%s",
            call_id,
            int(usage.get("prompt_tokens", 0) or 0),
            int(usage.get("completion_tokens", 0) or 0),
            int(usage.get("total_tokens", 0) or 0),
            elapsed_ms,
        )
        self.logger.info("[AgentLLM:END] call=%s", call_id)

    def log_step(
        self,
        *,
        step: int,
        history_count: int,
        action_summary: str,
        result_summary: str,
    ) -> None:
        if not self.enabled:
            return
        action_text = action_summary or "(empty)"
        result_text = result_summary or "(empty)"
        self.logger.info("[AgentStep:BEGIN] step=%s history_items=%s", step, history_count)
        self.logger.info("[AgentStep:ACTION] step=%s chars=%s", step, len(action_text))
        self.logger.info(truncate_text(action_text, self.max_chars))
        self.logger.info("[AgentStep:RESULT] step=%s chars=%s", step, len(result_text))
        self.logger.info(truncate_text(result_text, self.max_chars))
        self.logger.info("[AgentStep:END] step=%s", step)

    def log_page_state(self, *, step: int, page_state: dict[str, Any]) -> None:
        if not self.enabled:
            return
        url = page_state.get("url", "") or ""
        title = page_state.get("title", "") or ""
        editor_len = page_state.get("editor_text_length", 0) or 0
        editor_source = page_state.get("editor_source", "none") or "none"
        probe_found = bool(page_state.get("probe_found", False))
        preview = page_state.get("editor_text_preview", "") or ""
        self.logger.info(
            "[AgentState:PAGE] step=%s url=%s title=%r",
            step,
            truncate_text(url, 1000),
            truncate_text(title, 1000),
        )
        self.logger.info(
            "[AgentState:EDITOR] step=%s editor_len=%s editor_source=%s probe_found=%s preview=%r",
            step,
            editor_len,
            editor_source,
            probe_found,
            truncate_text(preview, self.max_chars),
        )

    def log_tool_call(self, *, name: str, **fields: Any) -> None:
        if not self.enabled:
            return
        self.logger.info("[AgentTool:CALL] name=%s %s", name, self._format_fields(fields))

    def log_tool_result(self, *, name: str, **fields: Any) -> None:
        if not self.enabled:
            return
        self.logger.info("[AgentTool:RESULT] name=%s %s", name, self._format_fields(fields))

    def log_tool_event(self, *, event: str, name: str, **fields: Any) -> None:
        if not self.enabled:
            return
        self.logger.info("[AgentTool:%s] name=%s %s", event, name, self._format_fields(fields))

    def log_guard(self, *, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        self.logger.info("[AgentGuard:%s] %s", event, self._format_fields(fields))

    def log_final_summary(
        self,
        *,
        success: bool,
        steps: int,
        article_url: str,
        failure_reason: str,
        state: dict[str, Any],
        llm_usage: dict[str, int],
    ) -> None:
        if not self.enabled:
            return
        self.logger.info(
            "[AgentFinal:SUMMARY] success=%s steps=%s article_url=%r failure_reason=%r",
            success,
            steps,
            article_url or "",
            failure_reason or "",
        )
        self.logger.info(
            "[AgentFinal:STATE] last_url=%r last_editor_len=%s last_probe_found=%s",
            state.get("last_url", "") or "",
            state.get("last_editor_len", 0) or 0,
            bool(state.get("last_probe_found", False)),
        )
        self.logger.info(
            "[AgentFinal:LLM_USAGE] calls=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            int(llm_usage.get("calls", 0) or 0),
            int(llm_usage.get("prompt_tokens", 0) or 0),
            int(llm_usage.get("completion_tokens", 0) or 0),
            int(llm_usage.get("total_tokens", 0) or 0),
        )

    def timed_call_start(self) -> float:
        return perf_counter()

    def elapsed_ms(self, started_at: float) -> int:
        return int((perf_counter() - started_at) * 1000)

    def _format_fields(self, fields: dict[str, Any]) -> str:
        if not fields:
            return ""
        parts = []
        for key, value in fields.items():
            parts.append(f"{key}={truncate_text(value, self.max_chars)!r}")
        return " ".join(parts)

    def _safe_serialize(self, value: Any) -> str:
        try:
            if hasattr(value, "model_dump"):
                return json.dumps(value.model_dump(), ensure_ascii=False, default=str)
            if hasattr(value, "__dict__") and not isinstance(value, (str, bytes)):
                return str(value)
            return str(value)
        except Exception:
            return str(value)
