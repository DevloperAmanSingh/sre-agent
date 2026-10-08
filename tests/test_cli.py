from importlib.metadata import version

import pytest
from typer.testing import CliRunner

from opensre.cli.main import app


@pytest.mark.parametrize(
    "source,field",
    [("yaml", "connectors.kubernetes.request_timeout_s"), ("env", "connectors"), ("env", "llm")],
)
def test_invalid_config(source, field, tmp_path, monkeypatch):
    flags = []
    if source == "yaml":
        path = tmp_path / "bad.yaml"
        path.write_text("connectors: {kubernetes: {request_timeout_s: nope}}")
        flags = ["--config", str(path)]
    else:
        monkeypatch.setenv(f"OPENSRE_{field.upper()}", "not-json")
    result = CliRunner().invoke(app, [*flags, "doctor", "--json"])
    assert result.exit_code == 2
    assert field in result.stderr
    assert "Invalid config" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
    if source == "env":
        assert "EnvSettingsSource" in result.stderr


def test_version():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == version("opensre")


@pytest.mark.parametrize("enabled", [True, False])
def test_tools_lists_effective_sources_without_credentials(enabled, monkeypatch):
    monkeypatch.setenv("OPENSRE_CONNECTORS__KUBERNETES__ENABLED", str(enabled).lower())
    result = CliRunner().invoke(app, ["tools"])
    assert result.exit_code == 0, result.output
    assert "read_file" in result.stdout
    assert "builtin" in result.stdout
    assert ("k8s_list_namespaces" in result.stdout) is enabled
    assert "execute" not in result.stdout
    assert "write_file" not in result.stdout
    assert "task" not in result.stdout
