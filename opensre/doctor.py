import logging
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from threading import Thread, get_ident
from time import perf_counter
from typing import Any, cast

from kubernetes import client  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel, computed_field

from opensre.config import KubeSettings, LLMSettings
from opensre.connectors.kubernetes.client import create_client
from opensre.domain import CheckResult
from opensre.llm import configured_models, litellm, validate_keys, validate_model


class DoctorReport(BaseModel):
    checks: list[CheckResult]

    @computed_field
    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


class _KubeDiagnostics(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.thread_id: int | None = None
        self.text = ""
        self.cut = 0
        self.has_error = False

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self.thread_id:
            return
        message = f"{record.name}: {record.getMessage()}\n"
        remaining = 1000 - len(self.text)
        self.text += message[:remaining]
        self.cut += max(0, len(message) - remaining)
        self.has_error |= record.levelno >= logging.ERROR

    def intercept(self, record: logging.LogRecord) -> bool:
        if record.thread != self.thread_id:
            return True
        self.handle(record)
        return False

    def summary(self) -> str:
        self.acquire()
        try:
            suffix = f"… [{self.cut} characters cut]" if self.cut else ""
            return self.text.rstrip() + suffix
        finally:
            self.release()

    @contextmanager
    def capture(self) -> Generator[None]:
        self.thread_id = get_ident()
        names = {"", "kubernetes", "urllib3", "urllib3.connectionpool"}
        names.update(
            name
            for name in list(logging.Logger.manager.loggerDict)
            if name.startswith(("kubernetes.", "urllib3."))
        )
        loggers = [logging.getLogger(name) for name in names]
        for logger in loggers:
            logger.addHandler(self)
            logger.addFilter(self.intercept)
        try:
            yield
        finally:
            for logger in loggers:
                logger.removeFilter(self.intercept)
                logger.removeHandler(self)
            self.close()


def check_kube(
    settings: KubeSettings,
    *,
    client_factory: Callable[[KubeSettings], AbstractContextManager[Any]] = create_client,
    version_factory: Callable[[Any], Any] = client.VersionApi,
) -> CheckResult:
    results: list[CheckResult] = []
    diagnostics = _KubeDiagnostics()

    def run() -> None:
        with diagnostics.capture():
            try:
                with client_factory(settings) as api_client:
                    if diagnostics.has_error:
                        raise ValueError("Kubernetes credential loading failed")
                    version = version_factory(api_client).get_code(
                        _request_timeout=settings.request_timeout_s
                    )
                if diagnostics.has_error:
                    raise ValueError("Kubernetes check emitted an error")
                if not isinstance(version.git_version, str):
                    raise ValueError("Version endpoint returned no server version")
                result = CheckResult(name="kubernetes", ok=True, detail=version.git_version)
            except Exception as exc:
                detail = "\n".join(part for part in (diagnostics.summary(), str(exc)) if part)
                result = CheckResult(name="kubernetes", ok=False, detail=detail)
        results.append(result)

    worker = Thread(target=run, daemon=True)
    worker.start()
    worker.join(settings.request_timeout_s)
    if worker.is_alive():
        return CheckResult(
            name="kubernetes",
            ok=False,
            detail="\n".join(
                part
                for part in (
                    f"timed out after {settings.request_timeout_s:g}s",
                    diagnostics.summary(),
                )
                if part
            ),
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
