from threading import Event, Thread

import pytest


@pytest.fixture
def deadline(monkeypatch):
    from opensre.connectors.kubernetes import execution

    entered = Event()
    release = Event()
    workers = []

    class DeadlineThread(Thread):
        def join(self, timeout=None):
            assert self.daemon
            assert timeout == 3
            entered.wait()

    def pause():
        entered.set()
        release.wait()

    def start_worker(**kwargs):
        worker = DeadlineThread(**kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(execution, "Thread", start_worker)
    try:
        yield pause
    finally:
        release.set()
        for worker in workers:
            Thread.join(worker)
            assert not worker.is_alive()
