from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from opensre.connectors.kubernetes.reader import BoundedResult


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


class ContainerObservation(BaseModel):
    name: str
    state: str | None
    state_kind: Literal["waiting", "running", "terminated", "unknown"]
    ready: bool
    restart_count: int
    init: bool = False
    current_termination: Termination | None
    last_termination: Termination | None


class Container(ContainerObservation):
    image: str | None
    env_names: list[str]
    secret_names: list[str]
    requests: dict[str, str]
    limits: dict[str, str]
    probes: dict[str, Probe]
    cut: dict[str, int] = Field(default_factory=dict)


class Condition(BaseModel):
    message: str | None = None
    type: str
    status: str
    reason: str | None


class PodObservation(BaseModel):
    name: str
    namespace: str
    uid: str | None
    phase: str | None
    created: datetime | None
    restart_policy: str
    containers: list[ContainerObservation]
    conditions: list[Condition]


class NodeSummary(BaseModel):
    name: str
    ready: bool
    pressure: dict[str, str]
    capacity: dict[str, str]
    allocatable: dict[str, str]
    version: str | None


class ServicePort(BaseModel):
    name: str | None
    port: int
    target_port: int | str | None
    protocol: str | None


class ServiceSummary(BaseModel):
    name: str
    type: str | None
    ports: list[ServicePort]
    ports_cut: int
    selector: dict[str, str]
    ready_endpoints: int


class Revision(BaseModel):
    revision: int | None
    change_cause: str | None
    images: list[str]
    images_cut: int


class DeploymentSummary(BaseModel):
    name: str
    desired: int
    ready: int
    available: int
    updated: int


class EventSummary(BaseModel):
    message: str | None
    type: str | None
    reason: str | None
    object: str
    count: int
    last_seen: datetime | None


class PodLogs(BoundedResult):
    text: str
    cut: int
    server_cap_bytes: int
    truncation: str


class DeploymentDetail(BoundedResult):
    name: str
    namespace: str
    strategy: str | None
    containers: list[Container]
    conditions: list[Condition]
    cut: dict[str, int] = Field(default_factory=dict)


class PodDetail(BoundedResult):
    name: str
    namespace: str
    containers: list[Container]
    conditions: list[Condition]
    cut: dict[str, int] = Field(default_factory=dict)
