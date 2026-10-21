import os
from pathlib import Path
import pytest

from docops.config import Settings
from docops.config import environment_secret
from docops.db import Database
from docops.extraction import FixtureInvoiceProvider
from docops.service import DocumentService
from docops.worker import Worker


@pytest.fixture
def service(tmp_path: Path):
    url = environment_secret("TEST_DOCUMENT_DATABASE_URL", "sqlite:///" + str(tmp_path / "test.db"))
    settings = Settings(
        database_url=url,
        database_schema=os.getenv("TEST_DOCUMENT_DATABASE_SCHEMA", "docops_test"),
        api_key="test-only-key",
        provider="fixture",
        worker_enabled=False,
        retry_base=0,
        redis_url="",
        parser_url="",
    )
    database = Database(url, settings.database_schema)
    database.initialize()
    if database.postgres:
        with database.transaction() as tx:
            for table in ["delivered_mail", "outbox", "audit", "invoice_registry", "ingestion_keys", "jobs", "inbox_cursor"]:
                tx.execute("DELETE FROM " + table)
    return DocumentService(database, settings)


@pytest.fixture
def worker(service):
    return Worker(service, FixtureInvoiceProvider())
