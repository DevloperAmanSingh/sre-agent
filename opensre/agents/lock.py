from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from opensre.agents.graph import SKILLS_ROOT, build_agent
from opensre.connectors.base import Connector
from opensre.connectors.registry import collect_tools

READ_TOOLS = frozenset({"ls", "read_file", "glob", "grep"})
FORBIDDEN = frozenset({"execute", "write_file", "edit_file", "task"})


class _Captured(Exception):
    pass


class InspectionModel(BaseChatModel):
    names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "inspection"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        self.names = [convert_to_openai_tool(tool)["function"]["name"] for tool in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise _Captured


class _Inspect(AgentMiddleware):
    def __init__(self, model: InspectionModel) -> None:
        self.model = model

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        return handler(request.override(model=self.model))


def check_lock(
    connectors: Sequence[Connector],
    *,
    model: BaseChatModel | None = None,
    skills_root: Path = SKILLS_ROOT,
) -> dict[str, str]:
    tools = collect_tools(connectors)
    reserved = READ_TOOLS | FORBIDDEN | {"Diagnosis"}
    for tool in tools:
        if tool.name in reserved:
            raise ValueError(f"Connector tool {tool.name} uses a reserved name")
    probe = InspectionModel()
    agent = build_agent(
        connectors, model=model or probe, skills_root=skills_root, inspection=_Inspect(probe)
    )
    try:
        agent.invoke(  # pyright: ignore[reportUnknownMemberType]
            {"messages": [{"role": "user", "content": "Inspect tool bindings only."}]}
        )
    except _Captured:
        pass
    if not probe.names:
        raise ValueError("Read-only self-check did not capture model tools")
    sources = {tool.name: connector.name for connector in connectors for tool in connector.tools()}
    allowed = READ_TOOLS | set(sources) | {"Diagnosis"}
    unsafe = set(probe.names) - allowed
    if unsafe or FORBIDDEN.intersection(probe.names):
        raise ValueError(f"Read-only self-check found forbidden tools: {sorted(unsafe)}")
    return {name: sources.get(name, "builtin") for name in probe.names if name != "Diagnosis"}
