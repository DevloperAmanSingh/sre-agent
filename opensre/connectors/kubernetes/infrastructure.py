from collections.abc import Callable
from typing import Annotated, Any

from kubernetes.client.exceptions import ApiException  # pyright: ignore[reportMissingTypeStubs]
from langchain_core.tools import BaseTool, tool
from pydantic import Field

from opensre.connectors.kubernetes.details import as_list
from opensre.connectors.kubernetes.models import ServicePort, ServiceSummary
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
            timeout = reader.settings.request_timeout_s
            page = core.list_namespaced_service(namespace=ns, limit=limit, _request_timeout=timeout)

            def summarize(service: Any) -> ServiceSummary:
                count = 0
                if service.spec.type != "ExternalName":
                    try:
                        endpoints = core.read_namespaced_endpoints(
                            name=service.metadata.name, namespace=ns, _request_timeout=timeout
                        )
                        count = sum(
                            len(as_list(subset.addresses)) for subset in as_list(endpoints.subsets)
                        )
                    except ApiException as exc:
                        if getattr(exc, "status", None) != 404:
                            raise
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
                )

            return bounded_page(page, limit, summarize)

        result = reader.read(read)
        return result.model_dump_json(), result

    return [k8s_list_services]
