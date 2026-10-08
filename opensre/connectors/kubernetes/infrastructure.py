from collections.abc import Callable
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from pydantic import Field

from opensre.connectors.kubernetes.details import as_list
from opensre.connectors.kubernetes.execution import remaining_timeout
from opensre.connectors.kubernetes.models import NodeSummary, ServicePort, ServiceSummary
from opensre.connectors.kubernetes.reader import KubeReader, Page, bounded_page


def infrastructure_tools(reader: KubeReader, core_factory: Callable[[Any], Any]) -> list[BaseTool]:
    @tool(response_format="content_and_artifact")
    def k8s_list_services(
        namespace: str | None = None, limit: Annotated[int, Field(ge=1, le=100)] = 50
    ) -> tuple[str, Page[ServiceSummary]]:
        """Read service types, ports, selectors and ready endpoint counts."""

        def read(api: Any) -> Page[ServiceSummary]:
            core = core_factory(api)
            ns = namespace or reader.settings.namespace
            page = core.list_namespaced_service(
                namespace=ns, limit=limit, _request_timeout=remaining_timeout()
            )
            endpoints = core.list_namespaced_endpoints(
                namespace=ns, limit=100, _request_timeout=remaining_timeout()
            )
            counts = {
                endpoint.metadata.name: sum(
                    len(as_list(subset.addresses)) for subset in as_list(endpoint.subsets)
                )
                for endpoint in endpoints.items[:100]
            }
            complete = not endpoints.metadata._continue and len(endpoints.items) <= 100

            def summarize(service: Any) -> ServiceSummary:
                external = service.spec.type == "ExternalName"
                known = external or complete or service.metadata.name in counts
                count = (
                    0 if external else counts.get(service.metadata.name, 0 if complete else None)
                )
                ports = as_list(service.spec.ports)
                return ServiceSummary(
                    name=service.metadata.name,
                    type=service.spec.type,
                    ports=[
                        ServicePort(
                            name=port.name,
                            port=port.port,
                            target_port=port.target_port,
                            protocol=port.protocol,
                        )
                        for port in ports[:50]
                    ],
                    ports_cut=max(0, len(ports) - 50),
                    selector=service.spec.selector or {},
                    ready_endpoints=count,
                    endpoints_complete=known,
                )

            return bounded_page(page, limit, summarize)

        return reader.read_result(read)

    @tool(response_format="content_and_artifact")
    def k8s_list_nodes(
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> tuple[str, Page[NodeSummary]]:
        """Read node readiness, pressure conditions, capacity, allocatable and kubelet version."""

        def read(api: Any) -> Page[NodeSummary]:
            page = core_factory(api).list_node(limit=limit, _request_timeout=remaining_timeout())

            def summarize(node: Any) -> NodeSummary:
                condition_map = {
                    condition.type: condition.status
                    for condition in as_list(node.status.conditions)
                }
                return NodeSummary(
                    name=node.metadata.name,
                    ready=condition_map.get("Ready") == "True",
                    pressure={
                        key: value
                        for key, value in condition_map.items()
                        if key.endswith("Pressure")
                    },
                    capacity=node.status.capacity or {},
                    allocatable=node.status.allocatable or {},
                    version=node.status.node_info.kubelet_version
                    if node.status.node_info
                    else None,
                )

            return bounded_page(page, limit, summarize)

        return reader.read_result(read)

    return [k8s_list_services, k8s_list_nodes]
