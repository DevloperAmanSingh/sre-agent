from types import SimpleNamespace

import pytest
from fakes.model import ScriptedModel
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from opensre.agents.lock import inspect_tools
from opensre.connectors.registry import collect_tools


def test_inspection_captures_effective_tools_without_running_connector():
    @tool
    def observe() -> str:
        """Observe a target."""
        raise AssertionError("Inspection must not execute tools")

    observe.metadata = {"read_only": True}
    snapshot = collect_tools([SimpleNamespace(name="fake", tools=lambda: [observe])])
    assert inspect_tools(snapshot) == {
        "observe": "fake",
        "ls": "builtin",
        "read_file": "builtin",
        "glob": "builtin",
        "grep": "builtin",
    }


@pytest.mark.parametrize("name", ["task", "write_file", "edit_file", "execute", "unknown"])
def test_guard_rechecks_tools_before_each_real_model_call(name, monkeypatch, tmp_path):
    from opensre.agents import graph

    @tool(name)
    def injected() -> str:
        """Must never execute."""
        raise AssertionError("Unsafe tool executed")

    class InjectOnSecondCall(AgentMiddleware):
        calls = 0

        def wrap_model_call(self, request, handler):
            self.calls += 1
            if self.calls == 2:
                request = request.override(tools=[*request.tools, injected])
            return handler(request)

    original = graph.create_deep_agent

    def create(**kwargs):
        kwargs["middleware"].insert(1, InjectOnSecondCall())
        return original(**kwargs)

    monkeypatch.setattr(graph, "create_deep_agent", create)
    model = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "ls", "args": {"path": "/"}, "id": "read"},
                ],
            )
        ]
    )
    agent = graph.build_agent(collect_tools([]), model=model, skills_root=tmp_path)
    with pytest.raises(ValueError, match=f"forbidden.*{name}"):
        agent.invoke({"messages": [{"role": "user", "content": "Investigate"}]})
    assert model.index == 1
    assert name not in model.bound_names
