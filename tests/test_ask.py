import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fakes.model import ScriptedModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from typer.testing import CliRunner

from opensre.cli.main import app
from opensre.config import KubeSettings
from opensre.connectors.kubernetes.connector import KubernetesConnector


@pytest.mark.parametrize("json_output", [False, True])
def test_ask_reads_namespaces_then_renders_diagnosis(json_output, monkeypatch):
    from opensre.agents import run
    from opensre.cli import main

    diagnosis = {
        "summary": "Namespace visible",
        "cause": "No namespace fault observed",
        "evidence": [{"source": "k8s_list_namespaces", "detail": "default exists"}],
        "suggested_fix": "Check workload health next",
        "confidence": 0.8,
    }
    model = ScriptedModel(
        responses=[
            AIMessage(
                content="", tool_calls=[{"name": "k8s_list_namespaces", "args": {}, "id": "read"}]
            ),
            AIMessage(
                content="", tool_calls=[{"name": "Diagnosis", "args": diagnosis, "id": "answer"}]
            ),
        ]
    )
    page = SimpleNamespace(
        items=[SimpleNamespace(metadata=SimpleNamespace(name="default"))],
        metadata=SimpleNamespace(_continue="", remaining_item_count=0),
    )
    connector = KubernetesConnector(
        KubeSettings(),
        client_factory=lambda settings: nullcontext(object()),
        core_factory=lambda client: SimpleNamespace(list_namespace=lambda **kwargs: page),
    )
    monkeypatch.setattr(run, "build_model", lambda settings: model)
    monkeypatch.setattr(main, "build_connectors", lambda settings: [connector])
    result = CliRunner().invoke(
        app, ["ask", "What is wrong?", *(["--json"] if json_output else [])]
    )
    assert result.exit_code == 0, result.output
    if json_output:
        assert json.loads(result.stdout) == diagnosis
    else:
        for text in [
            diagnosis["summary"],
            diagnosis["cause"],
            diagnosis["suggested_fix"],
            "default exists",
        ]:
            assert text in result.stdout
    evidence = [message for message in model.seen[-1] if isinstance(message, ToolMessage)]
    assert json.loads(evidence[0].content)["namespaces"] == ["default"]
    assert model.index == 2


@pytest.mark.parametrize("name", ["task", "write_file", "edit_file", "execute"])
def test_investigation_uses_one_tool_snapshot(name, monkeypatch):
    from opensre.agents import run
    from opensre.config import LLMSettings

    @tool
    def observe() -> str:
        """Read target."""
        return "safe observation"

    @tool(name)
    def forbidden() -> str:
        """Must never be bound or executed."""
        raise AssertionError("Unsafe tool executed")

    observe.metadata = forbidden.metadata = {"read_only": True}
    calls = []

    def changing_tools():
        calls.append(1)
        return [observe] if len(calls) == 1 else [forbidden]

    diagnosis = {
        "summary": "Safe",
        "cause": "Unknown",
        "evidence": [],
        "suggested_fix": "Observe",
        "confidence": 0.0,
    }
    model = ScriptedModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "observe", "args": {}, "id": "read"}]),
            AIMessage(
                content="", tool_calls=[{"name": "Diagnosis", "args": diagnosis, "id": "answer"}]
            ),
        ]
    )
    monkeypatch.setattr(run, "build_model", lambda settings: model)
    result = run.investigate(
        "Investigate", [SimpleNamespace(name="fake", tools=changing_tools)], LLMSettings()
    )
    assert result.summary == "Safe"
    assert len(calls) == 1
    assert name not in model.bound_names
    assert any(
        isinstance(message, ToolMessage) and message.content == "safe observation"
        for message in model.seen[-1]
    )


def test_ask_missing_keys_is_a_clean_error():
    result = CliRunner().invoke(app, ["ask", "What is wrong?", "--json"])
    assert result.exit_code == 1
    assert "DEEPSEEK_API_KEY" in result.stderr
    assert "Traceback" not in result.output
    assert result.stdout == ""
