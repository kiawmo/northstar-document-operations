"""Parser isolation, bounded response and credential-boundary tests."""

import json
import os

from fastapi.testclient import TestClient
import httpx
import pytest

from docops.config import Settings
from docops.extraction import ParserText, PermanentExtractionError, TransientProviderError, parser_request
from docops.parser_api import app


def test_parser_request_sends_source_without_private_credentials():
    def handle(request):
        assert request.content == b"source"
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        return httpx.Response(200, json={"text": "source", "used_ocr": False})

    output = parser_request(
        b"source",
        "text/plain",
        Settings(parser_url="http://parser:8104", api_key="private-value"),
        "extract",
        httpx.MockTransport(handle),
    )
    assert ParserText.model_validate_json(output).text == "source"


@pytest.mark.parametrize(
    "output",
    [
        {"text": "x" * 100001, "used_ocr": False},
        {"text": "source", "used_ocr": 1},
        {"text": "source", "used_ocr": False, "unexpected": "field"},
    ],
)
def test_parser_text_rejects_oversized_or_malformed_schema(output):
    with pytest.raises(ValueError):
        ParserText.model_validate_json(json.dumps(output))


def test_parser_refuses_redirects_and_does_not_forward_source():
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://unauthorized-host/"})

    with pytest.raises(TransientProviderError):
        parser_request(
            b"source", "text/plain", Settings(parser_url="http://parser:8104"), "extract", httpx.MockTransport(handle)
        )
    assert calls == ["http://parser:8104/extract"]


def test_parser_response_byte_bound_is_enforced():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (1024**2 + 1)))
    with pytest.raises(PermanentExtractionError, match="exceeded its bound"):
        parser_request(b"source", "text/plain", Settings(parser_url="http://parser:8104"), "extract", transport)


def test_parser_guardian_rejects_oversized_and_unsupported_sources():
    with TestClient(app) as client:
        assert (
            client.post(
                "/extract", content=b"x" * (5 * 1024**2 + 1), headers={"Content-Type": "text/plain"}
            ).status_code
            == 413
        )
        assert (
            client.post("/extract", content=b"<xml/>", headers={"Content-Type": "application/xml"}).status_code == 422
        )
        assert client.post("/extract", content=b"", headers={"Content-Type": "text/plain"}).status_code == 422


@pytest.mark.skipif(os.name != "posix", reason="The isolated parser guardian uses Linux resource limits")
def test_parser_guardian_actual_child_handles_plaintext():
    with TestClient(app) as client:
        response = client.post("/extract", content=b"Synthetic invoice source", headers={"Content-Type": "text/plain"})
        assert response.status_code == 200
        assert response.json() == {"text": "Synthetic invoice source", "used_ocr": False}
