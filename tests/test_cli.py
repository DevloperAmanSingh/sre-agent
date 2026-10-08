from importlib.metadata import version

import pytest
from typer.testing import CliRunner

from opensre.cli.main import app


@pytest.mark.parametrize(
    "source,field", [("yaml", "kube.request_timeout_s"), ("env", "kube"), ("env", "llm")]
)
def test_invalid_config(source, field, tmp_path, monkeypatch):
    flags = []
    if source == "yaml":
        path = tmp_path / "bad.yaml"
        path.write_text("kube: {request_timeout_s: nope}")
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
