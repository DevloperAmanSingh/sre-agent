from importlib.metadata import version

from typer.testing import CliRunner

from opensre.cli.main import app


def test_version():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == version("opensre")
