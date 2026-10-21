from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import time

import pytest

from docops.db import Database
from docops.extraction import FixtureInvoiceProvider, TransientProviderError
from docops.fixtures import invoice_text
from docops.models import Invoice, ReviewAction
from docops.service import Conflict, DocumentService
from docops.worker import Worker


def ingest(service, number="TEST-1", **kwargs):
    return service.ingest(invoice_text(number).encode(), "invoice.txt", "text/plain", **kwargs)


def test_content_idempotency_and_key_conflict(service):
    first = ingest(service, key="delivery-1")
    repeat = ingest(service, key="delivery-1")
    assert first["id"] == repeat["id"] and repeat["duplicate_delivery"]
    with pytest.raises(Conflict):
        ingest(service, "OTHER", key="delivery-1")
    assert service.snapshot()["total_jobs"] == 1


@pytest.mark.parametrize("order", [("A", "B"), ("B", "A")])
def test_existing_content_cannot_hide_a_conflicting_delivery_key(service, order):
    originals = {number: ingest(service, "CROSS-" + number, key="cross-key-" + number) for number in order}
    for content, conflicting_key in [("A", "B"), ("B", "A")]:
        with pytest.raises(Conflict, match="different content"):
            ingest(service, "CROSS-" + content, key="cross-key-" + conflicting_key)
    assert service.snapshot()["total_jobs"] == 2
    for original in originals.values():
        assert [entry["event"] for entry in service.get(original["id"])["audit"]] == ["RECEIVED"]
    for number in ("A", "B"):
        repeat = ingest(service, "CROSS-" + number, key="cross-key-" + number)
        assert repeat["id"] == originals[number]["id"] and repeat["duplicate_delivery"]


@pytest.mark.parametrize("distinct_keys", [False, True])
def test_concurrent_delivery_is_deduplicated(service, distinct_keys):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda index: ingest(service, key="concurrent-" + str(index) if distinct_keys else None),
                range(8),
            )
        )
    assert len({result["id"] for result in results}) == 1
    assert service.snapshot()["total_jobs"] == 1
    if distinct_keys:
        for index in range(8):
            with pytest.raises(Conflict, match="different content"):
                ingest(service, "OTHER-" + str(index), key="concurrent-" + str(index))
        with service.db.transaction(False) as tx:
            assert tx.execute("SELECT COUNT(*) AS total FROM ingestion_keys").fetchone()["total"] == 8


def test_duplicate_content_reserves_alias_key_across_restart(service, worker, monkeypatch):
    notifications = []
    monkeypatch.setattr(service.signals, "notify", notifications.append)
    first = ingest(service, "ALIAS-A", key="original-key")
    alias = ingest(service, "ALIAS-A", key="alias-key")
    assert alias["id"] == first["id"] and alias["duplicate_delivery"]
    assert notifications == [first["id"]]
    while worker.tick():
        pass
    before = service.snapshot()
    service.db.initialize()
    restarted = DocumentService(service.db, service.settings)
    with pytest.raises(Conflict, match="different content"):
        ingest(restarted, "ALIAS-B", key="alias-key")
    replay = ingest(restarted, "ALIAS-A", key="alias-key")
    assert replay["id"] == first["id"] and replay["duplicate_delivery"]
    after = restarted.snapshot()
    assert after["total_jobs"] == before["total_jobs"] == 1
    assert after["registered_invoices"] == before["registered_invoices"] == 1
    assert after["mailbox"] == before["mailbox"] and len(after["mailbox"]) == 1


def test_alias_delivery_keys_remain_client_scoped(service):
    first = ingest(service, "TENANT-A", client_id="client-a", key="original")
    alias = ingest(service, "TENANT-A", client_id="client-a", key="shared-alias")
    other = ingest(service, "TENANT-B", client_id="client-b", key="shared-alias")
    assert alias["id"] == first["id"] != other["id"]
    for tenant, number, original in [("client-a", "TENANT-A", first), ("client-b", "TENANT-B", other)]:
        repeat = ingest(service, number, client_id=tenant, key="shared-alias")
        assert repeat["id"] == original["id"] and repeat["duplicate_delivery"]
        with pytest.raises(Conflict, match="different content"):
            ingest(service, "OTHER", client_id=tenant, key="shared-alias")
    assert service.snapshot()["total_jobs"] == 2


@pytest.mark.parametrize("preexisting_sources", [False, True])
def test_concurrent_different_content_cannot_share_a_delivery_key(service, preexisting_sources):
    originals = {}
    if preexisting_sources:
        originals = {number: ingest(service, number, key="original-" + number) for number in ("RACE-A", "RACE-B")}
    gate = Barrier(2)

    def deliver(number):
        gate.wait(timeout=20)
        try:
            return number, ingest(service, number, key="shared-race-key")
        except Conflict:
            return number, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(deliver, ["RACE-A", "RACE-B"]))
    winners = [(number, result) for number, result in results if result is not None]
    assert len(winners) == 1
    number, winner = winners[0]
    assert winner["duplicate_delivery"] is preexisting_sources
    if preexisting_sources:
        assert winner["id"] == originals[number]["id"]
    loser = next(number for number, result in results if result is None)
    with pytest.raises(Conflict, match="different content"):
        ingest(service, loser, key="shared-race-key")
    replay = ingest(service, number, key="shared-race-key")
    assert replay["id"] == winner["id"] and replay["duplicate_delivery"]
    assert service.snapshot()["total_jobs"] == (2 if preexisting_sources else 1)
    with service.db.transaction(False) as tx:
        receipt = tx.execute("SELECT * FROM ingestion_keys WHERE idempotency_key=?", ("shared-race-key",)).fetchone()
        assert receipt["job_id"] == winner["id"] == replay["id"]


def test_existing_jobs_backfill_delivery_keys_without_rewriting_state(service, worker):
    original = ingest(service, "BACKFILL-A", key="legacy-source-key")
    while worker.tick():
        pass
    before = service.snapshot()
    audit_before = service.get(original["id"])["audit"]
    # Only the fixture's disposable database loses the additive table, simulating
    # an installation made before receipts existed. Production migrations never drop it.
    with service.db.transaction() as tx:
        tx.execute("DROP TABLE ingestion_keys")
    service.db.initialize()
    service.db.initialize()
    assert service.snapshot() == before
    assert service.get(original["id"])["audit"] == audit_before
    with service.db.transaction(False) as tx:
        receipts = tx.execute("SELECT * FROM ingestion_keys").fetchall()
        assert len(receipts) == 1 and receipts[0]["job_id"] == original["id"]
    with pytest.raises(Conflict, match="different content"):
        ingest(service, "BACKFILL-B", key="legacy-source-key")
    repeat = ingest(service, "BACKFILL-A", key="legacy-source-key")
    assert repeat["id"] == original["id"] and repeat["duplicate_delivery"]


def test_same_invoice_distinct_source_no_second_ack(service, worker):
    ingest(service)
    service.ingest((invoice_text("TEST-1") + "Forwarded copy\n").encode(), "copy.txt", "text/plain")
    while worker.tick():
        pass
    snapshot = service.snapshot()
    assert snapshot["states"] == {"COMPLETED": 1, "DUPLICATE": 1}
    assert len(snapshot["mailbox"]) == 1


def test_multi_client_namespace(service, worker):
    ingest(service, client_id="client-a")
    ingest(service, client_id="client-b")
    while worker.tick():
        pass
    assert service.snapshot()["registered_invoices"] == 2


def test_one_worker_claim_at_a_time(service):
    ingest(service)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: service.claim(), range(4)))
    assert sum(claim is not None for claim in claims) == 1


def test_lease_recovery_fences_original_worker(service):
    job = ingest(service)
    old = service.claim()
    with service.db.transaction() as tx:
        tx.execute("UPDATE jobs SET lease_until=? WHERE id=?", (time.time() - 1, job["id"]))
    new = service.claim()
    assert new["attempt"] == 2 and new["claim_token"] != old["claim_token"]
    with pytest.raises(Conflict):
        service.finish(old, FixtureInvoiceProvider().extract(invoice_text(), job["id"]))
    service.finish(new, FixtureInvoiceProvider().extract(invoice_text(), job["id"]))
    assert any(e["event"] == "LEASE_RECOVERED" for e in service.get(job["id"])["audit"])


def test_repeated_crashes_exhaust_retry_budget(service):
    job = ingest(service)
    for _ in range(3):
        assert service.claim()
        with service.db.transaction() as tx:
            tx.execute("UPDATE jobs SET lease_until=? WHERE id=?", (time.time() - 1, job["id"]))
    assert service.claim() is None
    assert service.get(job["id"])["state"] == "DEAD_LETTER"


def test_schema_failure_routes_review(service, worker):
    job = service.ingest(b"Unstructured unknown document data", "unknown.txt", "text/plain")
    worker.tick()
    assert service.get(job["id"])["state"] == "REVIEW_REQUIRED"


def test_retry_budget_and_manual_replay(service):
    class Broken(FixtureInvoiceProvider):
        def extract(self, text, trace_id):
            raise TransientProviderError("Simulated HTTP 500")

    job = ingest(service)
    worker = Worker(service, Broken())
    for _ in range(3):
        worker.tick()
    dead = service.get(job["id"])
    assert dead["state"] == "DEAD_LETTER" and dead["attempt"] == 3
    service.review(
        job["id"], ReviewAction(action="retry", expected_revision=dead["revision"], reason="Provider restored")
    )
    Worker(service, FixtureInvoiceProvider()).tick()
    assert service.get(job["id"])["state"] == "COMPLETED"


def test_review_edit_approve_and_optimistic_lock(service, worker):
    job = service.ingest(invoice_text("MATH", total="260.00").encode(), "math.txt", "text/plain")
    worker.tick()
    review = service.get(job["id"])
    with pytest.raises(Conflict, match="Correct business-rule"):
        service.review(
            job["id"], ReviewAction(action="approve", expected_revision=review["revision"], reason="Checked source")
        )
    fixed = dict(review["invoice"])
    fixed["total"] = "220.00"
    saved = service.review(
        job["id"],
        ReviewAction(
            action="save",
            expected_revision=review["revision"],
            reason="Corrected transcription",
            invoice=Invoice.model_validate(fixed),
        ),
    )
    with pytest.raises(Conflict, match="Review changed"):
        service.review(
            job["id"], ReviewAction(action="approve", expected_revision=review["revision"], reason="Stale reviewer")
        )
    approved = service.review(
        job["id"], ReviewAction(action="approve", expected_revision=saved["revision"], reason="Matched original source")
    )
    assert approved["state"] == "COMPLETED"
    assert len(service.snapshot()["outbox"]) == 1


def test_reject_creates_no_invoice_or_mail(service, worker):
    job = service.ingest(invoice_text("LOW", confidence="0.5").encode(), "low.txt", "text/plain")
    worker.tick()
    review = service.get(job["id"])
    service.review(
        job["id"], ReviewAction(action="reject", expected_revision=review["revision"], reason="Vendor needs correction")
    )
    assert service.snapshot()["registered_invoices"] == 0
    assert service.snapshot()["outbox"] == []


def test_outbox_failure_does_not_rollback_invoice(service, worker):
    job = ingest(service)
    worker.tick()
    assert service.deliver_one(fail=True)
    assert service.get(job["id"])["state"] == "COMPLETED"
    assert service.snapshot()["outbox"][0]["state"] == "RETRYING"
    service.deliver_one()
    assert not service.deliver_one()
    assert len(service.snapshot()["mailbox"]) == 1


def test_database_outage_persists_then_recovers(service, worker):
    job = ingest(service)
    original = service.db
    service.db = Database("postgresql://unavailable@127.0.0.1:55431/unavailable")
    with pytest.raises(__import__("psycopg").OperationalError):
        worker.tick()
    service.db = original
    worker.tick()
    assert service.get(job["id"])["state"] == "COMPLETED"


def test_transaction_rolls_back_register_and_outbox(service):
    job = ingest(service)
    original = service._register

    def injected(tx, current, invoice):
        original(tx, current, invoice)
        raise RuntimeError("Crash before transaction commit")

    service._register = injected
    claim = service.claim()
    with pytest.raises(RuntimeError):
        service.finish(claim, FixtureInvoiceProvider().extract(invoice_text(), job["id"]))
    assert service.snapshot()["registered_invoices"] == 0 and service.snapshot()["outbox"] == []


def test_database_restart_retains_source_and_queue(service):
    job = ingest(service)
    restarted = DocumentService(Database(service.db.url, service.settings.database_schema), service.settings)
    restarted.db.initialize()
    Worker(restarted, FixtureInvoiceProvider()).tick()
    assert restarted.get(job["id"])["state"] == "COMPLETED"
