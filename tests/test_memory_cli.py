from typer.testing import CliRunner

from opensre.cli.main import app


def test_remember_command_uses_configured_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSRE_MEMORY__DIR", str(tmp_path / "state"))
    result = CliRunner().invoke(app, ["remember", "payments runs in ns shop"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "state/memory/environment.md").read_text() == "- payments runs in ns shop\n"
    assert "Remembered" in result.stdout
    result = CliRunner().invoke(app, ["remember", "one\ntwo"])
    assert result.exit_code == 1
    assert "single line" in result.stderr
