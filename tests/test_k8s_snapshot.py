from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest
from fakes.kubernetes import NOW, container_status, page, pod

from opensre.config import KubeSettings
from opensre.connectors.kubernetes.connector import KubernetesConnector
from opensre.scan import run_checks


@pytest.mark.parametrize("count,expire", [(0, False), (1000, False), (1000, True)])
def test_scan_uses_one_paginated_snapshot_and_reports_sorted_omissions(count, expire, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("opensre.connectors.kubernetes.execution.monotonic", lambda: clock[0])
    clients = []
    requests = []

    @contextmanager
    def credentials(settings):
        if clients:
            raise RuntimeError("Credentials must not be rebuilt for cached observations")
        clients.append(1)
        yield object()

    objects = []
    for index in range(count):
        obj = pod([container_status("CrashLoopBackOff", ready=False)])
        obj.metadata.name = f"pod-{index:04}"
        obj.metadata.uid = f"uid-{index}"
        objects.append(obj)

    def pods(**kwargs):
        requests.append("pods")
        assert kwargs["limit"] == 100
        start = int(kwargs.get("_continue", 0))
        result = page(objects[start : start + 100])
        result.metadata._continue = str(start + 100) if start + 100 < count else ""
        if expire:
            clock[0] = 4.0
        return result

    def events(**kwargs):
        requests.append("events")
        return page([])

    target = KubernetesConnector(
        KubeSettings(namespace="production", request_timeout_s=3),
        client_factory=credentials,
        core_factory=lambda client: NS(list_namespaced_pod=pods, list_namespaced_event=events),
        now=lambda: NOW,
    )
    report = run_checks([target])
    assert len(clients) == 1
    if expire:
        assert report.errors and "timeout" in report.errors[0].detail
        assert report.findings == [] and report.ok is False
        assert requests == ["pods"]
    else:
        assert report.errors == []
        assert report.omitted == max(0, count - 50)
        assert len(report.findings) == min(count, 50)
        assert [finding.resource for finding in report.findings] == [
            f"pod/production/pod-{index:04}" for index in range(min(count, 50))
        ]
        assert len(report.model_dump_json()) < 30000
        assert report.ok is (count == 0)
        assert requests.count("pods") == max(1, count // 100)
        assert requests.count("events") == 1
