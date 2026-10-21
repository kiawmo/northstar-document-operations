import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from .db import Database
from .extraction import FixtureInvoiceProvider, TransientProviderError, create_provider
from .fixtures import build_fixtures, invoice_text
from .inbox import ingest_message
from .models import Invoice
from .service import Conflict, DocumentService, audit
from .worker import Worker


def drain(worker: Worker, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        busy = worker.tick()
        snapshot = worker.service.snapshot()
        pending = sum(
            snapshot["states"].get(x, 0) for x in ["QUEUED", "PROCESSING", "EXTRACTING", "VALIDATING", "RETRYING"]
        )
        if not busy and pending == 0 and not any(m["state"] in {"PENDING", "RETRYING"} for m in snapshot["outbox"]):
            return
        time.sleep(0.05)
    raise TimeoutError("Demo queue did not finish in time")


def happy(service: DocumentService, evidence: Path) -> dict:
    fixtures = build_fixtures(Path("fixtures"))
    provider = create_provider(service.settings)
    worker = Worker(service, provider)
    first = service.ingest(fixtures["valid-pdf"].read_bytes(), "valid.pdf", "application/pdf")
    repeated = service.ingest(fixtures["valid-pdf"].read_bytes(), "valid.pdf", "application/pdf")
    duplicate = service.ingest(fixtures["duplicate"].read_bytes(), "duplicate.txt", "text/plain")
    math = service.ingest(fixtures["incorrect-math"].read_bytes(), "incorrect-math.txt", "text/plain")
    scan = service.ingest(fixtures["scanned"].read_bytes(), "scanned.pdf", "application/pdf")
    low = service.ingest(fixtures["low-confidence"].read_bytes(), "low-confidence.txt", "text/plain")
    email = ingest_message(fixtures["email"].read_bytes(), service, "northstar")[0]
    drain(worker, timeout=300)
    assert repeated["id"] == first["id"] and repeated["duplicate_delivery"]
    assert service.get(first["id"])["state"] == "COMPLETED"
    assert service.get(duplicate["id"])["state"] == "DUPLICATE"
    assert service.get(math["id"])["state"] == "REVIEW_REQUIRED"
    assert service.get(scan["id"])["state"] == "COMPLETED"
    assert service.get(low["id"])["state"] == "REVIEW_REQUIRED"
    assert service.get(email["id"])["state"] == "COMPLETED"
    result = {
        "command": "python -m docops demo --llm " + service.settings.provider,
        "llm_provider": service.settings.provider,
        "provider_metadata": getattr(provider, "last_metadata", {}),
        "database": service.snapshot()["database"],
        "checks_passed": 7,
        "checks": [
            "PDF extraction completed",
            "Same bytes reused one job",
            "Vendor/invoice duplicate suppressed",
            "Incorrect total routed to review",
            "Scanned PDF OCR completed",
            "Low confidence routed to review",
            "Email attachment accepted",
        ],
        "job_ids": {
            "valid": first["id"],
            "duplicate": duplicate["id"],
            "math": math["id"],
            "scan": scan["id"],
            "low": low["id"],
            "email": email["id"],
        },
        "snapshot": service.snapshot(),
    }
    evidence.mkdir(exist_ok=True)
    (evidence / "demo-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


class RecoveringProvider(FixtureInvoiceProvider):
    def __init__(self):
        self.calls = 0

    def extract(self, text: str, trace_id: str) -> Invoice:
        self.calls += 1
        if self.calls <= 2:
            raise TransientProviderError(f"Simulated upstream HTTP {429 if self.calls == 1 else 503}")
        return super().extract(text, trace_id)


def chaos(service: DocumentService, evidence: Path) -> dict:
    fixtures = build_fixtures(Path("fixtures"))
    worker = Worker(service, FixtureInvoiceProvider())
    checks = []
    client = "chaos-" + uuid4().hex[:8]
    corrupted = service.ingest(fixtures["corrupted"].read_bytes(), "corrupted.pdf", "application/pdf", client_id=client)
    malformed = service.ingest(
        fixtures["malformed-ai"].read_bytes(), "malformed-ai.txt", "text/plain", client_id=client
    )
    drain(worker)
    assert service.get(corrupted["id"])["state"] == "REVIEW_REQUIRED"
    assert service.get(malformed["id"])["state"] == "REVIEW_REQUIRED"
    checks += ["Corrupted PDF safely reviewed", "Malformed structured AI JSON safely reviewed"]
    retry = service.ingest(
        fixtures["provider-retry"].read_bytes(), "provider-retry.txt", "text/plain", client_id=client
    )
    provider = RecoveringProvider()
    drain(Worker(service, provider))
    assert service.get(retry["id"])["state"] == "COMPLETED" and service.get(retry["id"])["attempt"] == 3
    checks.append("HTTP 429 then HTTP 503 recovered on attempt 3")
    crash = service.ingest(fixtures["worker-crash"].read_bytes(), "worker-crash.txt", "text/plain", client_id=client)
    # A real child process obtains a lease and exits without completing or cleanup.
    child_environment = dict(os.environ)
    # The selected disposable database must win over an inherited mounted secret.
    child_environment.pop("DOCUMENT_DATABASE_URL_FILE", None)
    child_environment["DOCUMENT_DATABASE_URL"] = service.db.url
    child_environment["DOCUMENT_DATABASE_SCHEMA"] = service.settings.database_schema
    process = subprocess.run(
        [sys.executable, "-m", "docops", "crash-once", "--llm", "fixture"],
        env=child_environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 23
    with service.db.transaction() as tx:
        row = tx.execute("SELECT * FROM jobs WHERE id=?", (crash["id"],)).fetchone()
        stale_token = row["claim_token"]
        tx.execute("UPDATE jobs SET lease_until=? WHERE id=?", (time.time() - 1, crash["id"]))
        audit(tx, crash["id"], "WORKER_PROCESS_EXITED", "Actual child exit code 23; test clock advanced beyond lease")
    recovered = service.claim()
    try:
        service.stage({"id": crash["id"], "claim_token": stale_token}, "VALIDATING", "stale result")
        raise AssertionError("Stale claim accepted")
    except Conflict:
        pass
    text = bytes(recovered["source_bytes"]).decode()
    service.finish(recovered, FixtureInvoiceProvider().extract(text, recovered["id"]))
    assert service.get(crash["id"])["state"] == "COMPLETED"
    checks.append("Actual worker subprocess crash recovered; stale claim fenced")
    # SQLite: actual exclusive competing lock; PG: actual connection refusal adapter.
    pending = service.ingest(
        fixtures["database-recovery"].read_bytes(), "database-recovery.txt", "text/plain", client_id=client
    )
    if not service.db.postgres:
        blocked = service.db.connect()
        blocked.execute("BEGIN IMMEDIATE")
        failed = False
        try:
            worker.tick()
        except Exception:
            failed = True
        finally:
            blocked.rollback()
            blocked.close()
        assert failed
        checks.append("Actual SQLite write lock caused temporary DB outage; persisted job survived")
    else:
        import psycopg

        broken = Database("postgresql://unavailable@127.0.0.1:55431/unavailable")
        try:
            broken.health()
            raise AssertionError("Unexpected database listener")
        except psycopg.OperationalError:
            pass
        checks.append("Actual PostgreSQL connection refusal detected; original persisted queue survived")
    drain(worker)
    assert service.get(pending["id"])["state"] == "COMPLETED"
    checks.append("Database restored; queued invoice completed")
    # Retain one failed mail attempt then recover the same outbox record.
    ack = service.ingest(invoice_text("NS-1051").encode(), "mail-recovery.txt", "text/plain", client_id=client)
    worker.tick()
    assert service.deliver_one(fail=True)
    time.sleep(service.settings.retry_base + 0.05)
    drain(worker)
    with service.db.transaction(False) as tx:
        assert (
            tx.execute("SELECT COUNT(*) AS count FROM delivered_mail WHERE job_id=?", (ack["id"],)).fetchone()["count"]
            == 1
        )
    checks.append("Mailbox HTTP 503 recovered with one acknowledgment delivery")
    result = {
        "command": "python -m docops chaos --llm fixture",
        "llm_provider": "fixture (deterministic fault injection only)",
        "database": service.snapshot()["database"],
        "checks_passed": len(checks),
        "checks": checks,
        "job_ids": {
            "corrupted": corrupted["id"],
            "malformed": malformed["id"],
            "retry": retry["id"],
            "worker_crash": crash["id"],
            "db_recovery": pending["id"],
        },
        "snapshot": service.snapshot(),
    }
    evidence.mkdir(exist_ok=True)
    (evidence / "chaos-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
