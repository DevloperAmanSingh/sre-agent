import json
from types import SimpleNamespace as NS

import pytest
from typer.testing import CliRunner

from opensre.cli.main import app
from opensre.domain import Evidence, Finding, QuickCheck


def result(severity):
    return Finding(
        resource=f"pod/{severity}",
        reason="Fault",
        summary=f"{severity} fault",
        severity=severity,
        evidence=[Evidence(source="fake", detail="observed")],
    )


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize(
    "findings,exit_code",
    [
        ([], 0),
        ([result("info"), result("warning"), result("critical")], 1),
        ([result("warning")] * 50 + [result("critical")], 1),
    ],
)
def test_scan_runs_enabled_connector_checks_and_sorts(
    findings, exit_code, json_output, monkeypatch
):
    from opensre.cli import main

    target = NS(
        name="fake",
        checks=lambda: [
            QuickCheck(name=f"fault-{index}", run=lambda item=item: [item])
            for index, item in enumerate(findings)
        ],
    )
    empty = NS(name="empty", checks=lambda: [])
    monkeypatch.setattr(main, "build_connectors", lambda settings: [empty, target])
    response = CliRunner().invoke(app, ["scan", *(["--json"] if json_output else [])])
    assert response.exit_code == exit_code, response.output
    if json_output:
        report = json.loads(response.stdout)
        assert report["errors"] == []
        expected = sorted(
            [item.severity.value for item in findings], key=["critical", "warning", "info"].index
        )[:50]
        assert [item["severity"] for item in report["findings"]] == expected
        assert report["omitted"] == max(0, len(findings) - 50)
    elif findings:
        assert response.stdout.index("critical") < response.stdout.index("warning")
        if len(findings) <= 50:
            assert response.stdout.index("warning") < response.stdout.index("info")
        else:
            assert "1 more omitted" in response.stdout
    else:
        assert "No findings" in response.stdout


def test_scan_access_failure_is_not_a_clean_scan(monkeypatch):
    from opensre.cli import main

    def fail():
        raise RuntimeError("target unavailable")

    target = NS(name="fake", checks=lambda: [QuickCheck(name="fault", run=fail)])
    monkeypatch.setattr(main, "build_connectors", lambda settings: [target])
    response = CliRunner().invoke(app, ["scan", "--json"])
    assert response.exit_code == 1
    report = json.loads(response.stdout)
    assert report["findings"] == []
    assert report["errors"][0]["detail"] == "target unavailable"
    assert report["ok"] is False


def test_scan_config_error_exits_two():
    response = CliRunner().invoke(app, ["--request-timeout", "0", "scan"])
    assert response.exit_code == 2
    assert "Invalid config" in response.stderr


@pytest.mark.parametrize("option", ["-n", "--namespace"])
def test_scan_namespace_override_is_applied_in_registry(option, monkeypatch):
    from opensre.cli import main
    from opensre.connectors.registry import build_connectors

    def build(settings, **kwargs):
        targets = build_connectors(settings, **kwargs)
        assert targets[0].settings.namespace == "other"
        return [NS(name="fake", checks=lambda: [])]

    monkeypatch.setattr(main, "build_connectors", build)
    response = CliRunner().invoke(app, ["scan", option, "other", "--json"])
    assert response.exit_code == 0, response.output
    assert json.loads(response.stdout)["ok"] is True
