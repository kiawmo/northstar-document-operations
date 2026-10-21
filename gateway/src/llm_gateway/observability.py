import json
import logging
import re
from typing import Any

LOGGER = logging.getLogger("llm_gateway")
_SECRET_KEYS = re.compile(r"(?i)(password|token|secret|authorization|api.?key|cookie)")
_TOKEN_PATTERN = re.compile(r"(?i)(Bearer\s+\S+|sk-[A-Za-z0-9_-]{8,})")
_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if _SECRET_KEYS.search(str(k)) else redact(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _EMAIL_PATTERN.sub("[EMAIL]", _TOKEN_PATTERN.sub("[REDACTED]", value))
    return value


def audit(event: str, **metadata: Any) -> None:
    """Callers supply metadata only. Prompts, provider bodies and credentials are never logged."""
    LOGGER.info(json.dumps(redact({"event": event, **metadata}), separators=(",", ":")))
