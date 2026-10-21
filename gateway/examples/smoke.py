"""Explicit live Codex smoke test using only synthetic input; no API key or paid API."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from llm_gateway import Gateway, GatewayError, GenerateRequest, Settings
from llm_gateway.providers.codex import DISABLED_FEATURES, VERIFIED_VERSION


async def smoke(output: Path | None = None) -> dict:
    settings = Settings.from_env().model_copy(update={"max_attempts": 1})
    if settings.provider != "codex_cli":
        raise RuntimeError("This live smoke check requires LLM_PROVIDER=codex_cli")
    request = GenerateRequest(
        instruction="Return the object {ok:true}. No tools are needed.",
        input={"synthetic": True},
        schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean", "const": True}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    )
    evidence = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "verified_cli_version": VERIFIED_VERSION,
        "disabled_tool_features": list(DISABLED_FEATURES),
        "auth": "existing ChatGPT CLI login; credentials not read or copied",
        "synthetic_data": True,
    }
    try:
        result = await Gateway(settings).generate(request)
        evidence.update({"success": True, "result": result.model_dump()})
    except GatewayError as error:
        evidence.update({"success": False, **error.as_dict(request.trace_id)})
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    outcome = asyncio.run(smoke(args.output))
    print(json.dumps(outcome, indent=2))
    raise SystemExit(0 if outcome["success"] else 1)
