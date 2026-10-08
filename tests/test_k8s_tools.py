from datetime import timedelta

from fakes.kubernetes import NOW, connector, container_status, invoke, page, pod


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
