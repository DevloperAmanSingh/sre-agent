from datetime import datetime
from typing import Any

from opensre.connectors.kubernetes.models import Condition, Container, Probe, Termination


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
        Condition(type=item.type, status=item.status, reason=item.reason)
        for item in (items or [])[:50]
    ]


def termination(state: Any) -> Termination | None:
    term = getattr(state, "terminated", None)
    if not term:
        return None
    return Termination(reason=term.reason, exit_code=term.exit_code, finished_at=term.finished_at)


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
    state = getattr(status, "state", None)
    waiting = getattr(state, "waiting", None)
    current = termination(state)
    return Container(
        name=spec.name,
        image=spec.image,
        env_names=[item.name for item in env[:50]],
        secret_names=secrets[:50],
        requests=(resources.requests or {}) if resources else {},
        limits=(resources.limits or {}) if resources else {},
        probes=probes,
        cut={"env_names": max(0, len(env) - 50), "secret_names": max(0, len(secrets) - 50)},
        state=waiting.reason
        if waiting
        else (
            current.reason if current else ("Running" if getattr(state, "running", None) else None)
        ),
        last_termination=termination(getattr(status, "last_state", None)),
    )
