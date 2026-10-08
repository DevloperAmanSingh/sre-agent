from collections.abc import Callable
from datetime import datetime
from typing import Any

from opensre.connectors.kubernetes.details import as_list, termination
from opensre.connectors.kubernetes.models import Termination
from opensre.connectors.kubernetes.reader import KubeReader
from opensre.domain import Evidence, Finding, QuickCheck, Severity


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


def crashloop(pod: Any, now: datetime) -> list[Finding]:
    return [
        finding(pod, "CrashLoopBackOff", f"Container {status.name} is waiting in CrashLoopBackOff")
        for status in statuses(pod)
        if getattr(getattr(status.state, "waiting", None), "reason", None) == "CrashLoopBackOff"
    ]


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


class Snapshot:
    def __init__(self, reader: KubeReader, core_factory: Callable[[Any], Any]) -> None:
        self.reader = reader
        self.core_factory = core_factory
        self._pods: list[Any] | None = None

    def pods(self, api: Any) -> list[Any]:
        if self._pods is None:
            core = self.core_factory(api)
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
                page = core.list_namespaced_pod(**kwargs)
                items.extend(page.items)
                token = page.metadata._continue or ""
                if not token:
                    break
                if token in seen:
                    raise ValueError("Kubernetes pagination repeated a continuation token")
                seen.add(token)
            self._pods = items
        return self._pods


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

    return [check("crashloop", crashloop), check("oom", oom)]
