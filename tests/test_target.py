import pytest

from opensre.config import KubeSettings
from opensre.connectors.kubernetes.connector import KubernetesConnector


def test_explicit_target():
    assert KubernetesConnector(KubeSettings(context="prod")).target == "kubernetes/prod"


def test_current_context_target(tmp_path, monkeypatch):
    config = tmp_path / "kubeconfig"
    config.write_text(
        "current-context: staging\ncontexts:\n- name: staging\n  context: {cluster: staging}\n"
        "clusters:\n- name: staging\n  cluster: {server: 'https://example.invalid'}\n"
    )
    monkeypatch.setenv("KUBECONFIG", str(config))
    assert KubernetesConnector(KubeSettings()).target == "kubernetes/staging"


def test_unknown_target_is_not_shared():
    with pytest.raises(ValueError, match="identity"):
        _ = KubernetesConnector(KubeSettings()).target
