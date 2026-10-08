from pathlib import Path
from typing import Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,  # pyright: ignore[reportUnknownVariableType]
    register_harness_profile,
)
from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph  # pyright: ignore[reportMissingTypeStubs]

from opensre.agents.backend import SkillsBackend
from opensre.agents.lock import ToolGuardMiddleware
from opensre.agents.middleware import OutputCapMiddleware
from opensre.config import LLMSettings
from opensre.connectors.registry import ToolSnapshot
from opensre.domain import Diagnosis
from opensre.llm import build_model

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


def build_agent(
    snapshot: ToolSnapshot,
    *,
    settings: LLMSettings | None = None,
    model: BaseChatModel | None = None,
    skills_root: Path = SKILLS_ROOT,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    chat = model if model is not None else build_model(settings or LLMSettings())
    provider = chat._get_ls_params().get("ls_provider")  # pyright: ignore[reportPrivateUsage]
    if not provider:
        raise ValueError("Cannot identify model provider for read-only profile")
    identifier = getattr(chat, "model_name", None) or getattr(chat, "model", None)
    profile = HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        excluded_tools=frozenset({"execute", "write_file", "edit_file"}),
    )
    register_harness_profile(f"{provider}:{identifier}" if identifier else provider, profile)
    backend = SkillsBackend(skills_root)
    middleware: list[AgentMiddleware[Any, Any]] = [
        FilesystemMiddleware(backend=backend, tools=["ls", "read_file", "glob", "grep"]),
        OutputCapMiddleware(),
        ModelCallLimitMiddleware(run_limit=8, exit_behavior="error"),
        ToolCallLimitMiddleware(run_limit=16, exit_behavior="error"),
        ToolGuardMiddleware(snapshot),
    ]
    return create_deep_agent(
        model=chat,
        tools=snapshot.tools,
        system_prompt=(Path(__file__).parent / "prompts/system.md").read_text(),
        backend=backend,
        skills=["/"],
        middleware=middleware,
        permissions=[FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")],
        response_format=ToolStrategy(Diagnosis),
    )
