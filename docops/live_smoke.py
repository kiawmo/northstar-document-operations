"""Live gateway evidence, explicitly separate from deterministic test fixtures."""

import json
from pathlib import Path
from uuid import uuid4

from .extraction import GatewayInvoiceProvider
from .fixtures import invoice_text
from .service import DocumentService
from .worker import Worker


def gateway_smoke(service: DocumentService) -> dict:
    number = "NS-CODEX-" + uuid4().hex[:8].upper()
    provider = GatewayInvoiceProvider(service.settings)
    job = service.ingest(invoice_text(number).encode(), "codex-invoice.txt", "text/plain", client_id="codex-smoke")
    worker = Worker(service, provider)
    worker.tick()
    result = service.get(job["id"])
    if result["state"] != "COMPLETED":
        raise RuntimeError("Live gateway invoice did not complete: " + str(result["issues"]))
    assert result["invoice"]["invoice_number"] == number
    assert result["invoice"]["total"] == "220.00"
    assert provider.last_metadata["provider"] == "codex_cli"
    service.deliver_one()
    evidence = {
        "command": "python -m docops gateway-smoke",
        "provider": provider.last_metadata,
        "checks_passed": 4,
        "checks": [
            "Live authenticated Codex gateway returned strict invoice schema",
            "Invoice number came from actual source",
            "Independent Decimal rules passed",
            "Transactional invoice and acknowledgment completed",
        ],
        "job": service.get(job["id"]),
        "database": service.snapshot()["database"],
    }
    Path("evidence").mkdir(exist_ok=True)
    Path("evidence/gateway-smoke.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return {"provider": provider.last_metadata, "state": result["state"], "checks_passed": 4}
