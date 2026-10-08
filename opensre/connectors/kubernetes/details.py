from datetime import datetime
from typing import Any

from opensre.connectors.kubernetes.models import (
    Condition,
    Container,
    ContainerObservation,
    EventSummary,
    PodObservation,
    Probe,
    Termination,
)
from opensre.output import cap_text
from opensre.redaction import redact


def recent(timestamp: datetime | None, now: datetime) -> bool:
    return timestamp is not None and 0 <= (now - timestamp).total_seconds() <= 3600


def bounded_names(values: list[str]) -> tuple[list[str], int]:
    names: list[str] = []
    size = 0
    for value in values[:50]:
        name = cap_text(value, 128)
        size += len(name) + 3
        if size > 1024:
            break
        names.append(name)
    return names, len(values) - len(names)


def event_time(event: Any) -> datetime | None:
    return (
        getattr(getattr(event, "series", None), "last_observed_time", None)
        or event.last_timestamp
        or event.event_time
        or event.first_timestamp
    )


def event_summary(event: Any) -> EventSummary:
    return EventSummary(
        type=event.type,
        reason=redact(event.reason),
        message=cap_text(redact(event.message) or "", 2000),
        object=f"{event.involved_object.kind}/{event.involved_object.name}",
        count=event.count or 1,
        last_seen=event_time(event),
    )


def as_list(value: Any) -> list[Any]:
    return value or []


def conditions(items: list[Any] | None) -> list[Condition]:
    return [
        Condition(
            type=item.type,
            status=item.status,
            reason=redact(item.reason),
            message=cap_text(redact(getattr(item, "message", None)) or "", 512),
        )
        for item in (items or [])[:50]
    ]


def termination(state: Any) -> Termination | None:
    term = getattr(state, "terminated", None)
    if not term:
        return None
    return Termination(
        reason=redact(term.reason), exit_code=term.exit_code, finished_at=term.finished_at
    )


def container_observation(status: Any, name: str, *, init: bool = False) -> ContainerObservation:
    state = getattr(status, "state", None)
    waiting = getattr(state, "waiting", None)
    current = termination(state)
    kind = (
        "waiting"
        if waiting
        else "terminated"
        if current
        else "running"
        if getattr(state, "running", None)
        else "unknown"
    )
    return ContainerObservation(
        name=name,
        init=init,
        state_kind=kind,
        state=redact(waiting.reason)
        if waiting
        else current.reason
        if current
        else "Running"
        if kind == "running"
        else None,
        ready=bool(getattr(status, "ready", False)),
        restart_count=getattr(status, "restart_count", 0) or 0,
        current_termination=current,
        last_termination=termination(getattr(status, "last_state", None)),
    )


def pod_observation(pod: Any) -> PodObservation:
    regular = as_list(pod.status.container_statuses)
    init = as_list(pod.status.init_container_statuses)
    return PodObservation(
        name=pod.metadata.name,
        namespace=pod.metadata.namespace,
        uid=pod.metadata.uid,
        phase=pod.status.phase,
        created=pod.metadata.creation_timestamp,
        restart_policy=getattr(pod.spec, "restart_policy", None) or "Always",
        containers=[container_observation(status, status.name) for status in regular]
        + [container_observation(status, status.name, init=True) for status in init],
        conditions=conditions(pod.status.conditions),
    )


def container_details(spec: Any, status: Any = None) -> Container:
    probes: dict[str, Probe] = {}
    for name in ("startup", "readiness", "liveness"):
        probe = getattr(spec, f"{name}_probe", None)
        if probe:
            kind = next(
                (
                    kind
                    for kind in ("_exec", "http_get", "tcp_socket", "grpc")
                    if getattr(probe, kind, None)
                ),
                "unknown",
            )
            probes[name] = Probe(
                kind=kind.lstrip("_"),
                initial_delay_s=probe.initial_delay_seconds,
                period_s=probe.period_seconds,
                timeout_s=probe.timeout_seconds,
                failure_threshold=probe.failure_threshold,
            )
    resources = spec.resources
    env: list[Any] = spec.env or []
    secrets: list[str] = []
    for variable in env:
        reference = getattr(getattr(variable, "value_from", None), "secret_key_ref", None)
        if reference:
            secrets.append(reference.name)
    for source in as_list(spec.env_from):
        if source.secret_ref:
            secrets.append(source.secret_ref.name)
    observation = container_observation(status, spec.name)
    env_names, env_cut = bounded_names([variable.name for variable in env])
    secret_names, secret_cut = bounded_names(secrets)
    return Container(
        **observation.model_dump(),
        image=spec.image,
        env_names=env_names,
        secret_names=secret_names,
        requests=(resources.requests or {}) if resources else {},
        limits=(resources.limits or {}) if resources else {},
        probes=probes,
        cut={"env_names": env_cut, "secret_names": secret_cut},
    )
