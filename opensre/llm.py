import os
from typing import TypedDict, cast

# Construction must not fetch LiteLLM's remote model cost map.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402
from langchain_litellm import ChatLiteLLM  # noqa: E402

from opensre.config import LLMSettings  # noqa: E402

litellm.suppress_debug_info = True


class KeyValidation(TypedDict):
    keys_in_environment: bool
    missing_keys: list[str]


class LLMError(ValueError):
    pass


def configured_models(settings: LLMSettings) -> list[tuple[str, str]]:
    models = [("llm.primary", settings.primary)]
    if settings.fallback and settings.fallback != settings.primary:
        models.append(("llm.fallback", settings.fallback))
    return models


def validate_model(model: str, field: str) -> None:
    try:
        litellm.get_llm_provider(model=model)
    except Exception as exc:
        raise LLMError(f"{field}: unsupported model or provider: {model}") from exc


def validate_keys(model: str) -> None:
    result = cast(KeyValidation, litellm.validate_environment(model))  # pyright: ignore[reportUnknownMemberType]
    missing = result.get("missing_keys", [])
    if not result.get("keys_in_environment") or missing:
        detail = ", ".join(missing) or "unsupported model or provider"
        raise LLMError(f"{model}: {detail}")


def build_model(settings: LLMSettings) -> ChatLiteLLM:
    for field, model in configured_models(settings):
        validate_model(model, field)
        validate_keys(model)
    return ChatLiteLLM(
        model=settings.primary,
        request_timeout=settings.timeout_s,
        model_kwargs={"fallbacks": [settings.fallback]} if settings.fallback else {},
    )
