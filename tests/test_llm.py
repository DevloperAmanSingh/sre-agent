import pytest

from opensre.config import LLMSettings
from opensre.llm import LLMError, build_model


@pytest.mark.parametrize("value", [None, "", "   "])
@pytest.mark.parametrize("key", ["DEEPSEEK_API_KEY", "OPENAI_API_KEY"])
def test_missing_key(key, value, monkeypatch):
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        if name != key:
            monkeypatch.setenv(name, "fake-key")
    if value is not None:
        monkeypatch.setenv(key, value)
    with pytest.raises(LLMError, match=key):
        build_model(LLMSettings())


@pytest.mark.parametrize("fallback", [None, "openai/gpt-5.6-luna"])
def test_model_configuration(fallback, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    model = build_model(LLMSettings(fallback=fallback, timeout_s=7))
    assert model.model == "deepseek/deepseek-chat"
    assert model.request_timeout == 7
    assert model.model_kwargs == ({"fallbacks": [fallback]} if fallback else {})
