import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from .secrets import secret_value


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["codex_cli", "fixture", "openai", "anthropic", "gemini"] = "codex_cli"
    codex_executable: str = ""
    codex_model: str = ""
    temp_dir: str = ""
    concurrency: int = Field(default=2, ge=1, le=16)
    timeout_seconds: float = Field(default=120, ge=0.05, le=900)
    queue_timeout_seconds: float = Field(default=3, ge=0.01, le=60)
    max_attempts: int = Field(default=2, ge=1, le=3)
    max_input_bytes: int = Field(default=100_000, ge=1, le=1_000_000)
    max_output_bytes: int = Field(default=2_000_000, ge=1_024, le=10_000_000)
    host: str = "127.0.0.1"
    port: int = Field(default=8100, ge=1, le=65535)
    api_key: SecretStr = SecretStr("")
    allow_paid: bool = False
    openai_key: SecretStr = SecretStr("")
    openai_model: str = ""
    anthropic_key: SecretStr = SecretStr("")
    anthropic_model: str = ""
    gemini_key: SecretStr = SecretStr("")
    gemini_model: str = ""

    @model_validator(mode="after")
    def safe_network_binding(self):
        if (
            self.host not in {"127.0.0.1", "localhost", "::1"}
            and not self.api_key.get_secret_value()
        ):
            raise ValueError("A non-loopback listener requires GATEWAY_API_KEY")
        return self

    @classmethod
    def from_env(cls) -> "Settings":
        mapping = {
            "provider": "LLM_PROVIDER",
            "codex_executable": "CODEX_EXECUTABLE",
            "codex_model": "CODEX_MODEL",
            "temp_dir": "LLM_TEMP_DIR",
            "concurrency": "LLM_CONCURRENCY",
            "timeout_seconds": "LLM_TIMEOUT_SECONDS",
            "queue_timeout_seconds": "LLM_QUEUE_TIMEOUT_SECONDS",
            "max_attempts": "LLM_MAX_ATTEMPTS",
            "host": "GATEWAY_HOST",
            "port": "GATEWAY_PORT",
            "api_key": "GATEWAY_API_KEY",
            "allow_paid": "ALLOW_PAID_LLM",
            "openai_key": "OPENAI_API_KEY",
            "openai_model": "OPENAI_MODEL",
            "anthropic_key": "ANTHROPIC_API_KEY",
            "anthropic_model": "ANTHROPIC_MODEL",
            "gemini_key": "GEMINI_API_KEY",
            "gemini_model": "GEMINI_MODEL",
        }
        secrets = {"api_key", "openai_key", "anthropic_key", "gemini_key"}
        values = {
            k: os.environ[v] for k, v in mapping.items() if k not in secrets and os.environ.get(v)
        }
        for key in secrets:
            value = secret_value(mapping[key])
            if value:
                values[key] = value
        return cls(**values)
