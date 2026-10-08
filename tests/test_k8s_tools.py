from datetime import timedelta

import pytest
from fakes.kubernetes import NOW, connector, container_status, invoke, page, pod
from kubernetes import client as k


def test_list_pods_returns_health_summary_and_uses_default_or_override_namespace():
    def list_pods(**kwargs):
        assert kwargs["namespace"] in ("production", "other")
        assert kwargs["_request_timeout"] == 3
        return page(
            [pod([container_status(ready=False, restarts=4)], created=NOW - timedelta(hours=1))],
            remaining=2,
        )

    target = connector(list_namespaced_pod=list_pods)
    for args in ({}, {"namespace": "other"}):
        result = invoke(target, "k8s_list_pods", **args)
        row = result.items[0]
        assert (row.name, row.phase, row.ready, row.restarts, row.node) == (
            "checkout",
            "Running",
            "0/1",
            4,
            "worker",
        )
        assert row.age_s >= 3600
        assert result.truncation == "showing 1 of 3"


def test_describe_pod_includes_termination_and_specs_without_values():
    termination = k.V1ContainerStateTerminated(exit_code=137, reason="OOMKilled", finished_at=NOW)
    obj = k.V1Pod(
        metadata=k.V1ObjectMeta(name="checkout", namespace="production"),
        spec=k.V1PodSpec(
            containers=[
                k.V1Container(
                    name="app",
                    image="app:1",
                    env=[k.V1EnvVar(name="TOKEN", value="do-not-leak")],
                    resources=k.V1ResourceRequirements(limits={"memory": "128Mi"}),
                    readiness_probe=k.V1Probe(exec=k.V1ExecAction(command=["secret-command"])),
                )
            ]
        ),
        status=k.V1PodStatus(
            conditions=[k.V1PodCondition(type="Ready", status="False")],
            container_statuses=[
                k.V1ContainerStatus(
                    name="app",
                    image="app:1",
                    image_id="id",
                    ready=False,
                    restart_count=2,
                    state=k.V1ContainerState(
                        waiting=k.V1ContainerStateWaiting(reason="CrashLoopBackOff")
                    ),
                    last_state=k.V1ContainerState(terminated=termination),
                )
            ],
        ),
    )
    result = invoke(
        connector(read_namespaced_pod=lambda **kwargs: obj), "k8s_describe_pod", name="checkout"
    )
    row = result.containers[0]
    assert (
        row.name,
        row.image,
        row.state,
        row.last_termination.reason,
        row.last_termination.exit_code,
    ) == ("app", "app:1", "CrashLoopBackOff", "OOMKilled", 137)
    assert row.last_termination.finished_at == NOW
    assert row.limits == {"memory": "128Mi"}
    assert row.env_names == ["TOKEN"]
    assert row.probes["readiness"].kind == "exec"
    assert result.conditions[0].status == "False"
    assert "do-not-leak" not in result.model_dump_json()
    assert "secret-command" not in result.model_dump_json()


@pytest.mark.parametrize("previous", [False, True])
def test_pod_logs_request_tail_and_previous_and_cap_text(previous):
    def logs(**kwargs):
        assert kwargs == {
            "name": "checkout",
            "namespace": "production",
            "container": "app",
            "tail_lines": 10,
            "previous": previous,
            "limit_bytes": 16000,
            "_request_timeout": 3,
        }
        return "x" * 17000

    result = invoke(
        connector(read_namespaced_pod_log=logs),
        "k8s_pod_logs",
        name="checkout",
        container="app",
        tail_lines=10,
        previous=previous,
    )
    assert result.cut == 1000
    assert result.text.endswith("[1000 characters cut]")
    assert len(result.text) < 16100


def test_events_show_warnings_first_with_timestamps_and_counts():
    events = [
        k.CoreV1Event(
            metadata=k.V1ObjectMeta(name="event"),
            involved_object=k.V1ObjectReference(kind="Pod", name="checkout"),
            reason=reason,
            type=kind,
            count=3,
            last_timestamp=NOW,
        )
        for reason, kind in [("Started", "Normal"), ("Unhealthy", "Warning")]
    ]
    result = invoke(
        connector(list_namespaced_event=lambda **kwargs: page(events)), "k8s_list_events"
    )
    assert [item.reason for item in result.items] == ["Unhealthy", "Started"]
    assert result.items[0].object == "Pod/checkout"
    assert result.items[0].count == 3
    assert result.items[0].last_seen == NOW
