from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Annotated, Any

from kubernetes import client  # pyright: ignore[reportMissingTypeStubs]
from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import BaseModel, Field

from opensre.config import KubeSettings
from opensre.connectors.kubernetes.client import create_client
from opensre.connectors.kubernetes.execution import KubeDiagnostics, run_bounded
from opensre.connectors.kubernetes.health import check_kube
from opensre.connectors.kubernetes.reader import KubeReader
from opensre.connectors.kubernetes.tools import read_tools
from opensre.domain import CheckResult, QuickCheck


class NamespaceList(BaseModel):
    namespaces: list[str]
    cut: int | None
    more_available: bool


class KubernetesConnector:
    name = "kubernetes"

    def __init__(
        self,
        settings: KubeSettings,
        *,
        client_factory: Callable[[KubeSettings], AbstractContextManager[Any]] = create_client,
        core_factory: Callable[[Any], Any] = client.CoreV1Api,
        version_factory: Callable[[Any], Any] = client.VersionApi,
    ) -> None:
        self.settings = settings
        self.client_factory = client_factory
        self.core_factory = core_factory
        self.version_factory = version_factory

    def health(self) -> CheckResult:
        return check_kube(
            self.settings, client_factory=self.client_factory, version_factory=self.version_factory
        )

    def tools(self) -> list[BaseTool]:
        @tool(response_format="content_and_artifact")
        def k8s_list_namespaces(
            limit: Annotated[int, Field(ge=1, le=100)] = 100,
        ) -> tuple[str, NamespaceList]:
            """Read namespace names; cut is null when the remaining count is unknown."""

            def read_namespaces(diagnostics: KubeDiagnostics) -> NamespaceList:
                with self.client_factory(self.settings) as api_client:
                    diagnostics.check_credentials()
                    page = self.core_factory(api_client).list_namespace(
                        limit=limit, _request_timeout=self.settings.request_timeout_s
                    )
                names = [str(item.metadata.name) for item in page.items[:limit]]
                more = bool(page.metadata._continue)
                remaining = page.metadata.remaining_item_count
                dropped = max(0, len(page.items) - limit)
                cut = None if more and remaining is None else (remaining or 0) + dropped
                return NamespaceList(namespaces=names, cut=cut, more_available=more or dropped > 0)

            try:
                result = run_bounded(read_namespaces, self.settings.request_timeout_s)
                return result.model_dump_json(), result
            except Exception as exc:
                raise ToolException(f"Namespace read failed: {exc}") from exc

        k8s_list_namespaces.metadata = {"read_only": True}
        return [
            k8s_list_namespaces,
            *read_tools(KubeReader(self.settings, self.client_factory), self.core_factory),
        ]

    def checks(self) -> list[QuickCheck]:
        return []
