import pytest
from test_memory_store import NOW
from typer.testing import CliRunner

from opensre.cli.main import app
from opensre.domain import Diagnosis
from opensre.memory.store import IncidentStore


@pytest.fixture
def seeded_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSRE_MEMORY__DIR", str(tmp_path))
    monkeypatch.setattr("opensre.memory.store.utc_now", lambda: NOW)
    store = IncidentStore(tmp_path, now=lambda: NOW)
    store.save(
        target="fake/prod",
        question="Why?",
        signature=[],
        diagnosis=Diagnosis(
            summary="Failure",
            cause="OOM",
            suggested_fix="Check limits",
            evidence=[],
            confidence=0.8,
        ),
    )
    return store


@pytest.mark.parametrize(
    "flags,exit_code", [([], 2), (["--right", "--wrong"], 2), (["--right"], 0), (["--wrong"], 0)]
)
def test_feedback_flags_and_note(seeded_store, flags, exit_code):
    result = CliRunner().invoke(app, ["feedback", "1", *flags, "--note", "token=secret"])
    assert result.exit_code == exit_code, result.output
    incident = seeded_store.get(1)
    if exit_code == 0:
        assert incident.status == flags[0][2:]
        assert "secret" not in incident.note
        assert incident.feedback_at == NOW
    else:
        assert incident.status == "unconfirmed"
    result = CliRunner().invoke(app, ["feedback", "999", "--right"])
    assert result.exit_code == 1
    assert "Unknown incident #999" in result.stderr


def test_remember_command_uses_configured_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSRE_MEMORY__DIR", str(tmp_path / "state"))
    result = CliRunner().invoke(app, ["remember", "payments runs in ns shop"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "state/memory/environment.md").read_text() == "- payments runs in ns shop\n"
    assert "Remembered" in result.stdout
    result = CliRunner().invoke(app, ["remember", "one\ntwo"])
    assert result.exit_code == 1
    assert "single line" in result.stderr
