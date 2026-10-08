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


@pytest.mark.parametrize("warnings_only", [True, False])
@pytest.mark.parametrize("object_name", [None, "checkout"])
def test_events_show_warnings_first_with_timestamps_and_counts(warnings_only, object_name):
    def event(reason, kind, name, timestamp=NOW):
        return k.CoreV1Event(
            metadata=k.V1ObjectMeta(name="event"),
            involved_object=k.V1ObjectReference(kind="Pod", name=name),
            reason=reason,
            type=kind,
            count=3,
            last_timestamp=timestamp,
        )

    events = [event("Started", "Normal", "checkout") for _ in range(120)] + [
        event("Other", "Warning", "other", NOW - timedelta(minutes=1)),
        event("Unhealthy", "Warning", "checkout"),
    ]

    def list_events(**kwargs):
        selected = events
        selector = kwargs["field_selector"]
        if "type=Warning" in selector:
            selected = [event for event in selected if event.type == "Warning"]
        if "involvedObject.name=checkout" in selector:
            selected = [event for event in selected if event.involved_object.name == "checkout"]
        limit = kwargs["limit"]
        return page(selected[:limit], remaining=max(0, len(selected) - limit))

    args = {"limit": 2, "object_name": object_name}
    if not warnings_only:
        args["warnings_only"] = False
    result = invoke(connector(list_namespaced_event=list_events), "k8s_list_events", **args)
    expected = (
        (["Unhealthy"] if object_name else ["Unhealthy", "Other"])
        if warnings_only
        else ["Started", "Started"]
    )
    assert [item.reason for item in result.items] == expected
    assert result.items[0].object == "Pod/checkout"
    assert result.items[0].count == 3
    assert result.items[0].last_seen == NOW
    if warnings_only:
        assert result.more_available is False
    else:
        assert result.cut >= 119


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
