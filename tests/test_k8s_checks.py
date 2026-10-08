from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from fakes.kubernetes import NOW, connector, container_status, page, pod
from kubernetes import client as k


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


def event(
    reason="FailedScheduling", uid="pod-1", minutes=5, message="0/2 nodes: insufficient memory"
):
    return k.CoreV1Event(
        metadata=k.V1ObjectMeta(name="event", namespace="production"),
        involved_object=k.V1ObjectReference(kind="Pod", name="checkout", uid=uid),
        type="Warning",
        reason=reason,
        message=message,
        last_timestamp=NOW - timedelta(minutes=minutes),
    )


@pytest.mark.parametrize(
    "uid,minutes,expected",
    [("pod-1", 5, "FailedScheduling"), ("old-pod", 5, "Pending"), ("pod-1", 61, "Pending")],
)
def test_pending_includes_current_scheduling_event_reason(uid, minutes, expected):
    target = connector(
        list_namespaced_pod=lambda **kwargs: page([pod(phase="Pending")]),
        list_namespaced_event=lambda **kwargs: page([event(uid=uid, minutes=minutes)]),
    )
    finding = run_rule(target, "pending")[0]
    assert finding.severity == "warning"
    assert finding.reason == expected
    if expected == "FailedScheduling":
        assert "insufficient memory" in finding.evidence[0].detail
        assert finding.evidence[0].source == "k8s_list_events"


@pytest.mark.parametrize(
    "minutes,ready,phase,condition,expected",
    [
        (5, False, "Running", None, 0),
        (10, False, "Running", None, 0),
        (11, False, "Running", None, 1),
        (11, True, "Running", None, 0),
        (11, True, "Running", "False", 1),
        (11, False, "Succeeded", None, 0),
    ],
)
def test_running_not_ready_observes_startup_grace_and_pod_readiness(
    minutes, ready, phase, condition, expected
):
    obj = pod(
        [container_status(ready=ready)], phase=phase, created=NOW - timedelta(minutes=minutes)
    )
    if condition:
        obj.status.conditions = [NS(type="Ready", status=condition)]
    findings = run_rule(connector(list_namespaced_pod=lambda **kwargs: page([obj])), "not-ready")
    assert len(findings) == expected
    if expected:
        assert findings[0].severity == "warning"
        assert findings[0].reason == "NotReady"


@pytest.mark.parametrize(
    "count,minutes,reason,exit_code,expected",
    [
        (100, None, None, 0, 0),
        (100, 61, "Error", 1, 0),
        (1, 5, "Completed", 0, 0),
        (1, 5, "Error", 1, 1),
        (0, 5, "Error", 1, 0),
    ],
)
def test_restart_warning_requires_recent_failed_termination_not_lifetime_count(
    count, minutes, reason, exit_code, expected
):
    term = (
        NS(reason=reason, exit_code=exit_code, finished_at=NOW - timedelta(minutes=minutes))
        if minutes is not None
        else None
    )
    findings = run_rule(
        connector(
            list_namespaced_pod=lambda **kwargs: page(
                [pod([container_status(term=term, restarts=count)])]
            )
        ),
        "restarts",
    )
    assert len(findings) == expected
    if expected:
        assert findings[0].severity == "warning"
        assert findings[0].reason == "RecentRestart"
        assert "Error" in findings[0].evidence[0].detail
