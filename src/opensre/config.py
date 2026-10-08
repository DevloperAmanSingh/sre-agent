import os
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import (
    BaseSettings,
    InitSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class KubeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context: str | None = None
    namespace: str = "default"
    request_timeout_s: float = Field(default=10, gt=0, allow_inf_nan=False)


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: str = "deepseek/deepseek-chat"
    fallback: str | None = "openai/gpt-5.6-luna"
    timeout_s: float = Field(default=60, gt=0, allow_inf_nan=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="forbid", env_prefix="OPENSRE_", env_nested_delimiter="__"
    )
    kube: KubeSettings = Field(default_factory=KubeSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)


class ConfigError(ValueError):
    pass


def load_settings(
    config_path: Path | None = None, overrides: dict[str, Any] | None = None
) -> Settings:
    path = config_path
    if path is None:
        env_path = os.environ.get("OPENSRE_CONFIG")
        local = Path("opensre.yaml")
        path = (
            Path(env_path)
            if env_path
            else (local if local.exists() else Path.home() / ".config/opensre/config.yaml")
        )
    try:
        data: dict[str, Any] = {}
        if path.exists():
            loaded = yaml.safe_load(path.read_text())
            if loaded is not None:
                if not isinstance(loaded, dict):
                    raise ConfigError(f"{path}: config must be a YAML mapping")
                data = cast(dict[str, Any], loaded)

        class FileSettings(Settings):
            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls: type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> tuple[PydanticBaseSettingsSource, ...]:
                return init_settings, env_settings, InitSettingsSource(settings_cls, data)

        return FileSettings(**(overrides or {}))
    except ValidationError as exc:
        fields = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigError(fields) from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: cannot read config: {exc}") from exc
