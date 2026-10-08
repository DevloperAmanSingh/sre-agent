from collections.abc import Callable
from contextlib import AbstractContextManager
from threading import Thread
from time import perf_counter
from typing import Any, cast

from kubernetes import client  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel, computed_field, field_validator

from opensre.config import KubeSettings, LLMSettings
from opensre.connectors.k8s.client import create_client
from opensre.llm import configured_models, litellm, validate_keys


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


class DoctorReport(BaseModel):
    checks: list[CheckResult]

    @computed_field
    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def check_kube(
    settings: KubeSettings,
    *,
    client_factory: Callable[[KubeSettings], AbstractContextManager[Any]] = create_client,
    version_factory: Callable[[Any], Any] = client.VersionApi,
) -> CheckResult:
    results: list[CheckResult] = []

    def run() -> None:
        try:
            with client_factory(settings) as api_client:
                version = version_factory(api_client).get_code(
                    _request_timeout=settings.request_timeout_s
                )
            if not isinstance(version.git_version, str):
                raise ValueError("Version endpoint returned no server version")
            result = CheckResult(name="kubernetes", ok=True, detail=version.git_version)
        except Exception as exc:
            result = CheckResult(name="kubernetes", ok=False, detail=str(exc))
        results.append(result)

    worker = Thread(target=run, daemon=True)
    worker.start()
    worker.join(settings.request_timeout_s)
    if worker.is_alive():
        return CheckResult(
            name="kubernetes",
            ok=False,
            detail=f"timed out after {settings.request_timeout_s:g}s",
        )
    return results[0]


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
    for model in configured_models(settings):
        started: float | None = None
        try:
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
