import os

import pytest
from kubernetes.client import Configuration
from kubernetes.config import ConfigException

from opensre.config import KubeSettings
from opensre.connectors.kubernetes.client import create_client


@pytest.mark.parametrize("source", ["home", "env", "env-list"])
@pytest.mark.parametrize("state", ["missing", "valid", "invalid"])
def test_isolated_client(source, state, tmp_path, monkeypatch):
    original = Configuration.get_default_copy().host
    path = tmp_path / ".kube" / "config" if source == "home" else tmp_path / "selected.yaml"
    config_file = str(path)
    if source == "env-list":
        config_file = str(tmp_path / "missing.yaml") + os.pathsep + config_file
    if source != "home":
        monkeypatch.setenv("KUBECONFIG", config_file)
    if state != "missing":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder")

    def kube_loader(*, context, config_file=None, client_configuration):
        if config_file is not None:
            assert config_file == (str(path) if source != "env-list" else os.environ["KUBECONFIG"])
        assert context is None
        if state != "valid":
            raise ConfigException("selected kubeconfig is broken")
        client_configuration.host = "https://kube.example"

    def cluster_loader(*, client_configuration):
        client_configuration.host = "https://incluster.example"

    def connect():
        return create_client(KubeSettings(), kube_loader=kube_loader, cluster_loader=cluster_loader)

    if state == "invalid":
        with pytest.raises(ConfigException, match="selected kubeconfig is broken"):
            connect()
    else:
        with connect() as client:
            assert client.configuration.retries == 0
            assert client.configuration.host == (
                "https://incluster.example" if state == "missing" else "https://kube.example"
            )
    assert Configuration.get_default_copy().host == original


def test_explicit_context_does_not_fall_back():
    def missing_context(**kwargs):
        raise ConfigException("unknown context")

    with pytest.raises(ConfigException, match="unknown context"):
        create_client(KubeSettings(context="missing"), kube_loader=missing_context)
