from collections.abc import Sequence
from pathlib import Path
from typing import Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,  # pyright: ignore[reportUnknownVariableType]
    register_harness_profile,
)
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph  # pyright: ignore[reportMissingTypeStubs]

from opensre.agents.backend import SkillsBackend
from opensre.agents.middleware import OutputCapMiddleware
from opensre.config import LLMSettings
from opensre.connectors.base import Connector
from opensre.connectors.registry import collect_tools
from opensre.domain import Diagnosis
from opensre.llm import build_model

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


def build_agent(
    connectors: Sequence[Connector],
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
    return create_deep_agent(
        model=chat,
        tools=collect_tools(connectors),
        system_prompt=(Path(__file__).parent / "prompts/system.md").read_text(),
        backend=SkillsBackend(skills_root),
        middleware=[OutputCapMiddleware()],
        permissions=[FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")],
        response_format=ToolStrategy(Diagnosis),
    )
