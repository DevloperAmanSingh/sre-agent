from collections.abc import Callable
from datetime import datetime
from typing import Any

from opensre.connectors.kubernetes.details import recent
from opensre.connectors.kubernetes.models import (
    ContainerObservation,
    EventSummary,
    PodObservation,
    Termination,
)
from opensre.connectors.kubernetes.reader import KubeReader
from opensre.connectors.kubernetes.snapshot import Snapshot, acquire_snapshot
from opensre.domain import Evidence, Finding, QuickCheck, Severity
from opensre.output import cap_text
from opensre.redaction import redact


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


def failed_terms(container: ContainerObservation, now: datetime) -> list[Termination]:
    return sorted(
        [
            term
            for term in terminations(container)
            if term.exit_code and recent(term.finished_at, now)
        ],
        key=lambda term: term.finished_at or now,
        reverse=True,
    )


def termination_detail(name: str, term: Termination) -> str:
    exit_code = term.exit_code if term.exit_code is not None else "unknown"
    return (
        f"Container {name}: {term.reason or 'Unknown'} exit={exit_code} "
        f"at {term.finished_at or 'unknown'}"
    )


def crashing(container: ContainerObservation, policy: str, now: datetime) -> bool:
    if oom_terms(container, now):
        return False
    if container.state_kind == "waiting" and container.state == "CrashLoopBackOff":
        return True
    failures = {term.finished_at for term in failed_terms(container, now)}
    return policy == "Always" and not container.ready and len(failures) >= 2


def crashloop(pod: PodObservation, now: datetime) -> list[Finding]:
    findings: list[Finding] = []
    for container in pod.containers:
        if not crashing(container, pod.restart_policy, now):
            continue
        if container.state_kind == "waiting" and container.state == "CrashLoopBackOff":
            detail = f"Container {container.name}: waiting in CrashLoopBackOff"
        else:
            failures = failed_terms(container, now)
            count = len({term.finished_at for term in failures})
            detail = (
                f"{termination_detail(container.name, failures[0])}, "
                f"{count} failed runs in the last hour"
            )
        findings.append(finding(pod, "CrashLoopBackOff", detail))
    return findings


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
            termination_detail(container.name, latest),
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
                    f"{termination_detail(container.name, term)}, restarted in the last hour",
                    Severity.WARNING,
                )
            )
    return findings


def not_ready(pod: PodObservation, now: datetime) -> list[Finding]:
    if pod.phase != "Running" or pod.created is None or (now - pod.created).total_seconds() <= 600:
        return []
    if crashloop(pod, now) or oom(pod, now) or image_pull(pod, now):
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


def pending(pod: PodObservation, events: list[EventSummary], now: datetime) -> list[Finding]:
    condition = next(
        (condition for condition in pod.conditions if condition.type == "PodScheduled"), None
    )
    if pod.phase != "Pending" or (condition and condition.status == "True"):
        return []
    scheduling = [event for event in events if event.reason == "FailedScheduling"]
    if scheduling:
        event = max(scheduling, key=lambda item: item.last_seen or now)
        return [
            finding(
                pod,
                event.reason or "FailedScheduling",
                event.message or event.reason or "FailedScheduling",
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


def probes(pod: PodObservation, events: list[EventSummary], now: datetime) -> list[Finding]:
    return [
        finding(
            pod,
            "ProbeFailure",
            event.message or "Probe failure",
            Severity.WARNING,
            "k8s_list_events",
        )
        for event in events
        if event.reason == "Unhealthy"
        and event.type == "Warning"
        and "probe failed" in (event.message or "").casefold()
    ]


def quick_checks(
    reader: KubeReader, core_factory: Callable[[Any], Any], now: Callable[[], datetime]
) -> list[QuickCheck]:
    snapshot: Snapshot | None = None
    timestamp = now()

    def check(
        name: str, rule: Callable[[PodObservation, list[EventSummary]], list[Finding]]
    ) -> QuickCheck:
        def run() -> list[Finding]:
            nonlocal snapshot
            if snapshot is None:
                snapshot = acquire_snapshot(reader, core_factory, timestamp)
            return [
                item
                for pod in snapshot.pods
                for item in rule(pod, snapshot.events.get((pod.namespace, pod.name), []))
            ]

        return QuickCheck(name=name, run=run)

    return [
        check("crashloop", lambda pod, events: crashloop(pod, timestamp)),
        check("oom", lambda pod, events: oom(pod, timestamp)),
        check("image-pull", lambda pod, events: image_pull(pod, timestamp)),
        check("not-ready", lambda pod, events: not_ready(pod, timestamp)),
        check("restarts", lambda pod, events: restarts(pod, timestamp)),
        check("pending", lambda pod, events: pending(pod, events, timestamp)),
        check("probes", lambda pod, events: probes(pod, events, timestamp)),
    ]
