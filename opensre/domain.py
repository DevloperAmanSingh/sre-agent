from collections.abc import Callable
from enum import StrEnum

from pydantic import BaseModel, field_validator


class CheckResult(BaseModel):
    name: str
    ok: bool
    detail: str
    latency_s: float | None = None

    @field_validator("detail")
    @classmethod
    def cap_detail(cls, value: str) -> str:
        limit = 2000
        if len(value) > limit:
            return f"{value[:limit]}… [{len(value) - limit} characters cut]"
        return value


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
