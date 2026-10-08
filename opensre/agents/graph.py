from pathlib import Path
from typing import Any

from deepagents import create_deep_agent  # pyright: ignore[reportUnknownVariableType]
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
from opensre.agents.model import HarnessModel
from opensre.config import LLMSettings, MemorySettings
from opensre.connectors.registry import ToolSnapshot
from opensre.domain import Diagnosis
from opensre.llm import build_model
from opensre.memory.facts import load_facts


def resolve_skills_root() -> Path:
    package = Path(__file__).resolve().parents[1]
    bundled = package / "skills"
    if bundled.is_dir():
        return bundled
    checkout = package.parent
    if (checkout / "pyproject.toml").is_file():
        return checkout / "skills"
    raise ValueError("Bundled skills directory is missing")


SKILLS_ROOT = resolve_skills_root()


def build_agent(
    snapshot: ToolSnapshot,
    *,
    settings: LLMSettings | None = None,
    model: BaseChatModel | None = None,
    skills_root: Path = SKILLS_ROOT,
    memory: MemorySettings | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    if not any(skills_root.rglob("SKILL.md")):
        raise ValueError(f"No SKILL.md playbooks found in {skills_root}")
    chat = model if model is not None else build_model(settings or LLMSettings())
    memory = memory or MemorySettings()
    facts = load_facts(memory.dir) if memory.enabled else {}
    backend = SkillsBackend(skills_root, facts=facts)
    middleware: list[AgentMiddleware[Any, Any]] = [
        FilesystemMiddleware(backend=backend, tools=["ls", "read_file", "glob", "grep"]),
        OutputCapMiddleware(),
        ModelCallLimitMiddleware(run_limit=8, exit_behavior="error"),
        ToolCallLimitMiddleware(run_limit=16, exit_behavior="error"),
        ToolGuardMiddleware(snapshot),
    ]
    return create_deep_agent(
        model=HarnessModel(delegate=chat),
        tools=snapshot.tools,
        system_prompt=(Path(__file__).parent / "prompts/system.md").read_text(),
        backend=backend,
        skills=["/"],
        memory=list(facts),
        middleware=middleware,
        permissions=[FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")],
        response_format=ToolStrategy(Diagnosis),
    )
