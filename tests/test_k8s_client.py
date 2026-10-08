import pytest
from kubernetes.client import Configuration
from kubernetes.config import ConfigException

from opensre.config import KubeSettings
from opensre.connectors.k8s.client import create_client


@pytest.mark.parametrize("in_cluster", [False, True])
def test_isolated_client(in_cluster):
    original = Configuration.get_default_copy().host

    def kube_loader(*, context, client_configuration):
        if in_cluster:
            raise ConfigException("no kubeconfig")
        assert context == "demo"
        client_configuration.host = "https://kube.example"

    def cluster_loader(*, client_configuration):
        client_configuration.host = "https://incluster.example"

    settings = KubeSettings(context=None if in_cluster else "demo")
    with create_client(settings, kube_loader=kube_loader, cluster_loader=cluster_loader) as client:
        assert client.configuration.retries == 0
        assert client.configuration.host == (
            "https://incluster.example" if in_cluster else "https://kube.example"
        )
    assert Configuration.get_default_copy().host == original


def test_explicit_context_does_not_fall_back():
    def missing_context(**kwargs):
        raise ConfigException("unknown context")

    with pytest.raises(ConfigException, match="unknown context"):
        create_client(KubeSettings(context="missing"), kube_loader=missing_context)
