from datetime import datetime

from pydantic import BaseModel, Field


class PodSummary(BaseModel):
    name: str
    namespace: str
    phase: str
    ready: str
    restarts: int
    age_s: int | None
    node: str | None


class Termination(BaseModel):
    reason: str | None
    exit_code: int | None
    finished_at: datetime | None


class Probe(BaseModel):
    kind: str
    initial_delay_s: int | None
    period_s: int | None
    timeout_s: int | None
    failure_threshold: int | None


class Container(BaseModel):
    name: str
    image: str | None
    env_names: list[str]
    secret_names: list[str]
    requests: dict[str, str]
    limits: dict[str, str]
    probes: dict[str, Probe]
    state: str | None = None
    last_termination: Termination | None = None


class Condition(BaseModel):
    type: str
    status: str
    reason: str | None


class PodLogs(BaseModel):
    text: str
    cut: int
    server_cap_bytes: int
    truncation: str


class PodDetail(BaseModel):
    name: str
    namespace: str
    containers: list[Container]
    conditions: list[Condition]
    cut: dict[str, int] = Field(default_factory=dict)
