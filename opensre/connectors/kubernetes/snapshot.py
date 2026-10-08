from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from opensre.connectors.kubernetes.details import event_summary, event_time, pod_observation, recent
from opensre.connectors.kubernetes.execution import remaining_timeout
from opensre.connectors.kubernetes.models import EventSummary, PodObservation
from opensre.connectors.kubernetes.reader import KubeReader


class Snapshot(BaseModel):
    pods: list[PodObservation]
    events: dict[tuple[str, str], list[EventSummary]]


def read_pages(method: Callable[..., Any], namespace: str) -> list[Any]:
    items: list[Any] = []
    token = ""
    seen: set[str] = set()
    while True:
        kwargs: dict[str, Any] = dict(
            namespace=namespace, limit=100, _request_timeout=remaining_timeout()
        )
        if token:
            kwargs["_continue"] = token
        page = method(**kwargs)
        items.extend(page.items)
        token = page.metadata._continue or ""
        if not token:
            return items
        if token in seen:
            raise ValueError("Kubernetes pagination repeated a continuation token")
        seen.add(token)


def acquire_snapshot(
    reader: KubeReader, core_factory: Callable[[Any], Any], now: datetime
) -> Snapshot:
    def read(api: Any) -> Snapshot:
        core = core_factory(api)
        namespace = reader.settings.namespace
        pods = [pod_observation(pod) for pod in read_pages(core.list_namespaced_pod, namespace)]
        by_name = {(pod.namespace, pod.name): pod for pod in pods}
        index: dict[tuple[str, str], list[EventSummary]] = {}
        for event in read_pages(core.list_namespaced_event, namespace):
            reference = event.involved_object
            key = (event.metadata.namespace, reference.name)
            pod = by_name.get(key)
            if reference.kind != "Pod" or pod is None or not recent(event_time(event), now):
                continue
            if reference.uid and reference.uid != pod.uid:
                continue
            index.setdefault(key, []).append(event_summary(event))
        return Snapshot(pods=pods, events=index)

    return reader.read(read)
