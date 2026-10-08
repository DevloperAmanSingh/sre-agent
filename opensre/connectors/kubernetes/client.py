import hashlib
import os
from collections.abc import Callable
from copy import deepcopy
from functools import cached_property
from pathlib import Path
from typing import Any, cast

from kubernetes import client, config  # pyright: ignore[reportMissingTypeStubs]
from kubernetes.config import (  # pyright: ignore[reportMissingTypeStubs]
    incluster_config,
    kube_config,
)

from opensre.config import KubeSettings


def kubeconfig_path(settings: KubeSettings) -> str | None:
    home = Path.home() / ".kube/config"
    selected = os.environ.get("KUBECONFIG") or str(home)
    paths = [Path(path).expanduser() for path in selected.split(os.pathsep) if path]
    return (
        selected
        if settings.context is not None or home.exists() or any(path.exists() for path in paths)
        else None
    )


class ClientSource:
    def __init__(self, settings: KubeSettings) -> None:
        self.settings = settings

    @cached_property
    def selection(self) -> tuple[str, str, Any]:
        path = kubeconfig_path(self.settings)
        if path is not None:
            merger = cast(Callable[..., Any], kube_config.KubeConfigMerger)(path)
            loader = cast(Callable[..., Any], kube_config.KubeConfigLoader)(
                merger.config, active_context=self.settings.context
            )
            return (
                str(loader.current_context["name"]),
                str(loader._cluster["server"]),
                merger.config,
            )
        environment = dict(os.environ)
        host = environment.get("KUBERNETES_SERVICE_HOST")
        port = environment.get("KUBERNETES_SERVICE_PORT")
        if not host or not port:
            raise ValueError("Cannot resolve Kubernetes target identity")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return "in-cluster", f"https://{host}:{port}", environment

    @property
    def target(self) -> str:
        context, server, _ = self.selection
        fingerprint = hashlib.sha256(server.encode()).hexdigest()[:12]
        return f"kubernetes/{context}#{fingerprint}"

    def create(self, settings: KubeSettings) -> client.ApiClient:
        context, server, snapshot = self.selection
        configuration = client.Configuration()
        if isinstance(snapshot, dict):
            loader = cast(Callable[..., Any], incluster_config.InClusterConfigLoader)(
                token_filename=incluster_config.SERVICE_TOKEN_FILENAME,
                cert_filename=incluster_config.SERVICE_CERT_FILENAME,
                environ=snapshot,
            )
        else:
            loader = cast(Callable[..., Any], kube_config.KubeConfigLoader)(
                deepcopy(snapshot), active_context=context
            )
        loader.load_and_set(configuration)
        if configuration.host.rstrip("/") != server.rstrip("/"):
            raise ValueError("Kubernetes client does not match pinned target identity")
        configuration.retries = 0
        return client.ApiClient(configuration=configuration)


def create_client(
    settings: KubeSettings,
    *,
    kube_loader: Callable[..., None] | None = None,
    cluster_loader: Callable[..., None] | None = None,
) -> client.ApiClient:
    if kube_loader is None and cluster_loader is None:
        return ClientSource(settings).create(settings)
    configuration = client.Configuration()
    load_kube = kube_loader or cast(Callable[..., None], getattr(config, "load_kube_config"))
    load_cluster = cluster_loader or cast(
        Callable[..., None], getattr(config, "load_incluster_config")
    )
    config_file = kubeconfig_path(settings)
    if config_file is not None:
        load_kube(
            config_file=config_file, context=settings.context, client_configuration=configuration
        )
    else:
        load_cluster(client_configuration=configuration)
    configuration.retries = 0
    return client.ApiClient(configuration=configuration)
