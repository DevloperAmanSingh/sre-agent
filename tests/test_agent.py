from fakes.model import ScriptedModel
from langchain_core.messages import AIMessage

from opensre.agents.graph import build_agent
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
    agent = build_agent([], model=model, skills_root=tmp_path)
    result = agent.invoke({"messages": [{"role": "user", "content": "Investigate"}]})
    assert result["structured_response"] == Diagnosis(**diagnosis)
    assert {"ls", "read_file", "glob", "grep"} <= set(model.bound_names)
    assert not {"execute", "write_file", "edit_file", "task"} & set(model.bound_names)
