from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from pydantic import Field

from opensre.connectors.kubernetes.details import as_list, conditions, container_details, event_time
from opensre.connectors.kubernetes.infrastructure import infrastructure_tools
from opensre.connectors.kubernetes.models import EventSummary, PodDetail, PodLogs, PodSummary
from opensre.connectors.kubernetes.reader import KubeReader, Page, bounded_page
from opensre.connectors.kubernetes.workloads import workload_tools
from opensre.output import cap_text

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
        restarts=sum(
            status.restart_count or 0
            for status in statuses + as_list(pod.status.init_container_statuses)
        ),
        age_s=max(0, int((now - created).total_seconds())) if created else None,
        node=pod.spec.node_name,
    )


def read_tools(
    reader: KubeReader,
    core_factory: Callable[[Any], Any],
    apps_factory: Callable[[Any], Any],
    now: Callable[[], datetime],
) -> list[BaseTool]:
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
            return bounded_page(page, limit, lambda pod: pod_summary(pod, now()))

        return reader.read_result(read)

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

        return reader.read_result(read)

    @tool(response_format="content_and_artifact")
    def k8s_pod_logs(
        name: str,
        namespace: str | None = None,
        container: str | None = None,
        tail_lines: Annotated[int, Field(ge=1, le=1000)] = 100,
        previous: bool = False,
    ) -> tuple[str, PodLogs]:
        """Read bounded tail logs; previous=True reads the crashed container run."""

        def read(api: Any) -> PodLogs:
            kwargs: dict[str, Any] = dict(
                name=name,
                namespace=namespace or reader.settings.namespace,
                tail_lines=tail_lines,
                previous=previous,
                limit_bytes=16000,
                _request_timeout=reader.settings.request_timeout_s,
            )
            if container:
                kwargs["container"] = container
            text = str(core_factory(api).read_namespaced_pod_log(**kwargs) or "")
            shown = 16000
            result = PodLogs(
                text=cap_text(text, shown),
                cut=max(0, len(text) - 16000),
                server_cap_bytes=16000,
                truncation="Tail only; API limit 16000 bytes; older omitted count unknown",
            )
            while len(result.model_dump_json()) > 19000:
                shown //= 2
                result.text = cap_text(text, shown)
                result.cut = max(0, len(text) - shown)
            return result

        return reader.read_result(read)

    @tool(response_format="content_and_artifact")
    def k8s_list_events(
        namespace: str | None = None, limit: Limit = 50
    ) -> tuple[str, Page[EventSummary]]:
        """Read a bounded event page, warnings first, with reason, object, count and last seen."""

        def read(api: Any) -> Page[EventSummary]:
            page = core_factory(api).list_namespaced_event(
                namespace=namespace or reader.settings.namespace,
                limit=limit,
                _request_timeout=reader.settings.request_timeout_s,
            )
            page.items = sorted(
                page.items,
                key=lambda event: (
                    event.type != "Warning",
                    -(event_time(event) or datetime.min.replace(tzinfo=UTC)).timestamp(),
                ),
            )
            return bounded_page(
                page,
                limit,
                lambda event: EventSummary(
                    type=event.type,
                    reason=event.reason,
                    object=f"{event.involved_object.kind}/{event.involved_object.name}",
                    count=event.count or 1,
                    last_seen=event_time(event),
                ),
            )

        return reader.read_result(read)

    tools: list[BaseTool] = [
        k8s_list_pods,
        k8s_describe_pod,
        k8s_pod_logs,
        k8s_list_events,
        *workload_tools(reader, apps_factory),
        *infrastructure_tools(reader, core_factory),
    ]
    for entry in tools:
        entry.metadata = {"read_only": True}
    return tools
