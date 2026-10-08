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


def test_pod_listing_includes_init_container_restarts():
    obj = pod([container_status(restarts=1)])
    obj.status.init_container_statuses = [container_status("CrashLoopBackOff", restarts=3)]
    row = invoke(
        connector(list_namespaced_pod=lambda **kwargs: page([obj])), "k8s_list_pods"
    ).items[0]
    assert row.restarts == 4


@pytest.mark.parametrize("current", [False, True])
@pytest.mark.parametrize("previous", [False, True])
def test_describe_pod_includes_termination_and_specs_without_values(current, previous):
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
                    state=k.V1ContainerState(terminated=termination)
                    if current
                    else k.V1ContainerState(
                        waiting=k.V1ContainerStateWaiting(reason="CrashLoopBackOff")
                    ),
                    last_state=k.V1ContainerState(terminated=termination if previous else None),
                )
            ],
        ),
    )
    result = invoke(
        connector(read_namespaced_pod=lambda **kwargs: obj), "k8s_describe_pod", name="checkout"
    )
    row = result.containers[0]
    assert (row.name, row.image, row.state) == (
        "app",
        "app:1",
        "OOMKilled" if current else "CrashLoopBackOff",
    )
    assert row.ready is False
    assert row.restart_count == 2
    for term, exists in [(row.current_termination, current), (row.last_termination, previous)]:
        if exists:
            assert (term.reason, term.exit_code, term.finished_at) == ("OOMKilled", 137, NOW)
        else:
            assert term is None
    assert row.limits == {"memory": "128Mi"}
    assert row.env_names == ["TOKEN"]
    assert row.probes["readiness"].kind == "exec"
    assert result.conditions[0].status == "False"
    assert "do-not-leak" not in result.model_dump_json()
    assert "secret-command" not in result.model_dump_json()


@pytest.mark.parametrize("long_names", [False, True])
def test_container_field_caps_report_omitted_env_and_secret_names(long_names):
    spec = k.V1Container(
        name="app",
        image="app:1",
        env=[
            k.V1EnvVar(name=f"ENV_{index}" + ("x" * 4000 if long_names else ""), value="hidden")
            for index in range(60)
        ],
        resources=k.V1ResourceRequirements(limits={"memory": "128Mi"}),
        env_from=[
            k.V1EnvFromSource(
                secret_ref=k.V1SecretEnvSource(
                    name=f"secret-{index}" + ("x" * 4000 if long_names else "")
                )
            )
            for index in range(55)
        ],
    )
    obj = k.V1Pod(
        metadata=k.V1ObjectMeta(name="checkout", namespace="production"),
        spec=k.V1PodSpec(containers=[spec, k.V1Container(name="sidecar", image="sidecar:1")]),
        status=k.V1PodStatus(
            container_statuses=[
                k.V1ContainerStatus(
                    name="app",
                    image="app:1",
                    image_id="id",
                    ready=False,
                    restart_count=2,
                    state=k.V1ContainerState(
                        terminated=k.V1ContainerStateTerminated(
                            reason="Error", exit_code=1, finished_at=NOW
                        )
                    ),
                )
            ]
        ),
    )
    result = invoke(
        connector(read_namespaced_pod=lambda **kwargs: obj), "k8s_describe_pod", name="checkout"
    )
    row = result.containers[0]
    assert row.limits == {"memory": "128Mi"}
    assert row.current_termination.exit_code == 1
    assert row.current_termination.finished_at == NOW
    assert len(result.containers) == 2
    assert row.image == "app:1"
    assert row.name == "app"
    assert row.cut == {
        "env_names": 60 - len(row.env_names),
        "secret_names": 55 - len(row.secret_names),
    }
    assert len(result.model_dump_json()) <= 20000
    if not long_names:
        assert len(row.env_names) == 50
    assert "hidden" not in result.model_dump_json()
    narrow = invoke(
        connector(read_namespaced_pod=lambda **kwargs: obj),
        "k8s_describe_pod",
        name="checkout",
        container="app",
    )
    assert len(narrow.containers) == 1
    assert narrow.containers[0].name == "app"


@pytest.mark.parametrize("previous", [False, True])
@pytest.mark.parametrize("character", ["x", '"'])
def test_pod_logs_request_tail_and_previous_and_cap_text(previous, character):
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
        return character * 17000

    result = invoke(
        connector(read_namespaced_pod_log=logs),
        "k8s_pod_logs",
        name="checkout",
        container="app",
        tail_lines=10,
        previous=previous,
    )
    assert result.cut >= 1000
    if character == "x":
        assert result.cut == 1000
    assert result.text.endswith(f"[{result.cut} characters cut]")
    assert len(result.model_dump_json()) <= 20000


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


@pytest.mark.parametrize("remaining", [0, 5])
def test_list_services_counts_ready_endpoints_and_reports_ports(remaining, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("opensre.connectors.kubernetes.execution.monotonic", lambda: clock[0])
    service = k.V1Service(
        metadata=k.V1ObjectMeta(name="checkout"),
        spec=k.V1ServiceSpec(
            type="ClusterIP",
            selector={"app": "checkout"},
            ports=[k.V1ServicePort(port=80, target_port=8080)],
        ),
    )
    endpoints = k.V1Endpoints(
        metadata=k.V1ObjectMeta(name="checkout"),
        subsets=[
            k.V1EndpointSubset(
                addresses=[k.V1EndpointAddress(ip="10.0.0.1")],
                not_ready_addresses=[k.V1EndpointAddress(ip="10.0.0.2")],
            )
        ],
    )

    def services(**kwargs):
        assert kwargs["_request_timeout"] == 3
        clock[0] = 2.0
        return page(
            [service, k.V1Service(metadata=k.V1ObjectMeta(name="missing"), spec=k.V1ServiceSpec())]
        )

    def list_endpoints(**kwargs):
        assert kwargs["_request_timeout"] == 1
        assert kwargs["limit"] == 100
        return page([endpoints], remaining=remaining)

    result = invoke(
        connector(list_namespaced_service=services, list_namespaced_endpoints=list_endpoints),
        "k8s_list_services",
    )
    row = result.items[0]
    assert row.ready_endpoints == 1
    assert row.endpoints_complete is True
    assert result.items[1].ready_endpoints == (None if remaining else 0)
    assert result.items[1].endpoints_complete is (remaining == 0)
    assert row.selector == {"app": "checkout"}
    assert row.ports[0].port == 80
    assert row.ports[0].target_port == 8080


def test_service_timeout_stops_additional_requests(deadline):
    from opensre.connectors.kubernetes.reader import KubeReadError

    calls = []

    def services(**kwargs):
        calls.append("services")
        deadline()
        return page([])

    def endpoints(**kwargs):
        calls.append("endpoints")
        return page([])

    target = connector(list_namespaced_service=services, list_namespaced_endpoints=endpoints)
    with pytest.raises(KubeReadError) as error:
        invoke(target, "k8s_list_services")
    assert error.value.error.code == "timeout"
    deadline.release()
    deadline.finish()
    assert calls == ["services"]


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


def test_rollout_history_filters_by_owner_uid_and_sorts_revisions():
    deployment = k.V1Deployment(
        metadata=k.V1ObjectMeta(name="checkout", uid="deployment-1"),
        spec=k.V1DeploymentSpec(
            selector=k.V1LabelSelector(
                match_labels={"app": "checkout"},
                match_expressions=[
                    k.V1LabelSelectorRequirement(
                        key="track", operator="In", values=["stable", "canary"]
                    )
                ],
            ),
            template=k.V1PodTemplateSpec(),
        ),
    )

    def replica(revision, uid):
        return k.V1ReplicaSet(
            metadata=k.V1ObjectMeta(
                name=f"checkout-{revision}",
                annotations={
                    "deployment.kubernetes.io/revision": str(revision),
                    "kubernetes.io/change-cause": f"release {revision}",
                },
                owner_references=[
                    k.V1OwnerReference(
                        api_version="apps/v1",
                        kind="Deployment",
                        name="checkout",
                        uid=uid,
                        controller=True,
                    )
                ],
            ),
            spec=k.V1ReplicaSetSpec(
                selector=k.V1LabelSelector(),
                template=k.V1PodTemplateSpec(
                    spec=k.V1PodSpec(
                        containers=[k.V1Container(name="app", image=f"app:{revision}")]
                    )
                ),
            ),
        )

    def list_replicas(**kwargs):
        assert kwargs["label_selector"] == "app=checkout,track in (stable,canary)"
        return page([replica(2, "deployment-1"), replica(1, "deployment-1"), replica(3, "other")])

    result = invoke(
        connector(
            read_namespaced_deployment=lambda **kwargs: deployment,
            list_namespaced_replica_set=list_replicas,
        ),
        "k8s_rollout_history",
        name="checkout",
    )
    assert [row.revision for row in result.items] == [1, 2]
    assert result.items[1].images == ["app:2"]
    assert result.items[1].change_cause == "release 2"
