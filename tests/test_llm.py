import pytest

from opensre.config import LLMSettings
from opensre.llm import LLMError, build_model


@pytest.mark.parametrize("key", ["DEEPSEEK_API_KEY", "OPENAI_API_KEY"])
def test_missing_key(key, monkeypatch):
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        if name != key:
            monkeypatch.setenv(name, "fake-key")
    with pytest.raises(LLMError, match=key):
        build_model(LLMSettings())


@pytest.mark.parametrize("field", ["primary", "fallback"])
def test_unknown_provider(field, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    with pytest.raises(LLMError, match=f"llm.{field}.*nonesuch/model"):
        build_model(LLMSettings(**{field: "nonesuch/model"}))


@pytest.mark.parametrize("fallback", [None, "openai/gpt-5.6-luna"])
def test_model_configuration(fallback, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    model = build_model(LLMSettings(fallback=fallback, timeout_s=7))
    assert model.model == "deepseek/deepseek-chat"
    assert model.request_timeout == 7
    assert model.model_kwargs == ({"fallbacks": [fallback]} if fallback else {})
