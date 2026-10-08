from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field


class ScriptedModel(BaseChatModel):
    responses: list[AIMessage] = Field(default_factory=lambda: [AIMessage(content="done")])
    bound_names: list[str] = Field(default_factory=list)
    seen: list = Field(default_factory=list)
    index: int = 0

    @property
    def _llm_type(self):
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.bound_names = [convert_to_openai_tool(tool)["function"]["name"] for tool in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        response = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])
