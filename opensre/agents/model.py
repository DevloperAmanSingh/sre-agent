from collections.abc import Callable, Sequence
from typing import Any, Literal

from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile
from deepagents.profiles.harness.harness_profiles import (
    _get_harness_profile,  # pyright: ignore[reportPrivateUsage]
)
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

PROFILE_KEY = "opensre:harness"
_PROFILE = HarnessProfile(
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    excluded_tools=frozenset({"execute", "write_file", "edit_file"}),
)
# The pinned deepagents API exposes registration but no public profile lookup.
_existing = _get_harness_profile(PROFILE_KEY)
if _existing is not None and _existing != _PROFILE:
    raise ValueError(f"Conflicting harness profile for {PROFILE_KEY}")
if _existing is None:
    register_harness_profile(PROFILE_KEY, _PROFILE)


class HarnessModel(BaseChatModel):
    """Give a settings-built LiteLLM model a profile identity owned only by this harness."""

    model_name: Literal["opensre:harness"] = PROFILE_KEY
    delegate: BaseChatModel

    @property
    def _llm_type(self) -> str:
        return "opensre-harness"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self.delegate.bind_tools(tools, tool_choice=tool_choice, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self.delegate.invoke(messages, stop=stop, **kwargs)
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = await self.delegate.ainvoke(messages, stop=stop, **kwargs)
        return ChatResult(generations=[ChatGeneration(message=message)])
