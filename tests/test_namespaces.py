import json
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException

from opensre.config import KubeSettings
from opensre.connectors.kubernetes.connector import KubernetesConnector


@pytest.mark.parametrize("remaining,extra", [(0, 0), (7, 0), (None, 0), (0, 1)])
def test_namespace_tool_returns_bounded_typed_result(remaining, extra):
    def list_namespace(**kwargs):
        assert kwargs == {"limit": 2, "_request_timeout": 3}
        return SimpleNamespace(
            items=[
                SimpleNamespace(metadata=SimpleNamespace(name=name))
                for name in ["a", "b", "c"][: 2 + extra]
            ],
            metadata=SimpleNamespace(
                remaining_item_count=remaining, _continue="next" if remaining != 0 else ""
            ),
        )

    connector = KubernetesConnector(
        KubeSettings(request_timeout_s=3),
        client_factory=lambda settings: nullcontext(object()),
        core_factory=lambda client: SimpleNamespace(list_namespace=list_namespace),
    )
    tool = connector.tools()[0]
    result = tool.invoke(
        {"name": tool.name, "args": {"limit": 2}, "id": "read", "type": "tool_call"}
    )
    assert tool.metadata["read_only"] is True
    assert result.artifact.namespaces == ["a", "b"]
    assert result.artifact.cut == (None if remaining is None else remaining + extra)
    assert result.artifact.more_available is (remaining != 0 or extra > 0)
    assert json.loads(result.content) == result.artifact.model_dump()


@pytest.mark.parametrize("stage", ["credentials", "request", "cleanup"])
def test_namespace_deadline_covers_whole_operation(stage, deadline):
    @contextmanager
    def client_factory(settings):
        if stage == "credentials":
            deadline()
        yield object()
        if stage == "cleanup":
            deadline()

    def list_namespace(**kwargs):
        if stage == "request":
            deadline()
        return SimpleNamespace(
            items=[], metadata=SimpleNamespace(_continue="", remaining_item_count=0)
        )

    tool = KubernetesConnector(
        KubeSettings(request_timeout_s=3),
        client_factory=client_factory,
        core_factory=lambda client: SimpleNamespace(list_namespace=list_namespace),
    ).tools()[0]
    with pytest.raises(ToolException, match="timed out after 3s"):
        tool.invoke({})


def test_namespace_errors_are_explicit():
    def unavailable(settings):
        raise RuntimeError("cluster unavailable")

    tool = KubernetesConnector(KubeSettings(), client_factory=unavailable).tools()[0]
    with pytest.raises(ToolException, match="cluster unavailable"):
        tool.invoke({})
