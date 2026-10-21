import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from llm_gateway import Gateway, GatewayError, GenerateRequest, Settings
from llm_gateway.app import create_app
from llm_gateway.errors import ErrorCode
from llm_gateway.observability import redact
from llm_gateway.providers.fixture import DEFAULT_FIXTURES, FixtureProvider

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def request(**changes):
    return GenerateRequest(schema=SCHEMA, **changes)


def test_fixture_public_import_and_http_contract():
    with TestClient(create_app(Settings(provider="fixture"))) as client:
        result = client.post(
            "/generate", json=request(trace_id="trace.test").model_dump(by_alias=True)
        )
        assert result.status_code == 200
        assert result.json()["data"] == {"ok": True}
        assert result.json()["provider"] == "fixture"
        assert result.headers["x-trace-id"] == "trace.test"
        assert result.headers["cache-control"] == "no-store"
        assert client.get("/health").json()["metrics"]["successes"] == 1
        assert client.get("/ready").status_code == 200


def test_invalid_output_retries_bounded_and_reports_safe_error():
    service = Gateway(Settings(provider="fixture", max_attempts=2))
    with pytest.raises(GatewayError) as error:
        asyncio.run(service.generate(request(fixture="invalid_schema")))
    assert error.value.code == ErrorCode.INVALID_OUTPUT
    assert service.metrics["retries"] == 1
    assert service.active == 0


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array"},
        {"type": "object", "$ref": "https://example.com/schema"},
        {"type": "object", "properties": {"x": {"type": "nonsense"}}},
    ],
)
def test_invalid_or_remote_reference_schemas_rejected(schema):
    service = Gateway(Settings(provider="fixture"))
    with pytest.raises(GatewayError) as error:
        asyncio.run(service.generate(GenerateRequest(schema=schema)))
    assert error.value.code == ErrorCode.INVALID_SCHEMA


def test_body_limit_includes_streamed_chunked_body():
    client = TestClient(create_app(Settings(provider="fixture", max_input_bytes=300)))
    response = client.post("/generate", content=(x for x in [b"a" * 200, b"b" * 200]))
    assert response.status_code == 413
    assert "aaaaaaaa" not in response.text


def test_http_validation_does_not_echo_customer_data():
    client = TestClient(create_app(Settings(provider="fixture")))
    response = client.post("/generate", json={"secret_customer_email": "person@example.com"})
    assert response.status_code == 422
    assert "person@example.com" not in response.text


def test_authenticated_gateway_requires_constant_time_bearer():
    client = TestClient(create_app(Settings(provider="fixture", api_key="test-only-token")))
    assert client.get("/docs").status_code == 401
    assert client.get("/health").status_code == 200
    assert client.post("/generate", json=request().model_dump(by_alias=True)).status_code == 401
    assert (
        client.post(
            "/generate",
            json=request().model_dump(by_alias=True),
            headers={"Authorization": "Bearer test-only-token"},
        ).status_code
        == 200
    )


def test_nonloopback_binding_requires_key():
    with pytest.raises(ValueError, match="non-loopback"):
        Settings(host="0.0.0.0")  # noqa: S104 - test intentionally rejects an unsafe bind


def test_logs_exclude_prompt_data_and_redact_sensitive_metadata(caplog):
    caplog.set_level(logging.INFO, logger="llm_gateway")
    service = Gateway(Settings(provider="fixture"))
    asyncio.run(
        service.generate(request(input={"email": "person@example.com", "password": "very-secret"}))
    )
    text = "\n".join(row.message for row in caplog.records)
    assert "person@example.com" not in text
    assert "very-secret" not in text
    assert "latency_ms" in text
    assert redact({"api_key": "unsafe", "message": "Bearer unsafe person@example.com"}) == {
        "api_key": "[REDACTED]",
        "message": "[REDACTED] [EMAIL]",
    }


class SlowProvider:
    name = "test"

    def __init__(self):
        self.active = 0
        self.high_water = 0

    async def generate(self, request):
        self.active += 1
        self.high_water = max(self.active, self.high_water)
        try:
            await asyncio.sleep(0.06)
            return {"ok": True}
        finally:
            self.active -= 1


def test_bounded_concurrency_and_queue_rejection():
    async def scenario():
        provider = SlowProvider()
        gateway = Gateway(
            Settings(provider="fixture", concurrency=2, queue_timeout_seconds=1), provider
        )
        outcomes = await asyncio.gather(
            *(gateway.generate(request()) for _ in range(10)), return_exceptions=True
        )
        assert provider.high_water == 2
        assert sum(isinstance(value, GatewayError) for value in outcomes) == 2
        assert gateway.active == 0
        assert gateway._inflight == 0

    asyncio.run(scenario())


def test_queue_deadline_and_provider_deadline_release_capacity():
    async def scenario():
        provider = SlowProvider()
        gateway = Gateway(
            Settings(
                provider="fixture",
                concurrency=1,
                queue_timeout_seconds=0.01,
                timeout_seconds=0.05,
                max_attempts=1,
            ),
            provider,
        )
        outcomes = await asyncio.gather(
            gateway.generate(request()), gateway.generate(request()), return_exceptions=True
        )
        assert {error.code for error in outcomes} == {ErrorCode.TIMEOUT, ErrorCode.OVERLOADED}
        assert gateway.active == 0
        assert gateway._inflight == 0

    asyncio.run(scenario())


def test_cancelled_request_releases_capacity():
    async def scenario():
        gateway = Gateway(Settings(provider="fixture"), SlowProvider())
        task = asyncio.create_task(gateway.generate(request()))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert gateway.active == 0
        assert gateway._inflight == 0

    asyncio.run(scenario())


def test_custom_named_fixture_injection_is_explicit():
    gateway = Gateway(
        Settings(provider="fixture"), FixtureProvider({"invoice_extraction": {"ok": True}})
    )
    response = asyncio.run(gateway.generate(request(task="invoice_extraction")))
    assert response.data == {"ok": True}
    assert set(DEFAULT_FIXTURES) == {"generic", "revenue", "support", "invoice"}


def test_local_pydantic_definitions_are_supported_without_remote_resolution():
    schema = {
        "type": "object",
        "$defs": {"Result": SCHEMA},
        "properties": {"result": {"$ref": "#/$defs/Result"}},
        "required": ["result"],
        "additionalProperties": False,
    }
    gateway = Gateway(
        Settings(provider="fixture"), FixtureProvider({"generic": {"result": {"ok": True}}})
    )
    assert asyncio.run(gateway.generate(GenerateRequest(schema=schema))).data == {
        "result": {"ok": True}
    }


@pytest.mark.parametrize("reference", ["#/$defs/Result", "#/$defs/Missing"])
def test_recursive_or_missing_local_definitions_fail_closed(reference):
    schema = {
        "type": "object",
        "$defs": {"Result": {"$ref": reference}},
        "properties": {"result": {"$ref": "#/$defs/Result"}},
    }
    with pytest.raises(GatewayError) as error:
        asyncio.run(Gateway(Settings(provider="fixture")).generate(GenerateRequest(schema=schema)))
    assert error.value.code == ErrorCode.INVALID_SCHEMA
