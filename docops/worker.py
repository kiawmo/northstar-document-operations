import json
import logging
from pathlib import Path
import time
from threading import Event, Thread

from .extraction import InvoiceProvider, PermanentExtractionError, TransientProviderError, extract_text
from .service import Conflict, DocumentService


logger = logging.getLogger("docops.worker")


class Worker:
    def __init__(self, service: DocumentService, provider: InvoiceProvider):
        self.service = service
        self.provider = provider
        self.next_inbox_poll = 0.0

    def tick(self) -> bool:
        if "imap" in self.service.mail_profile and time.monotonic() >= self.next_inbox_poll:
            from .mail import poll_imap

            self.next_inbox_poll = time.monotonic() + self.service.settings.inbox_poll_seconds
            try:
                logger.info(json.dumps({"event": "inbox_poll", **poll_imap(self.service)}))
            except Exception:
                logger.warning(
                    json.dumps(
                        {"event": "inbox_poll_failed", "retry_after_seconds": self.service.settings.inbox_poll_seconds}
                    )
                )
        job = self.service.claim()
        if job is None:
            return self.service.deliver_one()
        started = time.monotonic()
        try:
            detail = (
                "Isolated parser over Unix socket; no network or secrets"
                if self.service.settings.parser_socket
                else "Source parser started; content treated as untrusted data"
            )
            self.service.stage(job, "EXTRACTING", detail)
            text, used_ocr = extract_text(bytes(job["source_bytes"]), job["content_type"], self.service.settings)
            self.service.stage(
                job, "EXTRACTING", "Tesseract OCR completed" if used_ocr else "Embedded text extracted", text
            )
            invoice = self.provider.extract(text, job["id"])
            self.service.stage(
                job, "EXTRACTING", f"Structured invoice provider: {self.service.settings.provider}; schema validated"
            )
            self.service.stage(job, "VALIDATING", "Strict schema passed; Decimal business-rule checks started")
            state = self.service.finish(job, invoice)
            logger.info(
                json.dumps(
                    {
                        "trace_id": job["id"],
                        "event": "completed",
                        "state": state,
                        "latency_ms": round((time.monotonic() - started) * 1000),
                    }
                )
            )
        except Conflict:
            logger.info(json.dumps({"trace_id": job["id"], "event": "stale_claim_refused"}))
        except PermanentExtractionError as exc:
            self.service.fail(job, str(exc), permanent=True)
        except TransientProviderError as exc:
            self.service.fail(job, str(exc))
        except Exception as exc:
            # Unknown errors are observable without printing confidential source/provider output.
            self.service.fail(job, f"Unexpected {type(exc).__name__}; inspect worker configuration")
        return True

    def run(self, stop: Event | None = None) -> None:
        stop = stop or Event()
        pulse_stop = Event()

        def pulse():
            while not pulse_stop.is_set():
                if self.service.settings.worker_heartbeat_file:
                    path = Path(self.service.settings.worker_heartbeat_file)
                    temporary = path.with_suffix(".tmp")
                    temporary.write_text(str(time.time()))
                    temporary.replace(path)
                self.service.signals.heartbeat()
                pulse_stop.wait(5)

        # Health observations remain fresh during bounded OCR/gateway calls.
        thread = Thread(target=pulse, name="worker-heartbeat", daemon=True)
        thread.start()
        try:
            while not stop.is_set():
                try:
                    busy = self.tick()
                except Exception as exc:
                    logger.warning(
                        json.dumps(
                            {
                                "event": "database_or_worker_unavailable",
                                "error_type": type(exc).__name__,
                                "recovery": "durable claims recover after lease",
                            }
                        )
                    )
                    busy = False
                if busy:
                    stop.wait(0.05)
                elif self.service.signals.client is not None:
                    # Redis is only an acceleration hint; every wakeup checks SQL.
                    if not self.service.signals.wait(0.5):
                        stop.wait(0.05)
                else:
                    stop.wait(0.5)
        finally:
            pulse_stop.set()
            thread.join(timeout=2)
