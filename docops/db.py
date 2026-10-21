"""Portable transactional store. PostgreSQL and SQLite share identical invariants."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import re
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_cursor (
 identity TEXT PRIMARY KEY, epoch INTEGER NOT NULL, last_uid INTEGER NOT NULL, rejected INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL, filename TEXT NOT NULL,
 source TEXT NOT NULL, source_bytes BLOB NOT NULL, content_type TEXT NOT NULL,
 content_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
 available_at REAL NOT NULL, lease_until REAL, claim_token TEXT,
 extracted_text TEXT, invoice_json TEXT, error TEXT, issues_json TEXT,
 created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(client_id, content_hash), UNIQUE(client_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state, available_at);
CREATE TABLE IF NOT EXISTS ingestion_keys (
 client_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 content_hash TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id),
 created_at REAL NOT NULL,
 PRIMARY KEY(client_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS ingestion_keys_job ON ingestion_keys(job_id);
CREATE TABLE IF NOT EXISTS invoice_registry (
 client_id TEXT NOT NULL, vendor TEXT NOT NULL, invoice_number TEXT NOT NULL,
 job_id TEXT NOT NULL REFERENCES jobs(id), invoice_json TEXT NOT NULL,
 PRIMARY KEY(client_id,vendor,invoice_number)
);
CREATE TABLE IF NOT EXISTS audit (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
 event TEXT NOT NULL, detail TEXT NOT NULL, trace_id TEXT NOT NULL, timestamp REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_job ON audit(job_id,timestamp);
CREATE TABLE IF NOT EXISTS outbox (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
 recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
 state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
 available_at REAL NOT NULL, lease_until REAL, claim_token TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS delivered_mail (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
 recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, delivered_at REAL NOT NULL
);
"""


class Session:
    def __init__(self, connection: Any, postgres: bool):
        self.connection = connection
        self.postgres = postgres

    def execute(self, sql: str, params: tuple = ()) -> Any:
        if self.postgres:
            sql = sql.replace("?", "%s")
        return self.connection.execute(sql, params)


class Database:
    def __init__(self, url: str, schema: str = "docops"):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise ValueError("Invalid database schema")
        self.schema = schema
        self.url = url.replace("postgresql+psycopg://", "postgresql://")
        self.postgres = self.url.startswith("postgresql://") or self.url.startswith("postgres://")

    def connect(self) -> Any:
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(
                self.url, row_factory=dict_row, connect_timeout=10, options=f"-c search_path={self.schema},public"
            )
        path = self.url.removeprefix("sqlite:///")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Eight concurrent durable ingestions may serialize several FULL-sync
        # commits on a slow local disk; bound contention without losing a source.
        connection = sqlite3.connect(path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def transaction(self, write: bool = True) -> Iterator[Session]:
        connection = self.connect()
        try:
            if not self.postgres:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield Session(connection, self.postgres)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.transaction() as tx:
            if self.postgres:
                # API and separate workers may initialize simultaneously.
                tx.execute("SELECT pg_advisory_xact_lock(hashtext(?))", ("docops-init:" + self.schema,))
                tx.execute("CREATE SCHEMA IF NOT EXISTS " + self.schema)
            schema = (
                SCHEMA.replace(" BLOB ", " BYTEA ").replace(" REAL", " DOUBLE PRECISION") if self.postgres else SCHEMA
            )
            for statement in schema.split(";"):
                if statement.strip():
                    tx.execute(statement)
            # Add receipts to an existing installation without rewriting its
            # jobs, source bytes, audit, processing state or acknowledgments.
            tx.execute(
                """INSERT INTO ingestion_keys(client_id,idempotency_key,content_hash,job_id,created_at)
                SELECT client_id,idempotency_key,content_hash,id,created_at FROM jobs WHERE 1=1
                ON CONFLICT(client_id,idempotency_key) DO NOTHING"""
            )

    def health(self) -> bool:
        with self.transaction(False) as tx:
            tx.execute("SELECT 1").fetchone()
        return True
