from __future__ import annotations


def _api_key_value(llm):
    api_key = llm.api_key
    if hasattr(api_key, "get_secret_value"):
        return api_key.get_secret_value()
    return api_key


def test_browser_llm_defaults_to_qwen_with_generic_key(monkeypatch):
    from app.publishing.service import PublishService

    monkeypatch.setenv("LLM_API_KEY", "qwen-key")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    llm = PublishService()._get_browser_llm()

    assert llm.model == "Qwen/Qwen3.5-397B-A17B"
    assert str(llm.base_url).rstrip("/") == "http://47.242.205.13:8110/v1"
    assert _api_key_value(llm) == "qwen-key"


def test_browser_llm_uses_explicit_openai_compatible_config(monkeypatch):
    from app.publishing.service import PublishService

    monkeypatch.setenv("LLM_API_KEY", "custom-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://example.test/custom/v1/")
    monkeypatch.setenv("LLM_MODEL", "custom-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-key")

    llm = PublishService()._get_browser_llm()

    assert llm.model == "custom-model"
    assert str(llm.base_url).rstrip("/") == "http://example.test/custom/v1"
    assert _api_key_value(llm) == "custom-key"


def test_browser_llm_keeps_deepseek_fallback(monkeypatch):
    from app.publishing.service import PublishService

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-key")

    llm = PublishService()._get_browser_llm()

    assert llm.model == "deepseek-chat"
    assert str(llm.base_url).rstrip("/") == "https://api.deepseek.com/v1"
    assert _api_key_value(llm) == "legacy-key"
