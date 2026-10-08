import pytest
from pydantic import ValidationError

from opensre.config import ConfigError, Settings, load_settings


@pytest.mark.parametrize("source", ["default", "file", "env", "flag"])
def test_precedence(source, tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    values = {"default": "default", "file": "file", "env": "env", "flag": "flag"}
    if source != "default":
        path.write_text("connectors:\n  kubernetes:\n    namespace: file\n    context: retained\n")
    if source in ("env", "flag"):
        monkeypatch.setenv("OPENSRE_CONNECTORS__KUBERNETES__NAMESPACE", "env")
    flags = {"connectors": {"kubernetes": {"namespace": "flag"}}} if source == "flag" else {}
    settings = load_settings(path, flags)
    assert settings.connectors.kubernetes.namespace == values[source]
    assert settings.connectors.kubernetes.context == (None if source == "default" else "retained")


@pytest.mark.parametrize("location", ["flag", "env", "local", "home"])
def test_config_path(location, tmp_path, monkeypatch):
    home_path = tmp_path / ".config" / "opensre" / "config.yaml"
    home_path.parent.mkdir(parents=True)
    home_path.write_text("connectors: {kubernetes: {namespace: home}}")
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text("connectors: {kubernetes: {namespace: explicit}}")
    if location != "home":
        (tmp_path / "opensre.yaml").write_text("connectors: {kubernetes: {namespace: local}}")
    if location in ("flag", "env"):
        monkeypatch.setenv("OPENSRE_CONFIG", str(explicit))
    flag = explicit if location == "flag" else None
    if location == "flag":
        monkeypatch.setenv("OPENSRE_CONFIG", str(tmp_path / "missing"))
    assert (
        load_settings(flag).connectors.kubernetes.namespace
        == {"flag": "explicit", "env": "explicit", "local": "local", "home": "home"}[location]
    )


@pytest.mark.parametrize(
    "contents",
    [
        "connectors: {kubernetes: {request_timeout_s: invalid}}",
        "connectors: [",
        "- item",
        "llm: {api_key: secret}",
        "kube: {}",
    ],
)
def test_invalid_file(contents, tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(contents)
    with pytest.raises(ConfigError):
        load_settings(path)


def test_defaults():
    settings = Settings()
    assert settings.connectors.kubernetes.enabled is True
    assert settings.connectors.kubernetes.context is None
    assert settings.connectors.kubernetes.namespace == "default"
    assert settings.connectors.kubernetes.request_timeout_s == 10
    assert settings.llm.primary == "deepseek/deepseek-chat"
    assert settings.llm.fallback == "openai/gpt-5.6-luna"
    assert settings.llm.timeout_s == 60


@pytest.mark.parametrize("section", ["kube", "llm"])
def test_invalid_timeout(section):
    with pytest.raises(ValidationError, match="timeout_s"):
        Settings(
            **(
                {"connectors": {"kubernetes": {"request_timeout_s": 0}}}
                if section == "kube"
                else {"llm": {"timeout_s": 0}}
            )
        )
