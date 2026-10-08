from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class KubeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context: str | None = None
    namespace: str = "default"
    request_timeout_s: float = Field(default=10, gt=0, allow_inf_nan=False)


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: str = "openai/gpt-5.6-luna"
    fallback: str | None = "deepseek/deepseek-chat"
    timeout_s: float = Field(default=60, gt=0, allow_inf_nan=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")
    kube: KubeSettings = Field(default_factory=KubeSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
