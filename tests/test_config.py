import pytest
from pydantic import ValidationError

from opensre.config import ConfigError, Settings, load_settings


@pytest.mark.parametrize("source", ["default", "file", "env", "flag"])
def test_precedence(source, tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    values = {"default": "default", "file": "file", "env": "env", "flag": "flag"}
    if source != "default":
        path.write_text("kube:\n  namespace: file\n  context: retained\n")
    if source in ("env", "flag"):
        monkeypatch.setenv("OPENSRE_KUBE__NAMESPACE", "env")
    flags = {"kube": {"namespace": "flag"}} if source == "flag" else {}
    settings = load_settings(path, flags)
    assert settings.kube.namespace == values[source]
    assert settings.kube.context == (None if source == "default" else "retained")


@pytest.mark.parametrize("location", ["flag", "env", "local", "home"])
def test_config_path(location, tmp_path, monkeypatch):
    home_path = tmp_path / ".config" / "opensre" / "config.yaml"
    home_path.parent.mkdir(parents=True)
    home_path.write_text("kube: {namespace: home}")
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text("kube: {namespace: explicit}")
    if location != "home":
        (tmp_path / "opensre.yaml").write_text("kube: {namespace: local}")
    if location in ("flag", "env"):
        monkeypatch.setenv("OPENSRE_CONFIG", str(explicit))
    flag = explicit if location == "flag" else None
    if location == "flag":
        monkeypatch.setenv("OPENSRE_CONFIG", str(tmp_path / "missing"))
    assert (
        load_settings(flag).kube.namespace
        == {"flag": "explicit", "env": "explicit", "local": "local", "home": "home"}[location]
    )


@pytest.mark.parametrize(
    "contents",
    ["kube: {request_timeout_s: invalid}", "kube: [", "- item", "llm: {api_key: secret}"],
)
def test_invalid_file(contents, tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(contents)
    with pytest.raises(ConfigError):
        load_settings(path)


def test_defaults():
    settings = Settings()
    assert settings.kube.context is None
    assert settings.kube.namespace == "default"
    assert settings.kube.request_timeout_s == 10
    assert settings.llm.primary == "deepseek/deepseek-chat"
    assert settings.llm.fallback == "openai/gpt-5.6-luna"
    assert settings.llm.timeout_s == 60


@pytest.mark.parametrize("section", ["kube", "llm"])
def test_invalid_timeout(section):
    with pytest.raises(ValidationError, match="timeout_s"):
        Settings(**{section: {"request_timeout_s" if section == "kube" else "timeout_s": 0}})
