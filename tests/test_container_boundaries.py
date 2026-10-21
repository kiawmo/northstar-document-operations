from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread
import time
from uuid import uuid4

import pytest

from docops.config import Settings, environment_secret
from docops.extraction import FixtureInvoiceProvider
from docops.fixtures import invoice_text
from docops.models import ReviewAction
from docops.signals import QueueSignals
from docops.worker import Worker


def test_secret_file_precedes_environment_without_disclosure(tmp_path, monkeypatch):
    path = tmp_path / "key"
    path.write_text("file-private-value\n")
    monkeypatch.setenv("DOCUMENT_API_KEY", "different-environment-value")
    monkeypatch.setenv("DOCUMENT_API_KEY_FILE", str(path))
    assert environment_secret("DOCUMENT_API_KEY") == "file-private-value"


@pytest.mark.parametrize("content", ["", "x" * 8193], ids=["empty", "oversized"])
def test_configured_secret_file_fails_closed(tmp_path, monkeypatch, content):
    path = tmp_path / "key"
    path.write_text(content)
    monkeypatch.setenv("DOCUMENT_API_KEY_FILE", str(path))
    with pytest.raises(ValueError, match="secret file must contain"):
        Settings()


def test_split_api_worker_configuration(monkeypatch):
    monkeypatch.setenv("DOCUMENT_WORKER_ENABLED", "0")
    assert not Settings().worker_enabled
    monkeypatch.setenv("DOCUMENT_WORKER_ENABLED", "1")
    assert Settings().worker_enabled


def test_settings_representation_omits_private_values():
    settings = Settings(
        database_url="postgresql://user:private-db@host/db",
        api_key="private-api",
        gateway_key="private-gateway",
        redis_url="redis://:private-redis@host/0",
    )
    assert "private-" not in repr(settings)


def test_redis_disabled_retains_native_sql_queue(service, worker):
    assert service.signals.status() == {"configured": False, "status": "disabled", "sql_polling": True}
    job = service.ingest(invoice_text().encode(), "source.txt", "text/plain")
    worker.tick()
    assert service.get(job["id"])["state"] == "COMPLETED"


def test_redis_connection_refusal_cannot_lose_committed_source(service, caplog):
    service.signals = QueueSignals(Settings(redis_url="redis://:private-not-in-logs@127.0.0.1:1/0"))
    job = service.ingest(invoice_text().encode(), "source.txt", "text/plain")
    assert service.get(job["id"])["state"] == "QUEUED"
    assert service.signals.status()["status"] == "unavailable"
    Worker(service, FixtureInvoiceProvider()).tick()
    assert service.get(job["id"])["state"] == "COMPLETED"
    assert "private-not-in-logs" not in caplog.text


def test_redis_notification_happens_after_commit_and_skips_duplicates(service):
    notifications = []

    def recorded(job_id):
        notifications.append(service.get(job_id)["state"])

    service.signals.notify = recorded
    content = invoice_text().encode()
    service.ingest(content, "source.txt", "text/plain")
    service.ingest(content, "source.txt", "text/plain")
    assert notifications == ["QUEUED"]


def test_manual_retry_notifies_after_sql_commit(service, worker):
    job = service.ingest(b"unknown unstructured source", "source.txt", "text/plain")
    worker.tick()
    notifications = []
    service.signals.notify = lambda job_id: notifications.append(service.get(job_id)["state"])
    current = service.get(job["id"])
    service.review(
        job["id"], ReviewAction(action="retry", expected_revision=current["revision"], reason="Clean source retry")
    )
    assert notifications == ["QUEUED"]


def test_concurrent_initialization_preserves_existing_source(service):
    job = service.ingest(invoice_text().encode(), "source.txt", "text/plain")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: service.db.initialize(), range(4)))
    assert service.get(job["id"])["state"] == "QUEUED"


@pytest.fixture
def actual_signals():
    url = environment_secret("TEST_DOCUMENT_REDIS_URL")
    if not url:
        pytest.skip("Real Redis integration requires TEST_DOCUMENT_REDIS_URL or its secret file")
    settings = Settings(redis_url=url, redis_prefix="docops:test:" + uuid4().hex)
    signals = QueueSignals(settings)
    assert signals.status()["status"] == "ready", "Configured integration Redis must be available"
    yield signals
    keys = list(signals.client.scan_iter(settings.redis_prefix + ":*"))
    if keys:
        signals.client.delete(*keys)
    signals.client.close()


def test_real_redis_hint_is_consumed_without_owning_a_job(service, actual_signals):
    service.signals = actual_signals
    assert actual_signals.notify("nonexistent-advisory-id")
    assert actual_signals.status()["pending_wakeups"] == 1
    assert actual_signals.wait(0.2)
    assert not Worker(service, FixtureInvoiceProvider()).tick()
    assert service.snapshot()["total_jobs"] == 0


def test_real_redis_durable_ingestion_wakes_worker_and_exposes_heartbeat(service, actual_signals, tmp_path):
    service.signals = actual_signals
    service.settings.worker_heartbeat_file = str(tmp_path / "heartbeat")
    stop = Event()
    worker = Worker(service, FixtureInvoiceProvider())
    thread = Thread(target=worker.run, args=(stop,))
    thread.start()
    try:
        job = service.ingest(invoice_text("REDIS-WAKE").encode(), "source.txt", "text/plain")
        deadline = time.monotonic() + 10
        while service.get(job["id"])["state"] != "COMPLETED" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert service.get(job["id"])["state"] == "COMPLETED"
        assert actual_signals.status()["notifications"] == 1
        assert actual_signals.status()["active_workers"] == 1
        assert time.time() - float(Path(service.settings.worker_heartbeat_file).read_text()) < 10
    finally:
        stop.set()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_real_redis_hint_queue_is_bounded(actual_signals):
    for index in range(1003):
        assert actual_signals.notify(str(index))
    status = actual_signals.status()
    assert status["pending_wakeups"] == 1000
    assert status["notifications"] == 1003


def test_real_redis_heartbeat_remains_fresh_during_provider_call(service, actual_signals, tmp_path):
    entered, release, stop = Event(), Event(), Event()

    class WaitingProvider(FixtureInvoiceProvider):
        def extract(self, text, trace_id):
            entered.set()
            assert release.wait(15)
            return super().extract(text, trace_id)

    service.signals = actual_signals
    service.settings.worker_heartbeat_file = str(tmp_path / "heartbeat")
    job = service.ingest(invoice_text("BUSY-HEARTBEAT").encode(), "source.txt", "text/plain")
    thread = Thread(target=Worker(service, WaitingProvider()).run, args=(stop,))
    thread.start()
    try:
        assert entered.wait(5)
        initial = float(Path(service.settings.worker_heartbeat_file).read_text())
        time.sleep(6)
        assert float(Path(service.settings.worker_heartbeat_file).read_text()) > initial + 4
        assert service.get(job["id"])["state"] == "EXTRACTING"
        assert actual_signals.status()["active_workers"] == 1
    finally:
        release.set()
        stop.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
