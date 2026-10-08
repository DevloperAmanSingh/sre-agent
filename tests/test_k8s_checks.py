from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from fakes.kubernetes import NOW, connector, container_status, page, pod


def run_rule(target, name):
    return next(check for check in target.checks() if check.name == name).run()


@pytest.mark.parametrize("init", [False, True])
def test_crashloop_detects_current_state_in_regular_and_init_containers(init):
    obj = pod([container_status("CrashLoopBackOff", ready=False)])
    if init:
        obj.status.init_container_statuses = obj.status.container_statuses
        obj.status.container_statuses = []
    target = connector(list_namespaced_pod=lambda **kwargs: page([obj]))
    findings = run_rule(target, "crashloop")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "critical"
    assert finding.resource == "pod/production/checkout"
    assert finding.reason == "CrashLoopBackOff"
    assert finding.evidence[0].source == "k8s_describe_pod"
    assert "app" in finding.evidence[0].detail


@pytest.mark.parametrize("minutes,expected", [(5, 1), (60, 1), (61, 0), (-1, 0), (None, 0)])
def test_recent_oom_is_detected_per_container_not_hidden_by_clean_sidecar(minutes, expected):
    timestamp = NOW - timedelta(minutes=minutes) if minutes is not None else None
    app = container_status(term=NS(reason="OOMKilled", exit_code=137, finished_at=timestamp))
    sidecar = container_status(term=NS(reason="Completed", exit_code=0, finished_at=NOW))
    sidecar.name = "sidecar"
    findings = run_rule(
        connector(list_namespaced_pod=lambda **kwargs: page([pod([app, sidecar])])), "oom"
    )
    assert len(findings) == expected
    if expected:
        assert findings[0].severity == "critical"
        assert findings[0].reason == "OOMKilled"
        assert "137" in findings[0].evidence[0].detail


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("ImagePullBackOff", 1),
        ("ErrImagePull", 1),
        ("InvalidImageName", 1),
        ("ErrImageNeverPull", 1),
        (None, 0),
    ],
)
def test_image_pull_failures_are_critical(reason, expected):
    findings = run_rule(
        connector(list_namespaced_pod=lambda **kwargs: page([pod([container_status(reason)])])),
        "image-pull",
    )
    assert len(findings) == expected
    if expected:
        assert findings[0].severity == "critical"
        assert findings[0].reason == reason
