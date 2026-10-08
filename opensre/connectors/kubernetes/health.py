from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from kubernetes import client  # pyright: ignore[reportMissingTypeStubs]

from opensre.config import KubeSettings
from opensre.connectors.kubernetes.client import create_client
from opensre.connectors.kubernetes.execution import KubeDiagnostics, run_bounded
from opensre.domain import CheckResult


def check_kube(
    settings: KubeSettings,
    *,
    client_factory: Callable[[KubeSettings], AbstractContextManager[Any]] = create_client,
    version_factory: Callable[[Any], Any] = client.VersionApi,
) -> CheckResult:
    def read_version(diagnostics: KubeDiagnostics) -> str:
        with client_factory(settings) as api_client:
            diagnostics.check_credentials()
            version = version_factory(api_client).get_code(
                _request_timeout=settings.request_timeout_s
            )
        if not isinstance(version.git_version, str):
            raise ValueError("Version endpoint returned no server version")
        return version.git_version

    try:
        version = run_bounded(read_version, settings.request_timeout_s)
        return CheckResult(name="kubernetes", ok=True, detail=version)
    except Exception as exc:
        return CheckResult(name="kubernetes", ok=False, detail=str(exc))
