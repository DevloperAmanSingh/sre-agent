from collections.abc import Sequence

from opensre.agents.graph import build_agent
from opensre.config import LLMSettings
from opensre.connectors.base import Connector
from opensre.connectors.registry import collect_tools
from opensre.domain import Diagnosis
from opensre.llm import build_model


def investigate(question: str, connectors: Sequence[Connector], settings: LLMSettings) -> Diagnosis:
    if not question.strip():
        raise ValueError("Question must not be empty")
    snapshot = collect_tools(connectors)
    model = build_model(settings)
    agent = build_agent(snapshot, model=model)
    result = agent.invoke(  # pyright: ignore[reportUnknownMemberType]
        {"messages": [{"role": "user", "content": question}]}, config={"recursion_limit": 100}
    )
    if "structured_response" not in result:
        raise ValueError("Investigation ended without a diagnosis")
    return Diagnosis.model_validate(result["structured_response"])
