from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from pydantic import Field

from opensre.connectors.kubernetes.details import as_list, conditions, container_details
from opensre.connectors.kubernetes.models import PodDetail, PodSummary
from opensre.connectors.kubernetes.reader import KubeReader, Page, bounded_page

Limit = Annotated[int, Field(ge=1, le=100)]


def pod_summary(pod: Any, now: datetime) -> PodSummary:
    statuses: list[Any] = pod.status.container_statuses or []
    created = pod.metadata.creation_timestamp
    ready = sum(bool(status.ready) for status in statuses)
    total = len(pod.spec.containers or statuses)
    return PodSummary(
        name=pod.metadata.name,
        namespace=pod.metadata.namespace,
        phase=pod.status.phase or "Unknown",
        ready=f"{ready}/{total}",
        restarts=sum(status.restart_count or 0 for status in statuses),
        age_s=max(0, int((now - created).total_seconds())) if created else None,
        node=pod.spec.node_name,
    )


def read_tools(reader: KubeReader, core_factory: Callable[[Any], Any]) -> list[BaseTool]:
    @tool(response_format="content_and_artifact")
    def k8s_list_pods(
        namespace: str | None = None, limit: Limit = 50
    ) -> tuple[str, Page[PodSummary]]:
        """Read pod phase, readiness, restarts, age and node in a namespace."""

        def read(api: Any) -> Page[PodSummary]:
            page = core_factory(api).list_namespaced_pod(
                namespace=namespace or reader.settings.namespace,
                limit=limit,
                _request_timeout=reader.settings.request_timeout_s,
            )
            return bounded_page(page, limit, lambda pod: pod_summary(pod, datetime.now(UTC)))

        result = reader.read(read)
        return result.model_dump_json(), result

    @tool(response_format="content_and_artifact")
    def k8s_describe_pod(name: str, namespace: str | None = None) -> tuple[str, PodDetail]:
        """Read container state, last termination, images, resources and probes; no env values."""

        def read(api: Any) -> PodDetail:
            pod = core_factory(api).read_namespaced_pod(
                name=name,
                namespace=namespace or reader.settings.namespace,
                _request_timeout=reader.settings.request_timeout_s,
            )
            specs: list[Any] = as_list(pod.spec.init_containers) + as_list(pod.spec.containers)
            statuses: list[Any] = as_list(pod.status.init_container_statuses) + as_list(
                pod.status.container_statuses
            )
            by_name = {status.name: status for status in statuses}
            return PodDetail(
                name=pod.metadata.name,
                namespace=pod.metadata.namespace,
                containers=[container_details(spec, by_name.get(spec.name)) for spec in specs[:50]],
                conditions=conditions(pod.status.conditions),
                cut={
                    "containers": max(0, len(specs) - 50),
                    "conditions": max(0, len(pod.status.conditions or []) - 50),
                },
            )

        result = reader.read(read)
        return result.model_dump_json(), result

    tools: list[BaseTool] = [k8s_list_pods, k8s_describe_pod]
    for entry in tools:
        entry.metadata = {"read_only": True}
    return tools
