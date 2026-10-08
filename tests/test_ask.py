import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fakes.kubernetes import NOW, container_status, pod
from fakes.kubernetes import page as kube_page
from fakes.model import ScriptedModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from typer.testing import CliRunner

from opensre.cli.main import app
from opensre.config import KubeSettings
from opensre.connectors.kubernetes.connector import KubernetesConnector


@pytest.mark.parametrize("json_output,no_memory", [(False, False), (True, False), (True, True)])
def test_ask_reads_namespaces_then_renders_diagnosis(json_output, no_memory, monkeypatch, tmp_path):
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
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text(
        "contexts:\n- name: test\n  context: {cluster: test}\n"
        "clusters:\n- name: test\n  cluster: {server: 'https://test.invalid'}\n"
    )
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))
    monkeypatch.setenv("OPENSRE_MEMORY__DIR", str(tmp_path))
    monkeypatch.setattr("opensre.memory.store.utc_now", lambda: NOW)
    connector = KubernetesConnector(
        KubeSettings(context="test"),
        client_factory=lambda settings: nullcontext(object()),
        now=lambda: NOW,
        core_factory=lambda client: SimpleNamespace(
            list_namespace=lambda **kwargs: page,
            list_namespaced_pod=lambda **kwargs: kube_page(
                [pod([container_status("CrashLoopBackOff")])]
            ),
            list_namespaced_event=lambda **kwargs: kube_page([]),
        ),
    )
    monkeypatch.setattr(run, "build_model", lambda settings: model)
    monkeypatch.setattr(main, "build_connectors", lambda settings: [connector])
    result = CliRunner().invoke(
        app,
        [
            "ask",
            "What is wrong?",
            *(["--json"] if json_output else []),
            *(["--no-memory"] if no_memory else []),
        ],
    )
    assert result.exit_code == 0, result.output
    if json_output:
        assert json.loads(result.stdout) == {**diagnosis, "incident_id": None if no_memory else 1}
    else:
        assert "Saved as incident #1. Mark it: opensre feedback 1 --right" in result.stdout
        for text in [
            diagnosis["summary"],
            diagnosis["cause"],
            diagnosis["suggested_fix"],
            "default exists",
        ]:
            assert text in result.stdout
    assert (tmp_path / "memory.db").exists() is not no_memory
    evidence = [message for message in model.seen[-1] if isinstance(message, ToolMessage)]
    assert json.loads(evidence[0].content)["namespaces"] == ["default"]
    assert model.index == 2
    question = next(message for message in model.seen[0] if isinstance(message, HumanMessage))
    assert "What is wrong?" in question.content
    assert "CrashLoopBackOff" in question.content
    assert "pod/production/checkout" in question.content


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
        "Investigate",
        [SimpleNamespace(name="fake", tools=changing_tools, checks=lambda: [])],
        LLMSettings(),
    )
    assert result.summary == "Safe"
    assert len(calls) == 1
    assert name not in model.bound_names
    assert any(
        isinstance(message, ToolMessage) and message.content == "safe observation"
        for message in model.seen[-1]
    )


def test_ask_returns_after_credential_deadline(deadline, monkeypatch):
    from opensre.agents import run
    from opensre.cli import main

    def credentials(settings):
        deadline()
        raise RuntimeError("Released credential operation")

    connector = KubernetesConnector(
        KubeSettings(request_timeout_s=3), client_factory=credentials, now=lambda: NOW
    )
    diagnosis = {
        "summary": "Access timed out",
        "cause": "Unable to gather evidence",
        "evidence": [],
        "suggested_fix": "Check credentials",
        "confidence": 0.0,
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
    monkeypatch.setattr(run, "build_model", lambda settings: model)
    monkeypatch.setattr(main, "build_connectors", lambda settings: [connector])
    result = CliRunner().invoke(app, ["ask", "Investigate", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {**diagnosis, "incident_id": None}
    error = next(message for message in model.seen[-1] if isinstance(message, ToolMessage))
    assert error.status == "error"
    assert "timed out after 3s" in error.content


def test_ask_missing_keys_is_a_clean_error():
    result = CliRunner().invoke(app, ["ask", "What is wrong?", "--json"])
    assert result.exit_code == 1
    assert "DEEPSEEK_API_KEY" in result.stderr
    assert "Traceback" not in result.output
    assert result.stdout == ""
