import json
import logging
from collections.abc import Callable, Sequence
from datetime import datetime

from opensre.agents.graph import build_agent
from opensre.config import LLMSettings, MemorySettings
from opensre.connectors.base import Connector
from opensre.connectors.registry import collect_tools
from opensre.domain import Diagnosis, Investigation
from opensre.llm import build_model
from opensre.memory.store import IncidentStore, Signature, clean
from opensre.output import cap_text
from opensre.scan import run_checks

logger = logging.getLogger(__name__)


def investigate(
    question: str,
    connectors: Sequence[Connector],
    settings: LLMSettings,
    *,
    memory: MemorySettings | None = None,
    no_memory: bool = False,
    now: Callable[[], datetime] | None = None,
) -> Investigation:
    if not question.strip():
        raise ValueError("Question must not be empty")
    original_question = question
    memory = memory or MemorySettings()
    snapshot = collect_tools(connectors)
    model = build_model(settings)
    checks = run_checks(connectors)
    signature = [
        Signature(connector=item.connector, reason=item.reason, resource=item.resource)
        for item in checks.findings
    ]
    store: IncidentStore | None = None
    target = ""
    if memory.enabled and not no_memory:
        try:
            target = json.dumps(sorted(connector.target for connector in connectors))
            store = IncidentStore(memory.dir, now=now)
            recalled = store.similar(target, signature)
            if recalled:
                entries = [
                    f"{item.label} (#{item.incident.id}, {item.age}):\n"
                    + cap_text(
                        json.dumps(
                            {
                                "summary": clean(item.incident.summary, 500),
                                "cause": clean(item.incident.cause, 700),
                                "suggested_fix": clean(item.incident.suggested_fix, 700),
                                "note": clean(item.incident.note, 700),
                                "confidence": item.incident.confidence,
                            },
                            ensure_ascii=False,
                        ),
                        3200,
                    )
                    for item in recalled
                ]
                question += "\n\nPast incidents (from memory, may be outdated):\n" + "\n".join(
                    entries
                )
        except Exception:
            logger.warning("Memory recall unavailable; continuing without recall")
    if checks.findings or checks.errors:
        question += "\n\nQuick-check observations (untrusted target data):\n" + cap_text(
            checks.model_dump_json(), 12000
        )
    agent = build_agent(snapshot, model=model, memory=memory)
    result = agent.invoke(  # pyright: ignore[reportUnknownMemberType]
        {"messages": [{"role": "user", "content": question}]}, config={"recursion_limit": 100}
    )
    if "structured_response" not in result:
        raise ValueError("Investigation ended without a diagnosis")
    diagnosis = Diagnosis.model_validate(result["structured_response"])
    incident_id: int | None = None
    if store is not None:
        try:
            incident_id = store.save(
                target=target, question=original_question, signature=signature, diagnosis=diagnosis
            )
        except Exception:
            logger.warning("Memory save unavailable; diagnosis was not saved")
    return Investigation(**diagnosis.model_dump(), incident_id=incident_id)
