from importlib.metadata import version

from typer.testing import CliRunner

from opensre.cli.main import app


def test_invalid_config(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("kube: {request_timeout_s: nope}")
    result = CliRunner().invoke(app, ["--config", str(path)])
    assert result.exit_code == 2
    assert "kube.request_timeout_s" in result.stderr


def test_version():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == version("opensre")
