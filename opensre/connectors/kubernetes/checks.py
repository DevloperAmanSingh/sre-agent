from collections.abc import Callable
from datetime import datetime
from typing import Any

from opensre.connectors.kubernetes.details import event_time, pod_observation
from opensre.connectors.kubernetes.execution import remaining_timeout
from opensre.connectors.kubernetes.models import ContainerObservation, PodObservation, Termination
from opensre.connectors.kubernetes.reader import KubeReader
from opensre.connectors.kubernetes.redaction import redact
from opensre.domain import Evidence, Finding, QuickCheck, Severity
from opensre.output import cap_text


def finding(
    pod: PodObservation,
    reason: str,
    detail: str,
    severity: Severity = Severity.CRITICAL,
    source: str = "k8s_describe_pod",
) -> Finding:
    resource = f"pod/{pod.namespace}/{pod.name}"
    return Finding(
        resource=resource,
        reason=redact(reason) or "Unknown",
        summary=redact(f"{resource}: {reason}") or "Unknown",
        severity=severity,
        evidence=[Evidence(source=source, detail=cap_text(redact(detail) or "", 2000))],
    )


def recent(timestamp: datetime | None, now: datetime) -> bool:
    return timestamp is not None and 0 <= (now - timestamp).total_seconds() <= 3600


def terminations(container: ContainerObservation) -> list[Termination]:
    return [term for term in (container.current_termination, container.last_termination) if term]


def oom_terms(container: ContainerObservation, now: datetime) -> list[Termination]:
    return sorted(
        [
            term
            for term in terminations(container)
            if term.reason == "OOMKilled" and recent(term.finished_at, now)
        ],
        key=lambda term: term.finished_at or now,
        reverse=True,
    )


def crashing(container: ContainerObservation, policy: str, now: datetime) -> bool:
    if oom_terms(container, now):
        return False
    if container.state_kind == "waiting" and container.state == "CrashLoopBackOff":
        return True
    failures = {
        term.finished_at
        for term in terminations(container)
        if term.exit_code and recent(term.finished_at, now)
    }
    return policy == "Always" and not container.ready and len(failures) >= 2


def crashloop(pod: PodObservation, now: datetime) -> list[Finding]:
    return [
        finding(
            pod,
            "CrashLoopBackOff",
            f"Container {container.name}: repeated recent failed runs or CrashLoopBackOff; "
            f"current={container.current_termination}; previous={container.last_termination}",
        )
        for container in pod.containers
        if crashing(container, pod.restart_policy, now)
    ]


def image_pull(pod: PodObservation, now: datetime) -> list[Finding]:
    reasons = {"ImagePullBackOff", "ErrImagePull", "InvalidImageName", "ErrImageNeverPull"}
    return [
        finding(
            pod,
            container.state or "ImagePullFailure",
            f"Container {container.name} is waiting in {container.state}",
        )
        for container in pod.containers
        if container.state_kind == "waiting" and container.state in reasons
    ]


def oom(pod: PodObservation, now: datetime) -> list[Finding]:
    findings: list[Finding] = []
    for container in pod.containers:
        terms = oom_terms(container, now)
        if not terms:
            continue
        latest = terms[0]
        result = finding(
            pod,
            "OOMKilled",
            f"Container {container.name}: OOMKilled exit={latest.exit_code} "
            f"at {latest.finished_at}",
        )
        for older in terms[1:]:
            if older != latest:
                result.evidence.append(
                    Evidence(
                        source="k8s_describe_pod",
                        detail=f"Container {container.name}: older OOMKilled "
                        f"at {older.finished_at}",
                    )
                )
        findings.append(result)
    return findings


def restarts(pod: PodObservation, now: datetime) -> list[Finding]:
    findings: list[Finding] = []
    for container in pod.containers:
        if crashing(container, pod.restart_policy, now) or oom_terms(container, now):
            continue
        term = container.last_termination
        if not container.restart_count or not term or not recent(term.finished_at, now):
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
                    f"Container {container.name} restarted after {term.reason}, "
                    f"exit={term.exit_code} at {term.finished_at}",
                    Severity.WARNING,
                )
            )
    return findings


def not_ready(pod: PodObservation, now: datetime) -> list[Finding]:
    if pod.phase != "Running" or pod.created is None or (now - pod.created).total_seconds() <= 600:
        return []
    regular = [container for container in pod.containers if not container.init]
    condition = next((condition for condition in pod.conditions if condition.type == "Ready"), None)
    ready = (
        condition.status == "True"
        if condition
        else bool(regular) and all(container.ready for container in regular)
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


def pod_events(pod: PodObservation, events: list[Any], now: datetime) -> list[Any]:
    return [
        event
        for event in events
        if event.involved_object.kind == "Pod"
        and event.involved_object.name == pod.name
        and event.metadata.namespace == pod.namespace
        and (not event.involved_object.uid or event.involved_object.uid == pod.uid)
        and recent(event_time(event), now)
    ]


def pending(pod: PodObservation, events: list[Any], now: datetime) -> list[Finding]:
    condition = next(
        (condition for condition in pod.conditions if condition.type == "PodScheduled"), None
    )
    if pod.phase != "Pending" or (condition and condition.status == "True"):
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
                event.message or event.reason,
                Severity.WARNING,
                "k8s_list_events",
            )
        ]
    reason = condition.reason if condition and condition.reason else "Pending"
    return [
        finding(
            pod,
            reason,
            condition.message
            if condition and condition.message
            else "Pod is Pending; no recent scheduling event available",
            Severity.WARNING,
        )
    ]


def probes(pod: PodObservation, events: list[Any], now: datetime) -> list[Finding]:
    return [
        finding(pod, "ProbeFailure", event.message, Severity.WARNING, "k8s_list_events")
        for event in pod_events(pod, events, now)
        if event.reason == "Unhealthy"
        and event.type == "Warning"
        and "probe failed" in (event.message or "").casefold()
    ]


class Snapshot:
    def __init__(self, reader: KubeReader, core_factory: Callable[[Any], Any]) -> None:
        self.reader = reader
        self.core_factory = core_factory
        self._pods: list[PodObservation] | None = None
        self._events: list[Any] | None = None

    def pages(self, method: Callable[..., Any]) -> list[Any]:
        items: list[Any] = []
        token = ""
        seen: set[str] = set()
        while True:
            kwargs: dict[str, Any] = dict(
                namespace=self.reader.settings.namespace,
                limit=100,
                _request_timeout=remaining_timeout(),
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

    def pods(self, api: Any) -> list[PodObservation]:
        if self._pods is None:
            self._pods = [
                pod_observation(pod)
                for pod in self.pages(self.core_factory(api).list_namespaced_pod)
            ]
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

    def check(name: str, rule: Callable[[PodObservation, datetime], list[Finding]]) -> QuickCheck:
        def run() -> list[Finding]:
            return reader.read(
                lambda api: [item for pod in snapshot.pods(api) for item in rule(pod, timestamp)]
            )

        return QuickCheck(name=name, run=run)

    def run_pending() -> list[Finding]:
        def read(api: Any) -> list[Finding]:
            pods = [pod for pod in snapshot.pods(api) if pod.phase == "Pending"]
            events = snapshot.events(api) if pods else []
            return [item for pod in pods for item in pending(pod, events, timestamp)]

        return reader.read(read)

    def run_probes() -> list[Finding]:
        def read(api: Any) -> list[Finding]:
            pods = snapshot.pods(api)
            events = snapshot.events(api) if pods else []
            return [item for pod in pods for item in probes(pod, events, timestamp)]

        return reader.read(read)

    return [
        check("crashloop", crashloop),
        check("oom", oom),
        check("image-pull", image_pull),
        check("not-ready", not_ready),
        check("restarts", restarts),
        QuickCheck(name="pending", run=run_pending),
        QuickCheck(name="probes", run=run_probes),
    ]
