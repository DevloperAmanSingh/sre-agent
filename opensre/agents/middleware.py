import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException
from langgraph.types import Command  # pyright: ignore[reportMissingTypeStubs]

from opensre.output import cap_text


class OutputCapMiddleware(AgentMiddleware):
    def __init__(self, limit: int = 8000) -> None:
        self.limit = limit

    def _cap(self, result: ToolMessage | Command[Any]) -> ToolMessage:
        if not isinstance(result, ToolMessage):
            raise ValueError("Read-only tools must return a tool message, not a state command")
        content = result.content
        text = content if isinstance(content, str) else json.dumps(content)
        return result.model_copy(update={"content": cap_text(text, self.limit)})

    def _error(self, request: ToolCallRequest, exc: ToolException) -> ToolMessage:
        return ToolMessage(
            content=cap_text(str(exc), self.limit),
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        try:
            return self._cap(handler(request))
        except ToolException as exc:
            return self._error(request, exc)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        try:
            return self._cap(await handler(request))
        except ToolException as exc:
            return self._error(request, exc)
