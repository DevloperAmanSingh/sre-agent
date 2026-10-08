from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Literal

from kubernetes.client.exceptions import ApiException  # pyright: ignore[reportMissingTypeStubs]
from langchain_core.tools import ToolException
from pydantic import BaseModel

from opensre.config import KubeSettings
from opensre.connectors.kubernetes.execution import KubeDiagnostics, run_bounded


class ReadError(BaseModel):
    code: Literal["forbidden", "not_found", "unreachable", "timeout"]
    detail: str


class KubeReadError(ToolException):
    def __init__(self, error: ReadError) -> None:
        self.error = error
        super().__init__(error.model_dump_json())


class Page[T](BaseModel):
    items: list[T]
    cut: int | None
    more_available: bool
    truncation: str


def bounded_page[T](page: Any, limit: int, convert: Callable[[Any], T]) -> Page[T]:
    items = [convert(item) for item in page.items[:limit]]
    metadata = page.metadata
    remaining: int | None = getattr(metadata, "remaining_item_count", None)
    more = bool(getattr(metadata, "_continue", ""))
    dropped = max(0, len(page.items) - limit)
    cut = None if more and remaining is None else (remaining or 0) + dropped
    message = (
        f"showing {len(items)}; more available"
        if cut is None
        else f"showing {len(items)} of {len(items) + cut}"
    )
    return Page(items=items, cut=cut, more_available=more or dropped > 0, truncation=message)


class KubeReader:
    def __init__(
        self,
        settings: KubeSettings,
        client_factory: Callable[[KubeSettings], AbstractContextManager[Any]],
    ) -> None:
        self.settings = settings
        self.client_factory = client_factory

    def read[T](self, operation: Callable[[Any], T]) -> T:
        def execute(diagnostics: KubeDiagnostics) -> T:
            with self.client_factory(self.settings) as api:
                diagnostics.check_credentials()
                return operation(api)

        try:
            return run_bounded(execute, self.settings.request_timeout_s)
        except Exception as exc:
            cause = exc
            while cause.__cause__ is not None:
                cause = cause.__cause__
            if isinstance(cause, ApiException):
                code = {403: "forbidden", 404: "not_found", 408: "timeout", 504: "timeout"}.get(
                    getattr(cause, "status", 0), "unreachable"
                )
                error = ReadError.model_validate(
                    {"code": code, "detail": f"Kubernetes read {code}"}
                )
            elif isinstance(cause, TimeoutError):
                error = ReadError(
                    code="timeout", detail=f"timed out after {self.settings.request_timeout_s:g}s"
                )
            else:
                error = ReadError(
                    code="unreachable", detail="Kubernetes API or credentials unavailable"
                )
            raise KubeReadError(error) from exc
