from types import SimpleNamespace

import pytest
from langchain_core.tools import tool

from opensre.agents.lock import check_lock


def test_self_check_captures_effective_tools_without_running_connector(tmp_path):
    @tool
    def observe() -> str:
        """Observe a target."""
        raise AssertionError("Inspection must not execute tools")

    observe.metadata = {"read_only": True}
    connector = SimpleNamespace(name="fake", tools=lambda: [observe])
    sources = check_lock([connector], skills_root=tmp_path)
    assert sources == {
        "observe": "fake",
        "ls": "builtin",
        "read_file": "builtin",
        "glob": "builtin",
        "grep": "builtin",
    }


@pytest.mark.parametrize("name,marker", [("execute", True), ("task", True), ("unsafe", False)])
def test_self_check_rejects_unsafe_injected_tools(name, marker, tmp_path):
    @tool(name)
    def injected() -> str:
        """Injected tool."""
        return "unsafe"

    injected.metadata = {"read_only": marker}
    connector = SimpleNamespace(name="fake", tools=lambda: [injected])
    with pytest.raises(ValueError, match="read-only|forbidden|reserved"):
        check_lock([connector], skills_root=tmp_path)


def test_self_check_detects_unexpected_builtin_at_binding(monkeypatch, tmp_path):
    from opensre.agents import graph

    original = graph.create_deep_agent

    @tool
    def unexpected_write() -> str:
        """Unexpected builtin."""
        raise AssertionError("Must never execute")

    def injected(**kwargs):
        kwargs["tools"] = [*kwargs["tools"], unexpected_write]
        return original(**kwargs)

    monkeypatch.setattr(graph, "create_deep_agent", injected)
    with pytest.raises(ValueError, match="forbidden.*unexpected_write"):
        check_lock([], skills_root=tmp_path)
