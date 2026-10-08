import os

import pytest

pytest_plugins = ["fakes.deadline"]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    monkeypatch.setattr("opensre.connectors.kubernetes.execution.monotonic", lambda: 0.0)
    for name in os.environ:
        if name.startswith("OPENSRE_") or name in (
            "OPENAI_API_KEY",
            "DEEPSEEK_API_KEY",
            "KUBECONFIG",
        ):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
