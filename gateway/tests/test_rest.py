import asyncio
import json

import httpx
import pytest

from llm_gateway import Gateway, GatewayError, GenerateRequest, Settings
from llm_gateway.errors import ErrorCode
from llm_gateway.providers.rest import AnthropicProvider, GeminiProvider, OpenAIProvider

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    "provider,name",
    [(OpenAIProvider, "openai"), (AnthropicProvider, "anthropic"), (GeminiProvider, "gemini")],
)
def test_paid_providers_fail_closed_without_opt_in(provider, name):
    with pytest.raises(GatewayError) as error:
        provider(Settings(**{f"{name}_key": "test-only-key", f"{name}_model": "test-model"}))
    assert error.value.code == ErrorCode.CONFIGURATION


@pytest.mark.parametrize(
    "provider,name,response",
    [
        (
            OpenAIProvider,
            "openai",
            {
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": '{"ok":true}'}]}],
            },
        ),
        (
            AnthropicProvider,
            "anthropic",
            {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"ok":true}'}]},
        ),
        (
            GeminiProvider,
            "gemini",
            {
                "candidates": [
                    {"finishReason": "STOP", "content": {"parts": [{"text": '{"ok":true}'}]}}
                ]
            },
        ),
    ],
)
def test_real_rest_interfaces_with_mock_transport(provider, name, response):
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        assert request.method == "POST"
        return httpx.Response(200, json=response)

    settings = Settings(
        provider=name,
        allow_paid=True,
        **{f"{name}_key": "test-only-key", f"{name}_model": "test-model"},
    )
    gateway = Gateway(settings, provider(settings, httpx.MockTransport(handle)))
    result = asyncio.run(gateway.generate(GenerateRequest(schema=SCHEMA)))
    assert result.data == {"ok": True}
    assert "tools" not in captured[0]
    if name == "openai":
        assert captured[0]["text"]["format"]["strict"] is True
        assert captured[0]["store"] is False
    if name == "anthropic":
        assert captured[0]["output_config"]["format"]["schema"] == SCHEMA
    if name == "gemini":
        assert captured[0]["generationConfig"]["responseFormat"]["text"]["schema"] == SCHEMA


def test_rate_limit_retry_and_invalid_output_recovery():
    sequence = [
        httpx.Response(429, headers={"retry-after": "0.01"}),
        httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": '{"ok":true}'}]}],
            },
        ),
    ]
    settings = Settings(
        provider="openai",
        allow_paid=True,
        openai_key="test-only-key",
        openai_model="test-model",
        max_attempts=2,
    )
    provider = OpenAIProvider(settings, httpx.MockTransport(lambda request: sequence.pop(0)))
    result = asyncio.run(Gateway(settings, provider).generate(GenerateRequest(schema=SCHEMA)))
    assert result.attempts == 2


def test_provider_credentials_and_error_bodies_not_disclosed():
    settings = Settings(
        provider="openai",
        allow_paid=True,
        openai_key="test-only-key",
        openai_model="test-model",
        max_attempts=1,
    )
    provider = OpenAIProvider(
        settings,
        httpx.MockTransport(
            lambda request: httpx.Response(
                401, json={"error": "person@example.com test-only-key private-provider-detail"}
            )
        ),
    )
    with pytest.raises(GatewayError) as error:
        asyncio.run(Gateway(settings, provider).generate(GenerateRequest(schema=SCHEMA)))
    assert error.value.code == ErrorCode.AUTHENTICATION
    assert "private-provider-detail" not in str(error.value)
    assert "test-only-key" not in str(error.value)


def test_malformed_provider_json_is_a_safe_retryable_error():
    settings = Settings(
        provider="openai",
        allow_paid=True,
        openai_key="test-only-key",
        openai_model="test-model",
        max_attempts=1,
    )
    provider = OpenAIProvider(
        settings,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "private-provider-content that is not JSON",
                                }
                            ]
                        }
                    ],
                },
            )
        ),
    )
    with pytest.raises(GatewayError) as error:
        asyncio.run(Gateway(settings, provider).generate(GenerateRequest(schema=SCHEMA)))
    assert error.value.code == ErrorCode.INVALID_OUTPUT
    assert error.value.retryable
    assert "private-provider-content" not in str(error.value)
