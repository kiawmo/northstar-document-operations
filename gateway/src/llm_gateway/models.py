import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    task: str = Field(default="generic", min_length=1, max_length=80, pattern=r"^[a-z_]+$")
    instruction: str = Field(
        default="Return a JSON object matching the supplied schema using only the supplied data.",
        min_length=1,
        max_length=8_000,
    )
    input: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(alias="schema")
    trace_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    fixture: str = Field(default="default", max_length=80, pattern=r"^[a-z_]+$")


class GenerateResponse(BaseModel):
    trace_id: str
    provider: str
    data: dict[str, Any]
    latency_ms: float
    attempts: int
