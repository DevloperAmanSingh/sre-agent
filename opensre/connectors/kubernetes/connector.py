from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Annotated, Any

from kubernetes import client  # pyright: ignore[reportMissingTypeStubs]
from langchain_core.tools import BaseTool, tool
from pydantic import Field, computed_field

from opensre.config import KubeSettings
from opensre.connectors.kubernetes.checks import quick_checks
from opensre.connectors.kubernetes.client import ClientSource
from opensre.connectors.kubernetes.execution import remaining_timeout
from opensre.connectors.kubernetes.health import check_kube
from opensre.connectors.kubernetes.reader import (
    KubeReader,
    Page,
    bounded_page,
)
from opensre.connectors.kubernetes.tools import read_tools
from opensre.domain import CheckResult, QuickCheck


class NamespaceList(Page[str]):
    @computed_field
    @property
    def namespaces(self) -> list[str]:
        return self.items


class KubernetesConnector:
    name = "kubernetes"

    def __init__(
        self,
        settings: KubeSettings,
        *,
        client_factory: Callable[[KubeSettings], AbstractContextManager[Any]] | None = None,
        core_factory: Callable[[Any], Any] = client.CoreV1Api,
        version_factory: Callable[[Any], Any] = client.VersionApi,
        apps_factory: Callable[[Any], Any] = client.AppsV1Api,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.source = ClientSource(settings)
        self.client_factory = client_factory or self.source.create
        self.core_factory = core_factory
        self.version_factory = version_factory
        self.apps_factory = apps_factory
        self.now = now

    @property
    def target(self) -> str:
        return self.source.target

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

            def read_namespaces(api_client: Any) -> NamespaceList:
                page = self.core_factory(api_client).list_namespace(
                    limit=limit, _request_timeout=remaining_timeout()
                )
                result = bounded_page(page, limit, lambda item: str(item.metadata.name))
                return NamespaceList(
                    items=result.items,
                    cut=result.cut,
                    more_available=result.more_available,
                    truncation=result.truncation,
                )

            return KubeReader(self.settings, self.client_factory).read_result(read_namespaces)

        k8s_list_namespaces.metadata = {"read_only": True}
        return [
            k8s_list_namespaces,
            *read_tools(
                KubeReader(self.settings, self.client_factory),
                self.core_factory,
                self.apps_factory,
                self.now,
            ),
        ]

    def checks(self) -> list[QuickCheck]:
        return quick_checks(
            KubeReader(self.settings, self.client_factory), self.core_factory, self.now
        )
