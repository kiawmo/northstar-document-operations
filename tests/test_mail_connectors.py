import json
from email.message import EmailMessage
import smtplib
import time
import base64
import httpx

import pytest
from docops.mail import MailFailure, poll_imap, profile, send_smtp
from docops.fixtures import invoice_text


@pytest.fixture
def mailbox(tmp_path):
    password = tmp_path / "password"
    password.write_text("offline-test-password")
    item = {
        "host": "mail.example.test",
        "username": "accounts@example.test",
        "password_file": str(password),
        "sender": "accounts@example.test",
        "recipient": "finance@example.test",
    }
    return {"client_id": "northstar", "smtp": item, "imap": {**item, "port": 993}}


def test_mail_profile_secret_file_tls_and_no_inline_secret(mailbox, tmp_path):
    file = tmp_path / "mail.json"
    file.write_text(json.dumps(mailbox))
    assert profile(str(file))["smtp"]["host"] == "mail.example.test"
    mailbox["smtp"]["tls"] = "none"
    file.write_text(json.dumps(mailbox))
    with pytest.raises(ValueError, match="Invalid DOCUMENT_MAIL"):
        profile(str(file))


@pytest.mark.parametrize("tls", ["implicit", "starttls"])
def test_smtp_tls_auth_envelope_mime_and_accepted_quit_disconnect(mailbox, monkeypatch, tls):
    steps = []

    class SMTP:
        def __init__(self, host, port, **kw):
            assert host == "mail.example.test" and kw["timeout"] == 30
            if tls == "implicit":
                assert kw["context"].check_hostname

        def __enter__(self):
            return self

        def __exit__(self, *args):
            raise smtplib.SMTPServerDisconnected()

        def ehlo(self):
            steps.append("ehlo")

        def starttls(self, context):
            assert context.check_hostname
            steps.append("tls")

        def login(self, username, password):
            assert password == "offline-test-password"
            steps.append("login")

        def send_message(self, message):
            assert message["To"] == "finance@example.test" and message["Message-ID"] == "<outbox-1@example.test>"
            steps.append("send")
            return {}

    monkeypatch.setattr("docops.mail.smtplib.SMTP_SSL", SMTP)
    monkeypatch.setattr("docops.mail.smtplib.SMTP", SMTP)
    row = {"id": "outbox-1", "recipient": "finance@example.test", "subject": "Accepted", "body": "Receipt"}
    assert send_smtp({**mailbox["smtp"], "tls": tls}, row) == "<outbox-1@example.test>"
    assert steps.index("login") < steps.index("send")
    if tls == "starttls":
        assert steps.index("tls") < steps.index("login")


def seed(service, worker, mailbox):
    service.mail_profile = mailbox
    job = service.ingest(invoice_text().encode(), "invoice.txt", "text/plain")
    # Avoid IMAP during this queue test; polling is separately exercised below.
    worker.next_inbox_poll = float("inf")
    worker.tick()
    return job


def test_remote_outbox_send_is_reserved_duplicate_free_and_audited(service, worker, mailbox, monkeypatch):
    job = seed(service, worker, mailbox)
    calls = []
    monkeypatch.setattr("docops.mail.send_smtp", lambda item, row: calls.append(row) or "<accepted@example.test>")
    assert service.deliver_one()
    assert not service.deliver_one()
    assert len(calls) == 1 and service.snapshot()["delivered_count"] == 1
    assert service.get(job["id"])["audit"][-1]["event"] == "ACK_SENT"


@pytest.mark.parametrize("uncertain", [False, True])
def test_remote_outbox_retries_only_known_non_delivery_and_manual_reconciliation(
    service, worker, mailbox, monkeypatch, uncertain
):
    seed(service, worker, mailbox)

    def failure(*args):
        raise MailFailure("TEST_OUTCOME", not uncertain, uncertain)

    monkeypatch.setattr("docops.mail.send_smtp", failure)
    service.deliver_one()
    row = service.snapshot()["outbox"][0]
    assert row["state"] == ("UNCERTAIN" if uncertain else "RETRYING")
    if uncertain:
        assert not service.deliver_one()
        service.resolve_ack(row["id"], "confirmed", "SMTP provider receipt verified by operator")
        assert service.snapshot()["delivered_count"] == 1


def test_read_only_imap_uid_cursor_attachment_dedupe_and_worker_wiring(service, worker, mailbox, monkeypatch):
    message = EmailMessage()
    message["Message-ID"] = "<message@example.test>"
    message.set_content("Invoice attached")
    message.add_attachment(invoice_text().encode(), maintype="text", subtype="plain", filename="invoice.txt")
    raw = message.as_bytes()
    observed = []

    class Inbox:
        def __init__(self, host, port, ssl_context, timeout):
            assert ssl_context.check_hostname and port == 993

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, *args):
            pass

        def select(self, folder, readonly):
            assert readonly
            return "OK", []

        def response(self, key):
            return "UIDVALIDITY", [b"123"]

        def uid(self, command, *args):
            observed.append((command, args))
            if command == "search":
                return "OK", [b"1"]
            if args[-1] == "(RFC822.SIZE)":
                return "OK", [f"1 (RFC822.SIZE {len(raw)})".encode()]
            assert args[-1] == "(BODY.PEEK[])"
            return "OK", [(b"1 BODY[]", raw), b")"]

    monkeypatch.setattr("docops.mail.imaplib.IMAP4_SSL", Inbox)
    service.mail_profile = mailbox
    assert poll_imap(service)["accepted_messages"] == 1
    assert poll_imap(service)["accepted_messages"] == 0
    assert service.snapshot()["total_jobs"] == 1
    # UIDVALIDITY/cursor are durable rather than Python process state.
    worker.tick()
    assert any(x[0] == "search" for x in observed)
    assert service.snapshot()["jobs"][0]["state"] == "COMPLETED"


def test_expired_send_reservation_requires_review(service, worker, mailbox):
    seed(service, worker, mailbox)
    row = service.snapshot()["outbox"][0]
    with service.db.transaction() as tx:
        tx.execute("UPDATE outbox SET state='DISPATCHING',lease_until=? WHERE id=?", (time.time() - 1, row["id"]))
    assert not service.deliver_one()
    assert service.snapshot()["outbox"][0]["state"] == "UNCERTAIN"


@pytest.mark.parametrize(
    "failure,retryable,uncertain",
    [
        (smtplib.SMTPDataError(451, b"temporary"), True, False),
        (smtplib.SMTPDataError(550, b"rejected"), False, False),
        (smtplib.SMTPServerDisconnected(), False, True),
    ],
)
def test_smtp_known_rejection_and_ambiguous_data_outcome(mailbox, monkeypatch, failure, retryable, uncertain):
    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            pass

        def login(self, *args):
            pass

        def send_message(self, message):
            raise failure

    monkeypatch.setattr("docops.mail.smtplib.SMTP_SSL", SMTP)
    with pytest.raises(MailFailure) as error:
        send_smtp(
            mailbox["smtp"],
            {"id": "outbox", "recipient": "finance@example.test", "subject": "Accepted", "body": "Receipt"},
        )
    assert error.value.retryable == retryable and error.value.uncertain == uncertain


def test_google_smtp_oauth_refresh_and_sasl_wire_contract(mailbox, monkeypatch):
    item = {
        **mailbox["smtp"],
        "auth": "google_oauth2",
        "client_id": "oauth-test",
        "client_secret_file": mailbox["smtp"]["password_file"],
        "refresh_token_file": mailbox["smtp"]["password_file"],
    }

    class HTTP:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, data):
            assert url == "https://oauth2.googleapis.com/token" and data["grant_type"] == "refresh_token"
            return httpx.Response(200, json={"access_token": "offline-oauth-token"}, request=httpx.Request("POST", url))

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            pass

        def docmd(self, command, arguments):
            assert command == "AUTH"
            mechanism, encoded = arguments.split(" ", 1)
            assert mechanism == "XOAUTH2"
            assert base64.b64decode(encoded) == b"user=accounts@example.test\x01auth=Bearer offline-oauth-token\x01\x01"
            return 235, b"accepted"

        def send_message(self, message):
            return {}

    monkeypatch.setattr("docops.mail.httpx.Client", HTTP)
    monkeypatch.setattr("docops.mail.smtplib.SMTP_SSL", SMTP)
    assert (
        send_smtp(item, {"id": "outbox", "recipient": "finance@example.test", "subject": "Accepted", "body": "Receipt"})
        == "<outbox@example.test>"
    )
