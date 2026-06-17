from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, TypeVar, overload

from openai.types.chat.chat_completion import ChatCompletion
from pydantic import BaseModel

from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.messages import BaseMessage
from browser_use.llm.openai.chat import ChatOpenAI
from browser_use.llm.views import ChatInvokeCompletion

T = TypeVar("T", bound=BaseModel)


def extract_json_payload(raw: str) -> str:
    text = raw.strip()
    if not text:
        return text

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
        if candidate:
            return candidate

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            _, end = decoder.raw_decode(text[index:])
            return text[index : index + end]
        except json.JSONDecodeError:
            continue

    return text


def parse_structured_completion(response: ChatCompletion | Any, output_format: type[T]) -> ChatInvokeCompletion[T]:
    choice = response.choices[0] if getattr(response, "choices", None) else None
    if choice is None:
        raise ModelProviderError(
            message="Invalid OpenAI chat completion response: missing or empty `choices`.",
            status_code=502,
            model=getattr(response, "model", "unknown"),
        )

    raw_content = getattr(choice.message, "content", None)
    if raw_content is None:
        raise ModelProviderError(
            message="Failed to parse structured output from model response",
            status_code=500,
            model=getattr(response, "model", "unknown"),
        )

    raw_payload = extract_json_payload(str(raw_content))
    parsed = output_format.model_validate_json(raw_payload)

    return ChatInvokeCompletion(
        completion=parsed,
        usage=None,
        stop_reason=getattr(choice, "finish_reason", None),
    )


@dataclass
class OpenAICompatibleChat(ChatOpenAI):
    @overload
    async def ainvoke(
        self, messages: list[BaseMessage], output_format: None = None, **kwargs: Any
    ) -> ChatInvokeCompletion[str]: ...

    @overload
    async def ainvoke(
        self, messages: list[BaseMessage], output_format: type[T], **kwargs: Any
    ) -> ChatInvokeCompletion[T]: ...

    async def ainvoke(
        self, messages: list[BaseMessage], output_format: type[T] | None = None, **kwargs: Any
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        if output_format is None:
            return await super().ainvoke(messages, output_format=None, **kwargs)

        openai_messages = self._prepare_messages(messages, output_format)
        model_params = self._prepare_model_params()

        response = await self.get_client().chat.completions.create(
            model=self.model,
            messages=openai_messages,
            **model_params,
        )

        usage = self._get_usage(response)
        parsed = parse_structured_completion(response, output_format)
        parsed.usage = usage
        return parsed

    def _prepare_messages(self, messages: list[BaseMessage], output_format: type[T]) -> list[dict[str, Any]]:
        from browser_use.llm.openai.serializer import OpenAIMessageSerializer
        from browser_use.llm.schema import SchemaOptimizer
        from openai.types.chat import ChatCompletionContentPartTextParam

        openai_messages = OpenAIMessageSerializer.serialize_messages(messages)
        response_schema = {
            "name": "agent_output",
            "strict": True,
            "schema": SchemaOptimizer.create_optimized_json_schema(
                output_format,
                remove_min_items=self.remove_min_items_from_schema,
                remove_defaults=self.remove_defaults_from_schema,
            ),
        }

        if self.add_schema_to_system_prompt and openai_messages and openai_messages[0]["role"] == "system":
            schema_text = f"\n<json_schema>\n{response_schema}\n</json_schema>"
            if isinstance(openai_messages[0]["content"], str):
                openai_messages[0]["content"] += schema_text
            else:
                openai_messages[0]["content"] = list(openai_messages[0]["content"]) + [
                    ChatCompletionContentPartTextParam(text=schema_text, type="text")
                ]

        return openai_messages

    def _prepare_model_params(self) -> dict[str, Any]:
        model_params: dict[str, Any] = {}

        if self.temperature is not None:
            model_params["temperature"] = self.temperature
        if self.frequency_penalty is not None:
            model_params["frequency_penalty"] = self.frequency_penalty
        if self.max_completion_tokens is not None:
            model_params["max_completion_tokens"] = self.max_completion_tokens
        if self.top_p is not None:
            model_params["top_p"] = self.top_p
        if self.seed is not None:
            model_params["seed"] = self.seed
        if self.service_tier is not None:
            model_params["service_tier"] = self.service_tier

        if self.reasoning_models and any(str(m).lower() in str(self.model).lower() for m in self.reasoning_models):
            model_params["reasoning_effort"] = self.reasoning_effort
            model_params.pop("temperature", None)
            model_params.pop("frequency_penalty", None)

        return model_params


DeepSeekChatOpenAILike = OpenAICompatibleChat
