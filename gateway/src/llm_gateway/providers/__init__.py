from typing import Protocol

from ..models import GenerateRequest


class LLMProvider(Protocol):
    name: str

    async def generate(self, request: GenerateRequest) -> dict:
        """Return JSON data. The gateway performs independent schema validation."""
        ...
