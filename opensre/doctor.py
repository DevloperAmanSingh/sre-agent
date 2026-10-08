from collections.abc import Callable, Sequence
from time import perf_counter
from typing import Any, cast

from pydantic import BaseModel, computed_field

from opensre.config import LLMSettings
from opensre.connectors.base import Connector
from opensre.domain import CheckResult
from opensre.llm import configured_models, litellm, validate_keys, validate_model


class DoctorReport(BaseModel):
    checks: list[CheckResult]

    @computed_field
    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def diagnose_setup(
    connectors: Sequence[Connector], settings: LLMSettings, *, live: bool = False
) -> DoctorReport:
    return DoctorReport(
        checks=[*(connector.health() for connector in connectors), *check_llm(settings, live=live)]
    )


def check_llm(
    settings: LLMSettings,
    *,
    live: bool = False,
    key_validator: Callable[[str], None] = validate_keys,
    completion: Callable[..., Any] | None = None,
    clock: Callable[[], float] = perf_counter,
) -> list[CheckResult]:
    complete = completion or cast(Callable[..., Any], getattr(litellm, "completion"))
    results: list[CheckResult] = []
    for field, model in configured_models(settings):
        started: float | None = None
        try:
            validate_model(model, field)
            key_validator(model)
            if live:
                started = clock()
                complete(
                    model=model,
                    messages=[{"role": "user", "content": "Reply OK."}],
                    max_tokens=8,
                    timeout=settings.timeout_s,
                )
            result = CheckResult(
                name=model, ok=True, detail="Live request succeeded" if live else "Key present"
            )
        except Exception as exc:
            result = CheckResult(name=model, ok=False, detail=str(exc))
        if started is not None:
            result.latency_s = clock() - started
        results.append(result)
    return results
