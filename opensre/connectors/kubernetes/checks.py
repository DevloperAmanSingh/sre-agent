from collections.abc import Callable
from datetime import datetime
from typing import Any

from opensre.connectors.kubernetes.details import as_list, event_time, termination
from opensre.connectors.kubernetes.models import Termination
from opensre.connectors.kubernetes.reader import KubeReader
from opensre.domain import Evidence, Finding, QuickCheck, Severity
from opensre.output import cap_text


def statuses(pod: Any) -> list[Any]:
    return as_list(pod.status.init_container_statuses) + as_list(pod.status.container_statuses)


def finding(
    pod: Any,
    reason: str,
    detail: str,
    severity: Severity = Severity.CRITICAL,
    source: str = "k8s_describe_pod",
) -> Finding:
    resource = f"pod/{pod.metadata.namespace}/{pod.metadata.name}"
    return Finding(
        resource=resource,
        reason=reason,
        summary=f"{resource}: {reason}",
        severity=severity,
        evidence=[Evidence(source=source, detail=detail)],
    )


def waiting_failures(pod: Any, reasons: set[str]) -> list[Finding]:
    return [
        finding(pod, reason, f"Container {status.name} is waiting in {reason}")
        for status in statuses(pod)
        if (reason := getattr(getattr(status.state, "waiting", None), "reason", None)) in reasons
    ]


def crashloop(pod: Any, now: datetime) -> list[Finding]:
    return waiting_failures(pod, {"CrashLoopBackOff"})


def image_pull(pod: Any, now: datetime) -> list[Finding]:
    return waiting_failures(
        pod, {"ImagePullBackOff", "ErrImagePull", "InvalidImageName", "ErrImageNeverPull"}
    )


def recent(timestamp: datetime | None, now: datetime) -> bool:
    return timestamp is not None and 0 <= (now - timestamp).total_seconds() <= 3600


def terminations(status: Any) -> list[Termination]:
    return [term for state in (status.state, status.last_state) if (term := termination(state))]


def oom(pod: Any, now: datetime) -> list[Finding]:
    return [
        finding(
            pod,
            "OOMKilled",
            f"Container {status.name}: OOMKilled exit={term.exit_code} at {term.finished_at}",
        )
        for status in statuses(pod)
        for term in terminations(status)
        if term.reason == "OOMKilled" and recent(term.finished_at, now)
    ]


def restarts(pod: Any, now: datetime) -> list[Finding]:
    findings: list[Finding] = []
    for status in statuses(pod):
        term = termination(status.last_state)
        if not status.restart_count or not term or not recent(term.finished_at, now):
            continue
        failed = term.reason in {
            "OOMKilled",
            "Error",
            "ContainerCannotRun",
            "DeadlineExceeded",
        } or bool(term.exit_code)
        if failed:
            findings.append(
                finding(
                    pod,
                    "RecentRestart",
                    f"Container {status.name} restarted after {term.reason}, "
                    f"exit={term.exit_code} at {term.finished_at}",
                    Severity.WARNING,
                )
            )
    return findings


def not_ready(pod: Any, now: datetime) -> list[Finding]:
    created = pod.metadata.creation_timestamp
    if pod.status.phase != "Running" or created is None or (now - created).total_seconds() <= 600:
        return []
    regular = as_list(pod.status.container_statuses)
    ready_condition = next(
        (condition for condition in as_list(pod.status.conditions) if condition.type == "Ready"),
        None,
    )
    ready = (
        ready_condition.status == "True"
        if ready_condition
        else bool(regular) and all(status.ready for status in regular)
    )
    return (
        []
        if ready
        else [
            finding(
                pod,
                "NotReady",
                "Running pod is not ready past the 10-minute startup grace",
                Severity.WARNING,
            )
        ]
    )


def pod_events(pod: Any, events: list[Any], now: datetime) -> list[Any]:
    return [
        event
        for event in events
        if event.involved_object.kind == "Pod"
        and event.involved_object.name == pod.metadata.name
        and event.metadata.namespace == pod.metadata.namespace
        and (not event.involved_object.uid or event.involved_object.uid == pod.metadata.uid)
        and recent(event_time(event), now)
    ]


def pending(pod: Any, events: list[Any], now: datetime) -> list[Finding]:
    if pod.status.phase != "Pending":
        return []
    scheduling = [
        event for event in pod_events(pod, events, now) if event.reason == "FailedScheduling"
    ]
    if scheduling:
        event = max(scheduling, key=lambda item: event_time(item) or now)
        return [
            finding(
                pod,
                event.reason,
                cap_text(event.message or event.reason, 2000),
                Severity.WARNING,
                "k8s_list_events",
            )
        ]
    condition = next(
        (
            condition
            for condition in as_list(pod.status.conditions)
            if condition.type == "PodScheduled" and condition.status == "False"
        ),
        None,
    )
    reason = condition.reason if condition and condition.reason else "Pending"
    return [
        finding(
            pod, reason, "Pod is Pending; no recent scheduling event available", Severity.WARNING
        )
    ]


class Snapshot:
    def __init__(self, reader: KubeReader, core_factory: Callable[[Any], Any]) -> None:
        self.reader = reader
        self.core_factory = core_factory
        self._pods: list[Any] | None = None
        self._events: list[Any] | None = None

    def pages(self, method: Callable[..., Any]) -> list[Any]:
        items: list[Any] = []
        token = ""
        seen: set[str] = set()
        while True:
            kwargs: dict[str, Any] = dict(
                namespace=self.reader.settings.namespace,
                limit=100,
                _request_timeout=self.reader.settings.request_timeout_s,
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

    def pods(self, api: Any) -> list[Any]:
        if self._pods is None:
            self._pods = self.pages(self.core_factory(api).list_namespaced_pod)
        return self._pods

    def events(self, api: Any) -> list[Any]:
        if self._events is None:
            self._events = self.pages(self.core_factory(api).list_namespaced_event)
        return self._events


def quick_checks(
    reader: KubeReader, core_factory: Callable[[Any], Any], now: Callable[[], datetime]
) -> list[QuickCheck]:
    snapshot = Snapshot(reader, core_factory)
    timestamp = now()

    def check(name: str, rule: Callable[[Any, datetime], list[Finding]]) -> QuickCheck:
        def run() -> list[Finding]:
            return reader.read(
                lambda api: [item for pod in snapshot.pods(api) for item in rule(pod, timestamp)]
            )

        return QuickCheck(name=name, run=run)

    def run_pending() -> list[Finding]:
        def read(api: Any) -> list[Finding]:
            pods = [pod for pod in snapshot.pods(api) if pod.status.phase == "Pending"]
            events = snapshot.events(api) if pods else []
            return [item for pod in pods for item in pending(pod, events, timestamp)]

        return reader.read(read)

    return [
        check("crashloop", crashloop),
        check("oom", oom),
        check("image-pull", image_pull),
        check("not-ready", not_ready),
        check("restarts", restarts),
        QuickCheck(name="pending", run=run_pending),
    ]
