import pytest
from pydantic import ValidationError

from opensre.config import Settings


def test_defaults():
    settings = Settings()
    assert settings.kube.context is None
    assert settings.kube.namespace == "default"
    assert settings.kube.request_timeout_s == 10
    assert settings.llm.primary == "openai/gpt-5.6-luna"
    assert settings.llm.fallback == "deepseek/deepseek-chat"
    assert settings.llm.timeout_s == 60


@pytest.mark.parametrize("section", ["kube", "llm"])
def test_invalid_timeout(section):
    with pytest.raises(ValidationError, match="timeout_s"):
        Settings(**{section: {"request_timeout_s" if section == "kube" else "timeout_s": 0}})
