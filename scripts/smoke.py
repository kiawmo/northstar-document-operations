"""Exercise an actual deployed API without printing credentials or document bytes."""

import argparse
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("DOCOPS_URL", "http://127.0.0.1:18113"))
    parser.add_argument("--live", action="store_true", help="Require gateway production extraction mode")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    key = (root / ".secrets/api_key").read_text().strip()
    base = args.url.rstrip("/")

    def request(path, payload=None, extra=None, auth=True):
        headers = {"X-API-Key": key} if auth else {}
        if extra:
            headers.update(extra)
        data = json.dumps(payload).encode() if payload is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        with urlopen(Request(base + path, data=data, headers=headers), timeout=30) as response:
            body = response.read(6 * 1024**2)
            return json.loads(body) if "json" in response.headers.get("Content-Type", "") else body

    ready = request("/ready", auth=False)
    assert ready["status"] == "ready"
    if args.live:
        assert ready["llm_provider"] == "gateway", "Live validation must use gateway mode"
    try:
        request("/api/snapshot", auth=False)
        raise AssertionError("Unauthenticated business read succeeded")
    except HTTPError as error:
        assert error.code == 401
    number = "NS-" + uuid4().hex[:12].upper()
    text = f"""Northstar Office Supply - Synthetic Invoice
Vendor: Northstar Office Supply
Invoice: {number}
Invoice date: 2026-10-01
Due date: 2026-10-31
Currency: USD
Subtotal: 200.00
Tax: 20.00
Total: 220.00
PO: PO-2026-17
Confidence: 0.97
Item: Office supplies | 2 | 100.00 | 200.00
"""
    payload = {"text": text, "filename": "smoke.txt", "client_id": "smoke", "source": "api"}
    delivery_key = "smoke-" + uuid4().hex
    job = request("/api/documents/text", payload, {"Idempotency-Key": delivery_key})
    repeated = request("/api/documents/text", payload, {"Idempotency-Key": delivery_key})
    assert repeated["id"] == job["id"] and repeated["duplicate_delivery"]
    deadline = time.monotonic() + (300 if args.live else 45)
    while time.monotonic() < deadline:
        result = request("/api/documents/" + job["id"])
        if result["state"] in {"COMPLETED", "REVIEW_REQUIRED", "DUPLICATE", "REJECTED"}:
            break
        time.sleep(0.25)
    assert result["state"] == "COMPLETED", "Synthetic invoice did not complete"
    assert result["invoice"]["invoice_number"] == number
    assert result["invoice"]["total"] == "220.00"
    source = request("/api/documents/" + job["id"] + "/source")
    assert source == text.encode()
    print(json.dumps({"checks": ["readiness", "authentication", "durable intake", "idempotency", "worker extraction", "decimal validation", "source retrieval"], "provider_mode": ready["llm_provider"], "result": "passed"}, indent=2))


if __name__ == "__main__":
    main()
