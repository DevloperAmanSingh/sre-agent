import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

from kubernetes import client, config  # pyright: ignore[reportMissingTypeStubs]

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
    home_config = Path.home() / ".kube/config"
    config_file = os.environ.get("KUBECONFIG") or str(home_config)
    paths = [Path(path).expanduser() for path in config_file.split(os.pathsep) if path]
    if settings.context is not None or home_config.exists() or any(path.exists() for path in paths):
        load_kube(
            config_file=config_file, context=settings.context, client_configuration=configuration
        )
    else:
        load_cluster(client_configuration=configuration)
    configuration.retries = 0
    return client.ApiClient(configuration=configuration)
