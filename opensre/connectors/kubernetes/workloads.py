from collections.abc import Callable
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from pydantic import Field

from opensre.connectors.kubernetes.models import DeploymentSummary
from opensre.connectors.kubernetes.reader import KubeReader, Page, bounded_page


def workload_tools(reader: KubeReader, apps_factory: Callable[[Any], Any]) -> list[BaseTool]:
    @tool(response_format="content_and_artifact")
    def k8s_list_deployments(
        namespace: str | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> tuple[str, Page[DeploymentSummary]]:
        """Read desired, ready, available and updated deployment replica counts."""

        def read(api: Any) -> Page[DeploymentSummary]:
            page = apps_factory(api).list_namespaced_deployment(
                namespace=namespace or reader.settings.namespace,
                limit=limit,
                _request_timeout=reader.settings.request_timeout_s,
            )
            return bounded_page(
                page,
                limit,
                lambda deployment: DeploymentSummary(
                    name=deployment.metadata.name,
                    desired=deployment.spec.replicas or 0,
                    ready=deployment.status.ready_replicas or 0,
                    available=deployment.status.available_replicas or 0,
                    updated=deployment.status.updated_replicas or 0,
                ),
            )

        result = reader.read(read)
        return result.model_dump_json(), result

    return [k8s_list_deployments]
