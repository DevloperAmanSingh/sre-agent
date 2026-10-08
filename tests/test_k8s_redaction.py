import pytest
from fakes.kubernetes import connector, invoke, page
from kubernetes import client as k


@pytest.mark.parametrize(
    "text",
    [
        "DATABASE_PASSWORD=synthetic-secret",
        "password: synthetic-secret",
        '"api_key": "synthetic-secret"',
        "TOKEN='synthetic-secret'",
        "Bearer synthetic-secret",
        "https://user:synthetic-secret@example.test/path",
        "auth=synthetic-secret",
        "credential: synthetic-secret",
        "key=synthetic-secret",
    ],
)
def test_sensitive_text_is_redacted_before_artifact_and_content(text):
    from opensre.connectors.kubernetes.redaction import redact

    assert "synthetic-secret" not in redact(text)
    assert "[REDACTED]" in redact(text)


def test_successful_log_change_cause_and_event_message_are_sanitized():
    from contextlib import nullcontext
    from types import SimpleNamespace as NS

    from opensre.config import KubeSettings
    from opensre.connectors.kubernetes.reader import KubeReader
    from opensre.connectors.kubernetes.workloads import workload_tools

    text = (
        "DATABASE_PASSWORD=synthetic-secret Bearer other-sensitive https://user:pass@example.test"
    )
    target = connector(
        read_namespaced_pod_log=lambda **kwargs: text,
        list_namespaced_event=lambda **kwargs: page(
            [
                k.CoreV1Event(
                    metadata=k.V1ObjectMeta(name="event"),
                    involved_object=k.V1ObjectReference(kind="Pod", name="checkout"),
                    type="Warning",
                    reason="Unhealthy",
                    message=text,
                )
            ]
        ),
    )
    assert "synthetic-secret" not in invoke(target, "k8s_pod_logs", name="checkout").text
    assert "synthetic-secret" not in invoke(target, "k8s_list_events").items[0].message
    replica = NS(
        metadata=NS(
            annotations={
                "deployment.kubernetes.io/revision": "1",
                "kubernetes.io/change-cause": text,
            },
            owner_references=[NS(uid="dep", kind="Deployment", controller=True)],
        ),
        spec=NS(template=NS(spec=NS(containers=[]))),
    )
    api = NS(
        read_namespaced_deployment=lambda **kwargs: NS(
            metadata=NS(uid="dep"), spec=NS(selector=NS(match_labels={}, match_expressions=[]))
        ),
        list_namespaced_replica_set=lambda **kwargs: page([replica]),
    )
    tool = next(
        tool
        for tool in workload_tools(
            KubeReader(KubeSettings(), lambda settings: nullcontext(object())), lambda client: api
        )
        if tool.name == "k8s_rollout_history"
    )
    response = tool.invoke(
        {"name": tool.name, "args": {"name": "checkout"}, "id": "read", "type": "tool_call"}
    )
    assert response.content == response.artifact.model_dump_json()
    for secret in ("synthetic-secret", "other-sensitive", "user:pass"):
        assert secret not in response.content
