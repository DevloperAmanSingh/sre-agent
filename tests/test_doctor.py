from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from opensre.cli.main import app
from opensre.config import KubeSettings
from opensre.doctor import check_kube


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


@pytest.mark.parametrize("ok", [True, False])
def test_doctor_kube_exit(ok, monkeypatch):
    from opensre import doctor
    from opensre.cli import main

    result = doctor.CheckResult(name="kubernetes", ok=ok, detail="v1.35.0" if ok else "unreachable")
    monkeypatch.setattr(main, "check_kube", lambda settings: result)
    response = CliRunner().invoke(app, ["doctor"])
    assert response.exit_code == (0 if ok else 1)
    assert result.detail in response.stdout
