import hashlib
import os

import pytest

from opensre.config import KubeSettings
from opensre.connectors.kubernetes.connector import KubernetesConnector


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("split", [False, True])
def test_target_tracks_server_not_kubeconfig_location(tmp_path, monkeypatch, explicit, split):
    current = "other" if explicit else "prod"
    contexts = (
        f"current-context: {current}\ncontexts:\n- name: prod\n  context: {{cluster: selected}}\n"
        "- name: other\n  context: {cluster: other}\n"
    )

    def configure(directory, server):
        directory.mkdir()
        path = directory / "config"
        clusters = (
            f"clusters:\n- name: selected\n  cluster: {{server: '{server}'}}\n"
            "- name: other\n  cluster: {server: 'https://other.invalid'}\n"
        )
        path.write_text(contexts if split else contexts + clusters)
        if split:
            other = directory / "clusters"
            other.write_text(clusters)
            return str(path) + os.pathsep + str(other)
        return str(path)

    settings = KubeSettings(context="prod" if explicit else None)
    server = "https://cluster-a.invalid"
    first = configure(tmp_path / "first", server)
    monkeypatch.setenv("KUBECONFIG", first)
    connector = KubernetesConnector(settings)
    target = connector.target
    assert target == "kubernetes/prod#" + hashlib.sha256(server.encode()).hexdigest()[:12]
    monkeypatch.setenv("KUBECONFIG", configure(tmp_path / "moved", server))
    assert KubernetesConnector(settings).target == target
    monkeypatch.setenv("KUBECONFIG", configure(tmp_path / "replaced", "https://cluster-b.invalid"))
    assert KubernetesConnector(settings).target != target
    (tmp_path / "first/config").write_text("invalid: replaced after resolution\n")
    with connector.client_factory(settings) as client:
        assert client.configuration.host == server
    assert connector.target == target


def test_incluster_target(monkeypatch):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    expected = hashlib.sha256(b"https://10.0.0.1:443").hexdigest()[:12]
    assert KubernetesConnector(KubeSettings()).target == f"kubernetes/in-cluster#{expected}"


def test_unknown_target_is_not_shared(monkeypatch):
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    with pytest.raises(ValueError, match="identity"):
        _ = KubernetesConnector(KubeSettings()).target
