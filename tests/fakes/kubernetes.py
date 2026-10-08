from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace as NS

from opensre.config import KubeSettings
from opensre.connectors.kubernetes.connector import KubernetesConnector

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def page(items, remaining=0):
    return NS(
        items=items,
        metadata=NS(_continue="next" if remaining else "", remaining_item_count=remaining),
    )


def connector(**methods):
    api = NS(**methods)
    return KubernetesConnector(
        KubeSettings(namespace="production", request_timeout_s=3),
        client_factory=lambda settings: nullcontext(object()),
        core_factory=lambda client: api,
        apps_factory=lambda client: api,
        now=lambda: NOW,
    )


def invoke(connector, tool_name, **args):
    tool = next(tool for tool in connector.tools() if tool.name == tool_name)
    assert tool.metadata == {"read_only": True}
    result = tool.invoke({"name": tool_name, "args": args, "id": "read", "type": "tool_call"})
    assert result.content == result.artifact.model_dump_json()
    return result.artifact


def pod(statuses=(), phase="Running", created=NOW):
    return NS(
        metadata=NS(
            name="checkout", namespace="production", creation_timestamp=created, uid="pod-1"
        ),
        spec=NS(node_name="worker", containers=[]),
        status=NS(
            phase=phase,
            container_statuses=list(statuses),
            init_container_statuses=[],
            conditions=[],
        ),
    )


def container_status(reason=None, ready=True, term=None, restarts=0):
    return NS(
        name="app",
        ready=ready,
        restart_count=restarts,
        state=NS(
            waiting=NS(reason=reason) if reason else None,
            running=NS(started_at=NOW) if not reason else None,
            terminated=None,
        ),
        last_state=NS(terminated=term),
    )
