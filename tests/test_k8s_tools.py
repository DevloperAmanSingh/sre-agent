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
        assert row.age_s == 3600
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


def test_list_deployments_reports_replica_counts():
    deployment = k.V1Deployment(
        metadata=k.V1ObjectMeta(name="checkout"),
        spec=k.V1DeploymentSpec(
            replicas=3, selector=k.V1LabelSelector(), template=k.V1PodTemplateSpec()
        ),
        status=k.V1DeploymentStatus(ready_replicas=1, available_replicas=1, updated_replicas=2),
    )
    result = invoke(
        connector(list_namespaced_deployment=lambda **kwargs: page([deployment])),
        "k8s_list_deployments",
    )
    assert result.items[0].model_dump() == {
        "name": "checkout",
        "desired": 3,
        "ready": 1,
        "available": 1,
        "updated": 2,
    }


def test_describe_deployment_shows_strategy_conditions_and_only_env_secret_names():
    obj = k.V1Deployment(
        metadata=k.V1ObjectMeta(name="checkout", namespace="production"),
        spec=k.V1DeploymentSpec(
            selector=k.V1LabelSelector(),
            strategy=k.V1DeploymentStrategy(type="RollingUpdate"),
            template=k.V1PodTemplateSpec(
                spec=k.V1PodSpec(
                    containers=[
                        k.V1Container(
                            name="app",
                            image="app:2",
                            env=[
                                k.V1EnvVar(name="TOKEN", value="secret-value"),
                                k.V1EnvVar(
                                    name="PASSWORD",
                                    value_from=k.V1EnvVarSource(
                                        secret_key_ref=k.V1SecretKeySelector(
                                            name="credentials", key="password"
                                        )
                                    ),
                                ),
                            ],
                            env_from=[
                                k.V1EnvFromSource(
                                    secret_ref=k.V1SecretEnvSource(name="environment")
                                )
                            ],
                        )
                    ]
                )
            ),
        ),
        status=k.V1DeploymentStatus(
            conditions=[
                k.V1DeploymentCondition(
                    type="Available", status="False", reason="MinimumReplicasUnavailable"
                )
            ]
        ),
    )
    result = invoke(
        connector(read_namespaced_deployment=lambda **kwargs: obj),
        "k8s_describe_deployment",
        name="checkout",
    )
    assert result.strategy == "RollingUpdate"
    assert result.containers[0].env_names == ["TOKEN", "PASSWORD"]
    assert result.containers[0].secret_names == ["credentials", "environment"]
    assert result.conditions[0].reason == "MinimumReplicasUnavailable"
    assert "secret-value" not in result.model_dump_json()


def test_list_services_counts_ready_endpoints_and_reports_ports():
    service = k.V1Service(
        metadata=k.V1ObjectMeta(name="checkout"),
        spec=k.V1ServiceSpec(
            type="ClusterIP",
            selector={"app": "checkout"},
            ports=[k.V1ServicePort(port=80, target_port=8080)],
        ),
    )
    endpoints = k.V1Endpoints(
        subsets=[
            k.V1EndpointSubset(
                addresses=[k.V1EndpointAddress(ip="10.0.0.1")],
                not_ready_addresses=[k.V1EndpointAddress(ip="10.0.0.2")],
            )
        ]
    )
    result = invoke(
        connector(
            list_namespaced_service=lambda **kwargs: page([service]),
            read_namespaced_endpoints=lambda **kwargs: endpoints,
        ),
        "k8s_list_services",
    )
    row = result.items[0]
    assert row.ready_endpoints == 1
    assert row.selector == {"app": "checkout"}
    assert row.ports[0].port == 80
    assert row.ports[0].target_port == 8080


def test_list_nodes_reports_readiness_pressure_resources_and_version():
    obj = k.V1Node(
        metadata=k.V1ObjectMeta(name="worker"),
        status=k.V1NodeStatus(
            conditions=[
                k.V1NodeCondition(type="Ready", status="True"),
                k.V1NodeCondition(type="MemoryPressure", status="True"),
            ],
            capacity={"cpu": "4"},
            allocatable={"cpu": "3"},
            node_info=k.V1NodeSystemInfo(
                architecture="arm64",
                boot_id="boot",
                container_runtime_version="containerd",
                kernel_version="kernel",
                kube_proxy_version="proxy",
                kubelet_version="v1.35.0",
                machine_id="machine",
                operating_system="linux",
                os_image="linux",
                system_uuid="uuid",
            ),
        ),
    )
    row = invoke(connector(list_node=lambda **kwargs: page([obj])), "k8s_list_nodes").items[0]
    assert row.ready is True
    assert row.pressure == {"MemoryPressure": "True"}
    assert row.capacity == {"cpu": "4"}
    assert row.allocatable == {"cpu": "3"}
    assert row.version == "v1.35.0"
