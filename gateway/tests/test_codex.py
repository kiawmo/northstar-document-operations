import asyncio
import json
from pathlib import Path

import pytest

from llm_gateway import GatewayError, GenerateRequest, Settings
from llm_gateway.errors import ErrorCode
from llm_gateway.process import ProcessResult
from llm_gateway.providers.codex import CodexCliProvider

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    "body,event,code",
    [
        ("not-json", {"type": "turn.completed"}, ErrorCode.INVALID_OUTPUT),
        (
            '{"ok":true}',
            {"type": "item.started", "item": {"type": "mcp_tool_call"}},
            ErrorCode.POLICY,
        ),
    ],
)
def test_codex_malformed_output_and_tool_events_fail_closed_with_temp_cleanup(
    tmp_path, monkeypatch, body, event, code
):
    provider = CodexCliProvider(Settings(temp_dir=str(tmp_path)))
    created = []

    async def preflight():
        return Path("test-executable-not-invoked.exe")

    async def fake_process(args, **kwargs):
        response = Path(args[args.index("--output-last-message") + 1])
        created.append(response.parent)
        response.write_text(body, encoding="utf-8")
        assert kwargs["stdin"]
        return ProcessResult(0, json.dumps(event).encode(), b"")

    monkeypatch.setattr(provider, "preflight", preflight)
    monkeypatch.setattr("llm_gateway.providers.codex.run_process", fake_process)
    with pytest.raises(GatewayError) as error:
        asyncio.run(provider.generate(GenerateRequest(schema=SCHEMA)))
    assert error.value.code == code
    assert all(not directory.exists() for directory in created)
