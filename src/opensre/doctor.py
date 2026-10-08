from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from kubernetes import client  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel

from opensre.config import KubeSettings
from opensre.connectors.k8s.client import create_client


class CheckResult(BaseModel):
    name: str
    ok: bool
    detail: str
    latency_s: float | None = None


def check_kube(
    settings: KubeSettings,
    *,
    client_factory: Callable[[KubeSettings], AbstractContextManager[Any]] = create_client,
    version_factory: Callable[[Any], Any] = client.VersionApi,
) -> CheckResult:
    try:
        with client_factory(settings) as api_client:
            version = version_factory(api_client).get_code(
                _request_timeout=settings.request_timeout_s
            )
        if not isinstance(version.git_version, str):
            raise ValueError("Version endpoint returned no server version")
        return CheckResult(name="kubernetes", ok=True, detail=version.git_version)
    except Exception as exc:
        return CheckResult(name="kubernetes", ok=False, detail=str(exc))
