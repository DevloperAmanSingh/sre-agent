from collections.abc import Awaitable, Callable, Sequence
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

from opensre.connectors.registry import READ_TOOLS, ToolSnapshot


class ToolGuardMiddleware(AgentMiddleware):
    def __init__(self, snapshot: ToolSnapshot) -> None:
        self.allowed = READ_TOOLS | set(snapshot.sources)

    def _check(self, request: ModelRequest) -> None:
        names = {convert_to_openai_tool(tool)["function"]["name"] for tool in request.tools}
        unsafe = names - self.allowed
        if unsafe:
            raise ValueError(f"Read-only guard found forbidden or unknown tools: {sorted(unsafe)}")

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        self._check(request)
        return handler(request)

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        self._check(request)
        return await handler(request)


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


def inspect_tools(snapshot: ToolSnapshot) -> dict[str, str]:
    from opensre.agents.graph import build_agent

    probe = InspectionModel()
    agent = build_agent(snapshot, model=probe)
    try:
        agent.invoke(  # pyright: ignore[reportUnknownMemberType]
            {"messages": [{"role": "user", "content": "Inspect tool bindings only."}]}
        )
    except _Captured:
        pass
    if not probe.names:
        raise ValueError("Read-only self-check did not capture model tools")
    return {
        name: snapshot.sources.get(name, "builtin") for name in probe.names if name != "Diagnosis"
    }
