import pytest
from fakes.model import ScriptedModel
from langchain_core.messages import AIMessage

from opensre.agents.graph import SKILLS_ROOT, build_agent
from opensre.connectors.registry import collect_tools
from opensre.domain import Diagnosis


def test_agent_binds_only_read_tools_and_returns_diagnosis(tmp_path):
    diagnosis = {
        "summary": "No target evidence",
        "cause": "Unknown",
        "evidence": [],
        "suggested_fix": "Gather evidence",
        "confidence": 0.0,
    }
    model = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "Diagnosis",
                        "args": diagnosis,
                        "id": "answer",
                    }
                ],
            )
        ]
    )
    from opensre.config import MemorySettings
    from opensre.memory.facts import remember

    remember(tmp_path, "payments runs in ns shop")
    agent = build_agent(
        collect_tools([]),
        model=model,
        skills_root=SKILLS_ROOT,
        memory=MemorySettings(dir=tmp_path),
    )
    result = agent.invoke({"messages": [{"role": "user", "content": "Investigate"}]})
    assert result["structured_response"] == Diagnosis(**diagnosis)
    assert "payments runs in ns shop" in model.seen[0][0].text
    assert "triage" in model.seen[0][0].text
    assert "/triage/SKILL.md" in model.seen[0][0].text
    assert {"ls", "read_file", "glob", "grep"} <= set(model.bound_names)
    assert not {"execute", "write_file", "edit_file", "task"} & set(model.bound_names)


@pytest.mark.parametrize("tools_per_call", [1, 8])
def test_agent_stops_runaway_calls(tools_per_call):
    calls = [{"name": "ls", "args": {"path": "/"}, "id": str(i)} for i in range(tools_per_call)]
    model = ScriptedModel(responses=[AIMessage(content="", tool_calls=calls)])
    agent = build_agent(collect_tools([]), model=model)
    with pytest.raises(Exception, match="limit"):
        agent.invoke(
            {"messages": [{"role": "user", "content": "Investigate"}]},
            config={"recursion_limit": 100},
        )
    assert model.index <= 8
