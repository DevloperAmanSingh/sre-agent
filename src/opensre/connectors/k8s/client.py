from collections.abc import Callable
from typing import cast

from kubernetes import client, config  # pyright: ignore[reportMissingTypeStubs]
from kubernetes.config.config_exception import (
    ConfigException,  # pyright: ignore[reportMissingTypeStubs]
)

from opensre.config import KubeSettings


def create_client(
    settings: KubeSettings,
    *,
    kube_loader: Callable[..., None] | None = None,
    cluster_loader: Callable[..., None] | None = None,
) -> client.ApiClient:
    configuration = client.Configuration()
    load_kube = kube_loader or cast(Callable[..., None], getattr(config, "load_kube_config"))
    load_cluster = cluster_loader or cast(
        Callable[..., None], getattr(config, "load_incluster_config")
    )
    try:
        load_kube(context=settings.context, client_configuration=configuration)
    except ConfigException:
        if settings.context is not None:
            raise
        load_cluster(client_configuration=configuration)
    configuration.retries = 0
    return client.ApiClient(configuration=configuration)
