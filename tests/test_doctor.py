import json
import logging
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from opensre.cli.main import app
from opensre.config import KubeSettings, LLMSettings
from opensre.connectors.kubernetes.health import check_kube
from opensre.doctor import check_llm


@pytest.mark.parametrize("failure", [False, True])
def test_kube_result(failure):
    def version_factory(client):
        def get_code(*, _request_timeout):
            assert _request_timeout == 3
            if failure:
                raise RuntimeError("cluster unreachable")
            return SimpleNamespace(git_version="v1.35.0")

        return SimpleNamespace(get_code=get_code)

    result = check_kube(
        KubeSettings(request_timeout_s=3),
        client_factory=lambda settings: nullcontext(object()),
        version_factory=version_factory,
    )
    assert result.ok is not failure
    assert result.detail == ("cluster unreachable" if failure else "v1.35.0")


@pytest.mark.parametrize("stage", ["credentials", "version"])
def test_kube_deadline(stage, deadline):
    def client_factory(settings):
        if stage == "credentials":
            deadline()
        return nullcontext(object())

    def version_factory(client):
        def get_code(**kwargs):
            if stage == "version":
                deadline()
            return SimpleNamespace(git_version="v1.35.0")

        return SimpleNamespace(get_code=get_code)

    result = check_kube(
        KubeSettings(request_timeout_s=3),
        client_factory=client_factory,
        version_factory=version_factory,
    )
    assert not result.ok
    assert result.detail == "timed out after 3s"


@pytest.mark.parametrize("output", ["table", "json"])
@pytest.mark.parametrize("failure", [None, "kube", "key", "primary", "fallback"])
def test_doctor_exit_and_output(output, failure, monkeypatch):
    from opensre import doctor
    from opensre.cli import main

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    if failure != "key":
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    result = doctor.CheckResult(
        name="kubernetes",
        ok=failure != "kube",
        detail="unreachable" if failure == "kube" else "v1.35.0",
    )
    monkeypatch.setattr(
        main, "build_connectors", lambda settings: [SimpleNamespace(health=lambda: result)]
    )
    flags = [f"--{failure}", "nonesuch/model"] if failure in ("primary", "fallback") else []
    response = CliRunner().invoke(
        app, [*flags, "doctor", *(["--json"] if output == "json" else [])]
    )
    assert response.exit_code == (0 if failure is None else 1)
    if output == "json":
        data = json.loads(response.stdout)
        assert data["ok"] is (failure is None)
        assert len(data["checks"]) == 3
        assert data["checks"][0] == result.model_dump()
        assert all(set(check) == {"name", "ok", "detail", "latency_s"} for check in data["checks"])
    else:
        assert result.detail in response.stdout
    if failure == "key":
        assert "OPENAI_API_KEY" in response.stdout
    if failure in ("primary", "fallback"):
        assert f"llm.{failure}" in response.stdout
        assert "nonesuch/model" in response.stdout
        assert "Provider List" not in response.stdout


@pytest.mark.parametrize("output", ["table", "json"])
@pytest.mark.parametrize("version_fails", [False, True])
def test_kube_diagnostics_are_owned(output, version_fails, monkeypatch, caplog, capsys):
    from opensre.cli import main

    loggers = [logging.getLogger(name) for name in ("", "kubernetes", "urllib3.connectionpool")]
    before = [
        (logger.level, logger.propagate, list(logger.handlers), list(logger.filters))
        for logger in loggers
    ]

    def noisy_factory(settings):
        logging.log(
            logging.WARNING if version_fails else logging.ERROR,
            "exec: simulated credential error %s",
            "x" * 3000,
        )
        logging.getLogger("kubernetes").warning("kube warning")
        logging.getLogger("urllib3.connectionpool").warning("transport warning")
        return nullcontext(object())

    def version_factory(client):
        def get_code(**kwargs):
            if version_fails:
                raise RuntimeError("connection failed")
            return SimpleNamespace(git_version="v1.35.0")

        return SimpleNamespace(get_code=get_code)

    monkeypatch.setattr(
        main,
        "build_connectors",
        lambda settings: [
            SimpleNamespace(
                health=lambda: check_kube(
                    settings.connectors.kubernetes,
                    client_factory=noisy_factory,
                    version_factory=version_factory,
                )
            )
        ],
    )
    response = CliRunner().invoke(app, ["doctor", *(["--json"] if output == "json" else [])])
    assert response.exit_code == 1
    assert "simulated credential" in response.stdout
    assert "characters cut" in response.stdout
    assert response.stderr == ""
    if output == "json":
        result = json.loads(response.stdout)["checks"][0]
        assert not result["ok"]
        assert "simulated credential error" in result["detail"]
        assert ("connection failed" if version_fails else "credential loading failed") in result[
            "detail"
        ]
        assert len(result["detail"]) < 2100
    assert caplog.text == ""
    assert capsys.readouterr().err == ""
    assert before == [
        (logger.level, logger.propagate, list(logger.handlers), list(logger.filters))
        for logger in loggers
    ]
    logging.warning("unrelated logging remains enabled")
    assert "unrelated logging remains enabled" in caplog.text


@pytest.mark.parametrize("failure", [False, True])
def test_live_results(failure, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    times = iter([1.0, 1.25, 2.0, 2.5])
    requests = []

    def completion(**kwargs):
        requests.append(kwargs)
        if failure and kwargs["model"].startswith("deepseek"):
            raise RuntimeError("provider unavailable")
        return object()

    results = check_llm(
        LLMSettings(timeout_s=4), live=True, completion=completion, clock=lambda: next(times)
    )
    assert [result.ok for result in results] == [not failure, True]
    assert [result.latency_s for result in results] == [0.25, 0.5]
    if failure:
        assert results[0].detail == "provider unavailable"
    assert [request["model"] for request in requests] == [
        "deepseek/deepseek-chat",
        "openai/gpt-5.6-luna",
    ]
    assert all(request["timeout"] == 4 and request["max_tokens"] == 8 for request in requests)


def test_live_skips_missing_key():
    def forbidden(**kwargs):
        raise AssertionError("No request allowed without keys")

    results = check_llm(LLMSettings(fallback=None), live=True, completion=forbidden)
    assert not results[0].ok
    assert "DEEPSEEK_API_KEY" in results[0].detail
    assert results[0].latency_s is None
