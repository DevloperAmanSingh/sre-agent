from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from fakes.kubernetes import NOW, connector, container_status, page, pod
from kubernetes import client as k


def run_rule(target, name):
    findings = next(check for check in target.checks() if check.name == name).run()
    for finding in findings:
        for evidence in finding.evidence:
            assert "datetime" not in evidence.detail
            assert "=None" not in evidence.detail
    return findings


@pytest.mark.parametrize("init", [False, True])
@pytest.mark.parametrize(
    "state,ready,previous_minutes,policy,expected",
    [
        ("waiting", False, 5, "Always", 1),
        ("terminated", False, 5, "Always", 1),
        ("running", False, 5, "Always", 0),
        ("running", True, 5, "Always", 0),
        ("terminated", False, None, "Always", 0),
        ("terminated", False, 61, "Always", 0),
        ("terminated", False, 5, "Never", 0),
    ],
)
def test_crashloop_detects_current_state_in_regular_and_init_containers(
    init, state, ready, previous_minutes, policy, expected
):
    previous = (
        NS(reason="Error", exit_code=1, finished_at=NOW - timedelta(minutes=previous_minutes))
        if previous_minutes is not None
        else None
    )
    status = container_status(
        "CrashLoopBackOff" if state == "waiting" else None, ready=ready, term=previous, restarts=99
    )
    status.state.terminated = (
        NS(reason="Error", exit_code=1, finished_at=NOW) if state == "terminated" else None
    )
    obj = pod([status])
    obj.spec.restart_policy = policy
    if init:
        obj.status.init_container_statuses = obj.status.container_statuses
        obj.status.container_statuses = []
    target = connector(list_namespaced_pod=lambda **kwargs: page([obj]))
    findings = run_rule(target, "crashloop")
    assert len(findings) == expected
    if not expected:
        return
    finding = findings[0]
    assert finding.severity == "critical"
    assert finding.resource == "pod/production/checkout"
    assert finding.reason == "CrashLoopBackOff"
    assert finding.evidence[0].source == "k8s_describe_pod"
    if state == "waiting":
        assert "Container app: waiting in CrashLoopBackOff" in finding.evidence[0].detail
    else:
        assert (
            finding.evidence[0].detail
            == f"Container app: Error exit=1 at {NOW}, 2 failed runs in the last hour"
        )
    assert run_rule(target, "restarts") == []


@pytest.mark.parametrize("minutes,expected", [(5, 1), (60, 1), (61, 0), (-1, 0), (None, 0)])
@pytest.mark.parametrize("current_oom,sidecar_oom", [(False, False), (True, False), (True, True)])
def test_recent_oom_is_detected_per_container_not_hidden_by_clean_sidecar(
    minutes, expected, current_oom, sidecar_oom
):
    timestamp = NOW - timedelta(minutes=minutes) if minutes is not None else None
    app = container_status(term=NS(reason="OOMKilled", exit_code=137, finished_at=timestamp))
    sidecar = container_status(term=NS(reason="Completed", exit_code=0, finished_at=NOW))
    sidecar.name = "sidecar"
    app.restart_count = 2
    if current_oom and timestamp:
        app.state.terminated = NS(
            reason="OOMKilled", exit_code=137, finished_at=timestamp + timedelta(seconds=1)
        )
        app.ready = False
    if sidecar_oom:
        sidecar.last_state = app.last_state
        sidecar.state = app.state
        sidecar.ready = app.ready
        sidecar.restart_count = 2
    target = connector(list_namespaced_pod=lambda **kwargs: page([pod([app, sidecar])]))
    findings = run_rule(target, "oom")
    assert len(findings) == expected * (2 if sidecar_oom else 1)
    if expected:
        assert findings[0].severity == "critical"
        assert findings[0].reason == "OOMKilled"
        assert "137" in findings[0].evidence[0].detail
        if current_oom:
            assert len(findings[0].evidence) == 2
            assert str(timestamp + timedelta(seconds=1)) in findings[0].evidence[0].detail
        assert run_rule(target, "crashloop") == []
        assert run_rule(target, "restarts") == []


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
    "uid,minutes,scheduled,expected",
    [
        ("pod-1", 5, None, "FailedScheduling"),
        ("old-pod", 5, None, "Pending"),
        ("pod-1", 61, None, "Pending"),
        ("pod-1", 5, "True", None),
        ("pod-1", 61, "False", "Unschedulable"),
    ],
)
def test_pending_includes_current_scheduling_event_reason(uid, minutes, scheduled, expected):
    obj = pod([container_status("ImagePullBackOff")], phase="Pending")
    if scheduled:
        obj.status.conditions = [
            NS(
                type="PodScheduled",
                status=scheduled,
                reason="Unschedulable",
                message="insufficient CPU; token=synthetic-secret",
            )
        ]
    target = connector(
        list_namespaced_pod=lambda **kwargs: page([obj]),
        list_namespaced_event=lambda **kwargs: page([event(uid=uid, minutes=minutes)]),
    )
    findings = run_rule(target, "pending")
    if expected is None:
        assert findings == []
        assert len(run_rule(target, "image-pull")) == 1
        return
    finding = findings[0]
    if expected == "Unschedulable":
        assert "insufficient CPU" in finding.evidence[0].detail
        assert "synthetic-secret" not in finding.model_dump_json()
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
        obj.status.conditions = [NS(type="Ready", status=condition, reason=None)]
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
        (1, 5, "Error", None, 1),
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


@pytest.mark.parametrize(
    "message,uid,minutes,expected",
    [
        ("Readiness probe failed: connection refused", "pod-1", 5, 1),
        ("Liveness probe failed: timeout", "pod-1", 5, 1),
        ("Startup probe failed: timeout", "pod-1", 5, 1),
        ("Readiness probe failed: token=synthetic-secret", "pod-1", 5, 1),
        ("Readiness probe failed", "old-pod", 5, 0),
        ("Readiness probe failed", "pod-1", 61, 0),
        ("Failed mount", "pod-1", 5, 0),
    ],
)
def test_probe_failures_only_use_recent_events_for_current_pods(message, uid, minutes, expected):
    target = connector(
        list_namespaced_pod=lambda **kwargs: page([pod()]),
        list_namespaced_event=lambda **kwargs: page(
            [event(reason="Unhealthy", message=message, uid=uid, minutes=minutes)]
        ),
    )
    findings = run_rule(target, "probes")
    assert len(findings) == expected
    if expected:
        assert findings[0].severity == "warning"
        assert findings[0].reason == "ProbeFailure"
        assert findings[0].evidence[0].source == "k8s_list_events"
        from opensre.connectors.kubernetes.redaction import redact

        assert redact(message) in findings[0].evidence[0].detail
        assert "synthetic-secret" not in findings[0].model_dump_json()
