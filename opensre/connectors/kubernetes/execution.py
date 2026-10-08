import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager
from threading import Thread, get_ident

from opensre.output import cap_text


class KubeDiagnostics(logging.Handler):
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

    def check_credentials(self) -> None:
        if self.has_error:
            raise ValueError("Kubernetes credential loading failed")

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


def run_bounded[T](operation: Callable[[KubeDiagnostics], T], timeout_s: float) -> T:
    results: list[T] = []
    errors: list[Exception] = []
    diagnostics = KubeDiagnostics()

    def run() -> None:
        with diagnostics.capture():
            try:
                result = operation(diagnostics)
                if diagnostics.has_error:
                    raise ValueError("Kubernetes operation emitted an error")
                results.append(result)
            except Exception as exc:
                errors.append(exc)

    worker = Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise TimeoutError(
            cap_text(f"timed out after {timeout_s:g}s\n{diagnostics.summary()}".strip(), 2000)
        )
    if errors:
        detail = "\n".join(part for part in (diagnostics.summary(), str(errors[0])) if part)
        raise RuntimeError(cap_text(detail, 2000)) from errors[0]
    return results[0]
