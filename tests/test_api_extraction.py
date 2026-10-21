from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient

from docops.api import create_app
from docops.extraction import GatewayInvoiceProvider, PermanentExtractionError, TransientProviderError, extract_text
from docops.fixtures import build_fixtures, invoice_text
from docops.inbox import ingest_message


@pytest.fixture
def client(service):
    with TestClient(create_app(service.settings)) as client:
        yield client


def test_auth_health_security_headers(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/api/snapshot").status_code == 401
    response = client.get("/api/snapshot", headers={"X-API-Key": "test-only-key"})
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"


def test_api_upload_worker_source_end_to_end(client):
    headers = {"X-API-Key": "test-only-key", "Content-Type": "text/plain"}
    result = client.post("/api/documents/upload?filename=invoice.txt", headers=headers, content=invoice_text()).json()
    assert result["state"] == "QUEUED"
    client.app.state.worker.tick()
    client.app.state.worker.tick()
    response = client.get("/api/documents/" + result["id"], headers=headers)
    assert response.json()["state"] == "COMPLETED"
    assert len(response.json()["audit"]) >= 5
    assert client.get("/api/documents/" + result["id"] + "/source", headers=headers).text == invoice_text()
    assert "docops_jobs" in client.get("/metrics", headers=headers).text


@pytest.mark.parametrize("route", ["text", "upload"])
def test_api_existing_source_with_conflicting_delivery_key_returns_409(client, route):
    def submit(number, key):
        headers = {"X-API-Key": "test-only-key", "Idempotency-Key": key}
        if route == "text":
            return client.post(
                "/api/documents/text", headers=headers, json={"text": invoice_text("API-CROSS-" + number)}
            )
        return client.post(
            "/api/documents/upload?filename=invoice.txt",
            headers={**headers, "Content-Type": "text/plain"},
            content=invoice_text("API-CROSS-" + number),
        )

    first, second = submit("A", "api-key-a"), submit("B", "api-key-b")
    assert first.status_code == second.status_code == 202
    for number, key in [("A", "api-key-b"), ("B", "api-key-a")]:
        conflict = submit(number, key)
        assert conflict.status_code == 409
        assert conflict.json()["error"] == "Idempotency key already belongs to different content"
    for number, key, original in [("A", "api-key-a", first), ("B", "api-key-b", second)]:
        repeat = submit(number, key)
        assert repeat.status_code == 202
        assert repeat.json()["id"] == original.json()["id"] and repeat.json()["duplicate_delivery"]
    assert client.app.state.service.snapshot()["total_jobs"] == 2


@pytest.mark.parametrize("route", ["text", "upload"])
def test_api_duplicate_content_alias_key_is_reserved(client, route):
    def submit(number, key):
        headers = {"X-API-Key": "test-only-key", "Idempotency-Key": key}
        if route == "text":
            return client.post("/api/documents/text", headers=headers, json={"text": invoice_text(number)})
        return client.post(
            "/api/documents/upload?filename=invoice.txt",
            headers={**headers, "Content-Type": "text/plain"},
            content=invoice_text(number),
        )

    original = submit("API-ALIAS-A", "api-original")
    alias = submit("API-ALIAS-A", "api-alias")
    assert original.status_code == alias.status_code == 202
    assert alias.json()["id"] == original.json()["id"] and alias.json()["duplicate_delivery"]
    for _ in range(2):
        conflict = submit("API-ALIAS-B", "api-alias")
        assert conflict.status_code == 409
        assert conflict.json()["error"] == "Idempotency key already belongs to different content"
    replay = submit("API-ALIAS-A", "api-alias")
    assert replay.status_code == 202 and replay.json()["id"] == original.json()["id"]
    assert client.app.state.service.snapshot()["total_jobs"] == 1


def test_api_invalid_type_signature_and_size(client):
    headers = {"X-API-Key": "test-only-key", "Content-Type": "application/pdf"}
    assert (
        client.post("/api/documents/upload?filename=bad.pdf", headers=headers, content=b"not-a-pdf").status_code == 422
    )
    assert client.post("/api/documents/upload?filename=bad.exe", headers=headers, content=b"file").status_code == 422
    assert (
        client.post(
            "/api/documents/upload?filename=huge.pdf", headers=headers, content=b"x" * (5 * 1024 * 1024 + 1)
        ).status_code
        == 413
    )


def test_api_unknown_client_and_schema(client):
    headers = {"X-API-Key": "test-only-key", "Content-Type": "text/plain"}
    assert (
        client.post(
            "/api/documents/upload?filename=test.txt&client=../bad", headers=headers, content=invoice_text()
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/documents/text",
            headers={"X-API-Key": "test-only-key"},
            json={"text": "too short", "extra": "unexpected"},
        ).status_code
        == 422
    )


def test_api_review_invalid_state(client):
    headers = {"X-API-Key": "test-only-key"}
    job = client.post("/api/documents/text", headers=headers, json={"text": invoice_text()}).json()
    assert (
        client.post(
            "/api/documents/" + job["id"] + "/review",
            headers=headers,
            json={"action": "approve", "expected_revision": 0, "reason": "Premature"},
        ).status_code
        == 409
    )


def test_api_saved_review_returns_current_audit_for_ui(client):
    headers = {"X-API-Key": "test-only-key"}
    job = client.post(
        "/api/documents/text", headers=headers, json={"text": invoice_text("REVIEW-UI", total="260.00")}
    ).json()
    client.app.state.worker.tick()
    current = client.get("/api/documents/" + job["id"], headers=headers).json()
    current["invoice"]["total"] = "220.00"
    response = client.post(
        "/api/documents/" + job["id"] + "/review",
        headers=headers,
        json={
            "action": "save",
            "expected_revision": current["revision"],
            "reason": "Verified arithmetic against source",
            "invoice": current["invoice"],
        },
    )
    assert response.status_code == 200
    assert response.json()["audit"][-1]["event"] == "REVIEW_SAVE"
    assert response.json()["issues"] == []


def test_actual_pdf_and_scanned_ocr(service, tmp_path: Path):
    fixtures = build_fixtures(tmp_path)
    text, ocr_used = extract_text(fixtures["valid-pdf"].read_bytes(), "application/pdf", service.settings)
    assert not ocr_used and "NS-1042" in text
    text, ocr_used = extract_text(fixtures["scanned"].read_bytes(), "application/pdf", service.settings)
    assert ocr_used and "NS-1049" in text and "220.00" in text


def test_pdf_preview_is_authenticated_source_render(client, tmp_path: Path):
    fixtures = build_fixtures(tmp_path)
    headers = {"X-API-Key": "test-only-key", "Content-Type": "application/pdf"}
    job = client.post(
        "/api/documents/upload?filename=valid.pdf", headers=headers, content=fixtures["valid-pdf"].read_bytes()
    ).json()
    assert client.get("/api/documents/" + job["id"] + "/preview").status_code == 401
    response = client.get("/api/documents/" + job["id"] + "/preview", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    corrupt = client.post(
        "/api/documents/upload?filename=corrupted.pdf", headers=headers, content=b"%PDF-1.7\ninvalid"
    ).json()
    assert client.get("/api/documents/" + corrupt["id"] + "/preview", headers=headers).status_code == 422


def test_corrupted_pdf_is_reviewable(service):
    with pytest.raises(PermanentExtractionError, match="corrupted"):
        extract_text(b"%PDF-1.7\ninvalid", "application/pdf", service.settings)


def test_email_ingestion_and_duplicate_message(service, tmp_path):
    raw = build_fixtures(tmp_path)["email"].read_bytes()
    first = ingest_message(raw, service, "northstar")
    repeat = ingest_message(raw, service, "northstar")
    assert first[0]["source"] == "email" and repeat[0]["id"] == first[0]["id"]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_gateway_retryable_http_codes(service, status):
    provider = GatewayInvoiceProvider(service.settings, httpx.MockTransport(lambda _: httpx.Response(status)))
    with pytest.raises(TransientProviderError, match=str(status)):
        provider.extract(invoice_text(), "trace")


@pytest.mark.parametrize("kind", ["timeout", "connection", "invalid-json", "invalid-schema"])
def test_gateway_failure_boundaries(service, kind):
    def handler(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("hung", request=request)
        if kind == "connection":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(
            200, content=b"bad-json" if kind == "invalid-json" else b'{"data":{"vendor":"missing-fields"}}'
        )

    provider = GatewayInvoiceProvider(service.settings, httpx.MockTransport(handler))
    with pytest.raises(TransientProviderError if kind in {"timeout", "connection"} else PermanentExtractionError):
        provider.extract(invoice_text(), "trace")
