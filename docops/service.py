from hashlib import sha256
import json
from pathlib import PurePath
import time
from typing import Any
from uuid import uuid4

from .config import Settings
from .db import Database, Session
from .models import Invoice, ReviewAction
from .signals import QueueSignals


class Conflict(Exception):
    pass


def audit(tx: Session, job_id: str, event: str, detail: str) -> None:
    tx.execute(
        "INSERT INTO audit(id,job_id,event,detail,trace_id,timestamp) VALUES(?,?,?,?,?,?)",
        (str(uuid4()), job_id, event, detail, job_id, time.time()),
    )


def public_job(row: Any) -> dict:
    job = dict(row)
    job.pop("source_bytes", None)
    job.pop("claim_token", None)
    job.pop("idempotency_key", None)
    for field, target in [("invoice_json", "invoice"), ("issues_json", "issues")]:
        value = job.pop(field)
        job[target] = json.loads(value) if value else None
    return job


class DocumentService:
    def __init__(self, database: Database, settings: Settings):
        self.db = database
        self.settings = settings
        from .mail import profile

        self.mail_profile = profile(settings.mail_profile_file)
        self.signals = QueueSignals(settings)

    def ingest(self, *args, **kwargs) -> dict:
        result = self._ingest(*args, **kwargs)
        if not result["duplicate_delivery"]:
            # Notification happens only after the durable SQL transaction commits.
            self.signals.notify(result["id"])
        return result

    def _ingest(
        self,
        content: bytes,
        filename: str,
        content_type: str,
        client_id: str = "northstar",
        source: str = "upload",
        key: str | None = None,
    ) -> dict:
        if not content or len(content) > self.settings.max_upload_bytes:
            raise ValueError("File must contain 1 byte to 5 MB")
        safe_name = PurePath(filename.replace("\\", "/")).name[:160]
        extension = PurePath(safe_name).suffix.lower()
        types = {
            ".txt": "text/plain",
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }
        if extension not in types or content_type != types[extension]:
            raise ValueError("Supported source types: PDF, UTF-8 text, PNG, JPEG")
        if extension == ".pdf" and not content.startswith(b"%PDF-"):
            raise ValueError("PDF signature is invalid")
        if extension == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("PNG signature is invalid")
        if extension in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8"):
            raise ValueError("JPEG signature is invalid")
        content_hash = sha256(content).hexdigest()
        key = key or content_hash
        if len(key) > 160:
            raise ValueError("Idempotency key exceeds 160 characters")
        job_id, now = str(uuid4()), time.time()
        with self.db.transaction() as tx:
            inserted = tx.execute(
                """INSERT INTO jobs(id,client_id,filename,source,source_bytes,content_type,content_hash,
                idempotency_key,state,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT DO NOTHING""",
                (
                    job_id,
                    client_id,
                    safe_name,
                    source,
                    content,
                    content_type,
                    content_hash,
                    key,
                    "QUEUED",
                    now,
                    now,
                    now,
                ),
            ).rowcount
            if not inserted:
                # A retained delivery key takes precedence over content deduplication.
                # Otherwise another source match can hide a key bound to different bytes.
                row = tx.execute(
                    "SELECT * FROM jobs WHERE client_id=? AND idempotency_key=?",
                    (client_id, key),
                ).fetchone()
                if row is not None and row["content_hash"] != content_hash:
                    raise Conflict("Idempotency key already belongs to different content")
                if row is None:
                    row = tx.execute(
                        "SELECT * FROM jobs WHERE client_id=? AND content_hash=?",
                        (client_id, content_hash),
                    ).fetchone()
                if row is None:
                    raise RuntimeError("Conflicting delivery has no matching source")
            else:
                row = tx.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            # Even a content duplicate reserves its caller's delivery key.
            # The unique receipt serializes concurrent aliases; a conflict
            # rolls back any new source and audit in this same transaction.
            tx.execute(
                """INSERT INTO ingestion_keys(client_id,idempotency_key,content_hash,job_id,created_at)
                VALUES(?,?,?,?,?) ON CONFLICT(client_id,idempotency_key) DO NOTHING""",
                (client_id, key, content_hash, row["id"], now),
            )
            receipt = tx.execute(
                "SELECT * FROM ingestion_keys WHERE client_id=? AND idempotency_key=?", (client_id, key)
            ).fetchone()
            if receipt["content_hash"] != content_hash:
                raise Conflict("Idempotency key already belongs to different content")
            if receipt["job_id"] != row["id"]:
                raise RuntimeError("Delivery key and source identity disagree")
            if inserted:
                audit(tx, job_id, "RECEIVED", f"{source}: {safe_name}; persisted {len(content)} bytes")
            else:
                audit(
                    tx, row["id"], "DUPLICATE_DELIVERY_IGNORED", "Existing source reused; no new job or acknowledgment"
                )
            result = public_job(row)
            result["duplicate_delivery"] = not inserted
            return result

    def claim(self) -> dict | None:
        now = time.time()
        with self.db.transaction() as tx:
            stale = tx.execute(
                "SELECT id FROM jobs WHERE state IN ('PROCESSING','EXTRACTING','VALIDATING') AND lease_until<?", (now,)
            ).fetchall()
            for row in stale:
                current = tx.execute("SELECT attempt FROM jobs WHERE id=?", (row["id"],)).fetchone()
                state = "DEAD_LETTER" if current["attempt"] >= self.settings.max_attempts else "QUEUED"
                result = tx.execute(
                    "UPDATE jobs SET state=?,claim_token=NULL,lease_until=NULL,available_at=?,updated_at=? WHERE id=? AND lease_until<?",
                    (state, now, now, row["id"], now),
                )
                if result.rowcount:
                    audit(
                        tx,
                        row["id"],
                        "LEASE_RECOVERED" if state == "QUEUED" else "DEAD_LETTER",
                        "Worker lease expired; retry budget exhausted"
                        if state == "DEAD_LETTER"
                        else "Worker lease expired; durable source requeued with a new claim token",
                    )
            lock = " FOR UPDATE SKIP LOCKED" if tx.postgres else ""
            row = tx.execute(
                "SELECT * FROM jobs WHERE state IN ('QUEUED','RETRYING') AND available_at<=? ORDER BY created_at LIMIT 1"
                + lock,
                (now,),
            ).fetchone()
            if row is None:
                return None
            token = str(uuid4())
            tx.execute(
                "UPDATE jobs SET state='PROCESSING',attempt=attempt+1,claim_token=?,lease_until=?,updated_at=? WHERE id=?",
                (token, now + self.settings.lease_seconds, now, row["id"]),
            )
            audit(tx, row["id"], "WORKER_CLAIMED", f"Attempt {row['attempt'] + 1}; durable lease acquired")
            return dict(tx.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def stage(self, job: dict, state: str, detail: str, text: str | None = None) -> None:
        with self.db.transaction() as tx:
            result = tx.execute(
                "UPDATE jobs SET state=?,extracted_text=COALESCE(?,extracted_text),updated_at=? WHERE id=? AND claim_token=? AND lease_until>?",
                (state, text, time.time(), job["id"], job["claim_token"], time.time()),
            )
            if not result.rowcount:
                raise Conflict("Worker no longer owns this job lease")
            audit(tx, job["id"], state, detail)

    def finish(self, job: dict, invoice: Invoice) -> str:
        issues = invoice.business_errors()
        with self.db.transaction() as tx:
            lock = " FOR UPDATE" if tx.postgres else ""
            current = tx.execute(
                "SELECT * FROM jobs WHERE id=? AND claim_token=? AND lease_until>?" + lock,
                (job["id"], job["claim_token"], time.time()),
            ).fetchone()
            if current is None:
                raise Conflict("Stale worker result refused")
            state = "REVIEW_REQUIRED" if issues else self._register(tx, current, invoice)
            tx.execute(
                "UPDATE jobs SET state=?,invoice_json=?,issues_json=?,error=NULL,claim_token=NULL,lease_until=NULL,revision=revision+1,updated_at=? WHERE id=?",
                (state, invoice.model_dump_json(), json.dumps(issues), time.time(), job["id"]),
            )
            audit(
                tx,
                job["id"],
                state,
                "; ".join(issues)
                if issues
                else "Business rules passed; atomic invoice register and acknowledgment outbox",
            )
            return state

    def _register(self, tx: Session, job: Any, invoice: Invoice) -> str:
        vendor = " ".join(invoice.vendor.casefold().split())
        number = invoice.invoice_number.strip().casefold()
        inserted = tx.execute(
            "INSERT INTO invoice_registry(client_id,vendor,invoice_number,job_id,invoice_json) VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING",
            (job["client_id"], vendor, number, job["id"], invoice.model_dump_json()),
        ).rowcount
        if not inserted:
            return "DUPLICATE"
        tx.execute(
            "INSERT INTO outbox(id,job_id,recipient,subject,body,state,available_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (
                str(uuid4()),
                job["id"],
                self.mail_profile["smtp"]["recipient"]
                if "smtp" in self.mail_profile and job["client_id"] == self.mail_profile["client_id"]
                else "accounts@northstar.example",
                f"Invoice {invoice.invoice_number} accepted",
                f"Accepted {invoice.currency} {invoice.total}. Reference {job['id']}.",
                "PENDING",
                time.time(),
            ),
        )
        return "COMPLETED"

    def fail(self, job: dict, error: str, permanent: bool = False) -> None:
        attempt = job["attempt"]
        state = (
            "REVIEW_REQUIRED" if permanent else ("DEAD_LETTER" if attempt >= self.settings.max_attempts else "RETRYING")
        )
        # Stable per-job jitter keeps demonstration reproducible while desynchronizing workers.
        jitter = int(sha256(job["id"].encode()).hexdigest()[:4], 16) / 65535 * self.settings.retry_base
        delay = self.settings.retry_base * 2 ** (attempt - 1) + jitter
        with self.db.transaction() as tx:
            result = tx.execute(
                "UPDATE jobs SET state=?,error=?,issues_json=?,available_at=?,claim_token=NULL,lease_until=NULL,updated_at=?,revision=revision+1 WHERE id=? AND claim_token=? AND lease_until>?",
                (
                    state,
                    error[:500],
                    json.dumps([error[:500]]),
                    time.time() + delay,
                    time.time(),
                    job["id"],
                    job["claim_token"],
                    time.time(),
                ),
            )
            if result.rowcount:
                audit(
                    tx,
                    job["id"],
                    state,
                    f"{error[:300]}; {'human action required' if permanent else f'retry delay {delay:.2f}s; attempt {attempt}'}",
                )

    def review(self, job_id: str, action: ReviewAction) -> dict:
        result = self._review(job_id, action)
        if result["state"] == "QUEUED":
            self.signals.notify(job_id)
        return result

    def _review(self, job_id: str, action: ReviewAction) -> dict:
        with self.db.transaction() as tx:
            lock = " FOR UPDATE" if tx.postgres else ""
            row = tx.execute("SELECT * FROM jobs WHERE id=?" + lock, (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["revision"] != action.expected_revision:
                raise Conflict("Review changed; reload before applying this action")
            if row["state"] not in {"REVIEW_REQUIRED", "DEAD_LETTER", "REJECTED"}:
                raise Conflict("Only review, rejected, or dead-letter jobs can be changed")
            invoice = action.invoice or (
                Invoice.model_validate_json(row["invoice_json"]) if row["invoice_json"] else None
            )
            state = row["state"]
            issues = invoice.business_errors() if invoice else []
            if action.action == "approve":
                if invoice is None:
                    raise Conflict("Extract or enter a valid invoice before approval")
                blocking = [x for x in issues if not x.startswith("Extraction confidence")]
                if blocking:
                    raise Conflict("Correct business-rule errors before approval: " + "; ".join(blocking))
                state = self._register(tx, row, invoice)
            elif action.action == "reject":
                state = "REJECTED"
            elif action.action == "retry":
                state = "QUEUED"
            tx.execute(
                "UPDATE jobs SET state=?,invoice_json=?,issues_json=?,error=NULL,attempt=CASE WHEN ?='retry' THEN 0 ELSE attempt END,available_at=?,revision=revision+1,updated_at=? WHERE id=?",
                (
                    state,
                    invoice.model_dump_json() if invoice else None,
                    json.dumps(issues),
                    action.action,
                    time.time(),
                    time.time(),
                    job_id,
                ),
            )
            audit(tx, job_id, "REVIEW_" + action.action.upper(), action.reason)
            result = public_job(tx.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            result["audit"] = [
                dict(x)
                for x in tx.execute("SELECT * FROM audit WHERE job_id=? ORDER BY timestamp", (job_id,)).fetchall()
            ]
            return result

    def deliver_one(self, fail: bool = False) -> bool:
        if "smtp" in self.mail_profile:
            return self.deliver_remote(fail)
        now = time.time()
        with self.db.transaction() as tx:
            lock = " FOR UPDATE SKIP LOCKED" if tx.postgres else ""
            row = tx.execute(
                "SELECT * FROM outbox WHERE state IN ('PENDING','RETRYING') AND available_at<=? ORDER BY available_at LIMIT 1"
                + lock,
                (now,),
            ).fetchone()
            if row is None:
                return False
            attempt = row["attempt"] + 1
            if fail:
                state = "DEAD_LETTER" if attempt >= self.settings.max_attempts else "RETRYING"
                tx.execute(
                    "UPDATE outbox SET state=?,attempt=?,available_at=?,error=? WHERE id=?",
                    (
                        state,
                        attempt,
                        now + self.settings.retry_base * 2 ** (attempt - 1),
                        "Simulated mailbox HTTP 503",
                        row["id"],
                    ),
                )
                audit(tx, row["job_id"], "ACK_RETRY", f"Mailbox unavailable; attempt {attempt}; outbox retained")
                return True
            tx.execute(
                "INSERT INTO delivered_mail(id,job_id,recipient,subject,body,delivered_at) VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                (row["id"], row["job_id"], row["recipient"], row["subject"], row["body"], now),
            )
            tx.execute("UPDATE outbox SET state='SENT',attempt=?,error=NULL WHERE id=?", (attempt, row["id"]))
            audit(
                tx,
                row["job_id"],
                "ACK_DELIVERED",
                "Acknowledgment committed to local mailbox; unique job delivery constraint",
            )
            return True

    def deliver_remote(self, fail: bool = False) -> bool:
        from .mail import MailFailure, send_smtp

        now = time.time()
        with self.db.transaction() as tx:
            stale = tx.execute("SELECT * FROM outbox WHERE state='DISPATCHING' AND lease_until<?", (now,)).fetchall()
            for row in stale:
                tx.execute(
                    "UPDATE outbox SET state='UNCERTAIN',error='SMTP_OUTCOME_UNCERTAIN' WHERE id=? AND state='DISPATCHING' AND lease_until<?",
                    (row["id"], now),
                )
                audit(tx, row["job_id"], "ACK_UNCERTAIN", "Expired send reservation; operator reconciliation required")
            lock = " FOR UPDATE SKIP LOCKED" if tx.postgres else ""
            row = tx.execute(
                """SELECT outbox.* FROM outbox JOIN jobs ON jobs.id=outbox.job_id
                WHERE outbox.state IN ('PENDING','RETRYING') AND outbox.available_at<=? AND jobs.client_id=?
                ORDER BY outbox.available_at LIMIT 1"""
                + lock,
                (now, self.mail_profile["client_id"]),
            ).fetchone()
            if row is None:
                return False
            row = dict(row)
            row["attempt"] += 1
            token = str(uuid4())
            tx.execute(
                "UPDATE outbox SET state='DISPATCHING',attempt=?,claim_token=?,lease_until=? WHERE id=?",
                (row["attempt"], token, now + 180, row["id"]),
            )
            audit(tx, row["job_id"], "ACK_DISPATCHING", "Durable send reservation acquired")
        state, error, receipt = "SENT", None, None
        try:
            if fail:
                raise MailFailure("SIMULATED_SMTP_503", True)
            if row["recipient"] != self.mail_profile["smtp"]["recipient"]:
                raise MailFailure("MAIL_ACCOUNT_CUTOVER_REVIEW_REQUIRED", False)
            receipt = send_smtp(self.mail_profile["smtp"], row)
        except MailFailure as exc:
            state = (
                "UNCERTAIN"
                if exc.uncertain
                else ("RETRYING" if exc.retryable and row["attempt"] < self.settings.max_attempts else "DEAD_LETTER")
            )
            error = exc.code
        with self.db.transaction() as tx:
            current = tx.execute(
                "SELECT * FROM outbox WHERE id=? AND claim_token=? AND state='DISPATCHING'", (row["id"], token)
            ).fetchone()
            if current is None:
                return True
            tx.execute(
                "UPDATE outbox SET state=?,error=?,claim_token=NULL,lease_until=NULL,available_at=? WHERE id=?",
                (state, error, time.time() + self.settings.retry_base * 2 ** (row["attempt"] - 1), row["id"]),
            )
            if state == "SENT":
                tx.execute(
                    "INSERT INTO delivered_mail(id,job_id,recipient,subject,body,delivered_at) VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                    (row["id"], row["job_id"], row["recipient"], row["subject"], row["body"], time.time()),
                )
            audit(tx, row["job_id"], "ACK_" + state, receipt or error or "SMTP acknowledgment")
        return True

    def resolve_ack(self, outbox_id: str, outcome: str, evidence: str) -> None:
        if outcome not in {"confirmed", "not-sent"} or len(evidence.strip()) < 10:
            raise ValueError("Provide confirmed/not-sent and an operator verification reference")
        with self.db.transaction() as tx:
            lock = " FOR UPDATE" if tx.postgres else ""
            row = tx.execute("SELECT * FROM outbox WHERE id=?" + lock, (outbox_id,)).fetchone()
            if not row or row["state"] not in {"UNCERTAIN", "DEAD_LETTER"}:
                raise Conflict("Only uncertain/dead-letter acknowledgments can be reconciled")
            state = "SENT" if outcome == "confirmed" else "PENDING"
            tx.execute(
                "UPDATE outbox SET state=?,attempt=0,available_at=?,error=NULL,lease_until=NULL,claim_token=NULL WHERE id=?",
                (state, time.time(), outbox_id),
            )
            if state == "SENT":
                tx.execute(
                    "INSERT INTO delivered_mail(id,job_id,recipient,subject,body,delivered_at) VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                    (row["id"], row["job_id"], row["recipient"], row["subject"], row["body"], time.time()),
                )
            audit(tx, row["job_id"], "ACK_RECONCILED", outcome + ": " + evidence.strip()[:300])

    def get(self, job_id: str) -> dict:
        with self.db.transaction(False) as tx:
            row = tx.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            result = public_job(row)
            result["audit"] = [
                dict(x)
                for x in tx.execute("SELECT * FROM audit WHERE job_id=? ORDER BY timestamp", (job_id,)).fetchall()
            ]
            return result

    def snapshot(self) -> dict:
        with self.db.transaction(False) as tx:
            jobs = [
                public_job(x) for x in tx.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100").fetchall()
            ]
            states = {
                x["state"]: x["count"]
                for x in tx.execute("SELECT state,COUNT(*) AS count FROM jobs GROUP BY state").fetchall()
            }
            audits = [dict(x) for x in tx.execute("SELECT * FROM audit ORDER BY timestamp DESC LIMIT 30").fetchall()]
            mailbox = [
                dict(x)
                for x in tx.execute("SELECT * FROM delivered_mail ORDER BY delivered_at DESC LIMIT 20").fetchall()
            ]
            outbox = [
                dict(x) for x in tx.execute("SELECT * FROM outbox ORDER BY available_at DESC LIMIT 20").fetchall()
            ]
            return {
                "jobs": jobs,
                "states": states,
                "audit": audits,
                "mailbox": mailbox,
                "outbox": outbox,
                "registered_invoices": tx.execute("SELECT COUNT(*) AS count FROM invoice_registry").fetchone()["count"],
                "delivered_count": tx.execute("SELECT COUNT(*) AS count FROM delivered_mail").fetchone()["count"],
                "average_processing_seconds": float(
                    tx.execute(
                        "SELECT COALESCE(AVG(updated_at-created_at),0) AS latency FROM jobs WHERE state='COMPLETED'"
                    ).fetchone()["latency"]
                ),
                "total_jobs": sum(states.values()),
                "retry_events": tx.execute(
                    "SELECT COUNT(*) AS count FROM audit WHERE event IN ('RETRYING','LEASE_RECOVERED','ACK_RETRY')"
                ).fetchone()["count"],
                "database": "PostgreSQL" if self.db.postgres else "SQLite WAL",
                "llm_provider": self.settings.provider,
                "redis": self.signals.status(),
            }
