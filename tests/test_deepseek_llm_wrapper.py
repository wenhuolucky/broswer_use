from types import SimpleNamespace

from pydantic import BaseModel

from publish_service.deepseek_llm import extract_json_payload, parse_structured_completion


class SampleOutput(BaseModel):
    success: bool
    account_name: str


def test_extract_json_payload_strips_fenced_json_with_leading_text():
    raw = """Here is the result:

```json
{"success": true, "account_name": "demo"}
```
"""

    cleaned = extract_json_payload(raw)

    assert cleaned == '{"success": true, "account_name": "demo"}'


def test_extract_json_payload_handles_plain_json_without_changes():
    raw = '{"success": false, "account_name": ""}'

    cleaned = extract_json_payload(raw)

    assert cleaned == raw


def test_parse_structured_completion_accepts_fenced_json_payload():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='''```json\n{"success": true, "account_name": "demo"}\n```'''
                ),
                finish_reason="stop",
            )
        ],
        usage=None,
    )

    parsed = parse_structured_completion(response, SampleOutput)

    assert parsed.completion.success is True
    assert parsed.completion.account_name == "demo"


def test_parse_structured_completion_extracts_json_when_wrapped_in_extra_text():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='''analysis text\n```json\n{"success": false, "account_name": ""}\n```\nmore text'''
                ),
                finish_reason="stop",
            )
        ],
        usage=None,
    )

    parsed = parse_structured_completion(response, SampleOutput)

    assert parsed.completion.success is False
    assert parsed.completion.account_name == ""
