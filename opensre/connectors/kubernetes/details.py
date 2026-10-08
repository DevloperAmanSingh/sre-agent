from datetime import datetime
from typing import Any

from opensre.connectors.kubernetes.models import (
    Condition,
    Container,
    ContainerObservation,
    Probe,
    Termination,
)
from opensre.connectors.kubernetes.redaction import redact


def event_time(event: Any) -> datetime | None:
    return (
        getattr(getattr(event, "series", None), "last_observed_time", None)
        or event.last_timestamp
        or event.event_time
        or event.first_timestamp
    )


def as_list(value: Any) -> list[Any]:
    return value or []


def conditions(items: list[Any] | None) -> list[Condition]:
    return [
        Condition(type=item.type, status=item.status, reason=redact(item.reason))
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
    return Container(
        **observation.model_dump(),
        image=spec.image,
        env_names=[item.name for item in env[:50]],
        secret_names=secrets[:50],
        requests=(resources.requests or {}) if resources else {},
        limits=(resources.limits or {}) if resources else {},
        probes=probes,
        cut={"env_names": max(0, len(env) - 50), "secret_names": max(0, len(secrets) - 50)},
    )
