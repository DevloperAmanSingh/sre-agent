from collections.abc import Callable
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from opensre.output import cap_text


class CheckResult(BaseModel):
    name: str
    ok: bool
    detail: str
    latency_s: float | None = None

    @field_validator("detail")
    @classmethod
    def cap_detail(cls, value: str) -> str:
        return cap_text(value, 2000)


class Severity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class Evidence(BaseModel):
    source: str
    detail: str


class Finding(BaseModel):
    summary: str
    severity: Severity
    evidence: list[Evidence]


class QuickCheck(BaseModel):
    name: str
    run: Callable[[], list[Finding]]


class Diagnosis(BaseModel):
    summary: str
    cause: str
    evidence: list[Evidence]
    suggested_fix: str
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
