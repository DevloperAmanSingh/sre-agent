import pytest
from fakes.kubernetes import connector, invoke, page
from kubernetes import client as k


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
