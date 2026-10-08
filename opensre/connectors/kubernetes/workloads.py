from collections.abc import Callable
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from pydantic import Field

from opensre.connectors.kubernetes.details import as_list, conditions, container_details
from opensre.connectors.kubernetes.models import DeploymentDetail, DeploymentSummary
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

    @tool(response_format="content_and_artifact")
    def k8s_describe_deployment(
        name: str, namespace: str | None = None
    ) -> tuple[str, DeploymentDetail]:
        """Read deployment images, env names, resources, probes, strategy and conditions."""

        def read(api: Any) -> DeploymentDetail:
            deployment = apps_factory(api).read_namespaced_deployment(
                name=name,
                namespace=namespace or reader.settings.namespace,
                _request_timeout=reader.settings.request_timeout_s,
            )
            specs = as_list(deployment.spec.template.spec.init_containers) + as_list(
                deployment.spec.template.spec.containers
            )
            return DeploymentDetail(
                name=deployment.metadata.name,
                namespace=deployment.metadata.namespace,
                strategy=deployment.spec.strategy.type if deployment.spec.strategy else None,
                containers=[container_details(spec) for spec in specs[:50]],
                conditions=conditions(deployment.status.conditions),
                cut={
                    "containers": max(0, len(specs) - 50),
                    "conditions": max(0, len(deployment.status.conditions or []) - 50),
                },
            )

        result = reader.read(read)
        return result.model_dump_json(), result

    return [k8s_list_deployments, k8s_describe_deployment]
