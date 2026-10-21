"""Run a synthetic invoice contract against an explicitly selected gateway URL."""

import argparse
import json
import os
from pathlib import Path
import sys

import httpx

# Support direct execution from this complete source checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from docops.fixtures import invoice_text  # noqa: E402
from docops.models import Invoice  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Your private gateway base URL")
    args = parser.parse_args()
    key = os.environ.get("GATEWAY_API_KEY", "")
    if not key:
        raise SystemExit("Set GATEWAY_API_KEY privately before invoking the HTTP gateway")
    with httpx.Client(timeout=150, follow_redirects=False) as client:
        response = client.post(
            args.url.rstrip("/") + "/generate",
            headers={"Authorization": "Bearer " + key},
            json={
                "task": "invoice_extraction",
                "instruction": "Extract the supplied fictional invoice; use decimal strings and ISO dates.",
                "input": {"text": invoice_text()},
                "schema": Invoice.model_json_schema(mode="serialization"),
            },
        )
    response.raise_for_status()
    data = response.json()
    invoice = Invoice.model_validate(data["data"])
    assert invoice.invoice_number == "NS-1042" and not invoice.business_errors()
    print(json.dumps({"result": "passed", "synthetic_input": True, "provider": data["provider"]}))


if __name__ == "__main__":
    main()
