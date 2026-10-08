import pytest
from pydantic import ValidationError

from opensre.config import Settings


def test_memory_settings_expand_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSRE_MEMORY__DIR", "~/history")
    monkeypatch.setenv("OPENSRE_MEMORY__ENABLED", "false")
    settings = Settings()
    assert settings.memory.dir == tmp_path / "history"
    assert not settings.memory.enabled
    with pytest.raises(ValidationError):
        Settings(memory={"unknown": True})
