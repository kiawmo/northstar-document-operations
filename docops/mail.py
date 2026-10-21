"""Configured IMAP/SMTP transport; no credentials or destinations from document text."""

from __future__ import annotations

from email.message import EmailMessage
import base64
import imaplib
import json
from pathlib import Path
import re
import smtplib
import ssl
import httpx


def profile(filename: str) -> dict:
    if not filename:
        return {}
    try:
        path = Path(filename)
        if not path.is_absolute() or path.stat().st_size > 32768:
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) - {"client_id", "imap", "smtp"}:
            raise ValueError
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,50}", value["client_id"]):
            raise ValueError
        for kind in ("imap", "smtp"):
            if kind not in value:
                continue
            item = value[kind]
            if not isinstance(item, dict) or not re.fullmatch(r"[A-Za-z0-9.-]+", item["host"]):
                raise ValueError
            if not isinstance(item.get("username"), str) or not item["username"]:
                raise ValueError
            if "password" in item or not 1 <= int(item.get("port", 993 if kind == "imap" else 465)) <= 65535:
                raise ValueError
            if item.get("auth", "password") == "google_oauth2":
                if not item.get("client_id") or "client_secret" in item or "refresh_token" in item:
                    raise ValueError
                read_secret(item["client_secret_file"])
                read_secret(item["refresh_token_file"])
            elif item.get("auth", "password") == "password":
                read_secret(item["password_file"])
            else:
                raise ValueError
            if kind == "imap" and (
                not isinstance(item.get("folder", "INBOX"), str)
                or any(c in item.get("folder", "INBOX") for c in "\r\n")
            ):
                raise ValueError
            if kind == "smtp":
                if item.get("tls", "implicit") not in ("implicit", "starttls"):
                    raise ValueError
                address(item["sender"])
                address(item["recipient"])
        if not any(k in value for k in ("imap", "smtp")):
            raise ValueError
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Invalid DOCUMENT_MAIL_PROFILE_FILE; use mounted secrets and verified TLS") from None


def read_secret(filename: str) -> str:
    path = Path(filename)
    if not path.is_absolute() or path.stat().st_size > 16384:
        raise ValueError("A bounded absolute secret file is required")
    result = path.read_text(encoding="utf-8").rstrip("\r\n")
    if not result or any(c in result for c in "\r\n\x00"):
        raise ValueError("Invalid mailbox secret file")
    return result


def address(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
        raise ValueError("A mailbox address is required")
    return value


class MailFailure(Exception):
    def __init__(self, code: str, retryable: bool, uncertain: bool = False):
        self.code, self.retryable, self.uncertain = code, retryable, uncertain
        super().__init__(code)


def xoauth(item: dict) -> bytes:
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as http:
            result = http.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": item["client_id"],
                    "client_secret": read_secret(item["client_secret_file"]),
                    "refresh_token": read_secret(item["refresh_token_file"]),
                },
            )
        result.raise_for_status()
        token = result.json()["access_token"]
        if not isinstance(token, str) or not token or any(c in token for c in "\r\n\x01"):
            raise ValueError
        return f"user={item['username']}\x01auth=Bearer {token}\x01\x01".encode()
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise MailFailure("MAIL_OAUTH_FAILED", True) from None


def send_smtp(item: dict, row: dict) -> str:
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = (
        address(item["sender"]),
        address(row["recipient"]),
        row["subject"],
    )
    message["Message-ID"] = "<" + row["id"] + "@" + item["sender"].split("@")[1] + ">"
    message.set_content(row["body"])
    sending = False
    accepted = False
    try:
        context = ssl.create_default_context()
        if item.get("tls", "implicit") == "implicit":
            smtp = smtplib.SMTP_SSL(item["host"], int(item.get("port", 465)), timeout=30, context=context)
        else:
            smtp = smtplib.SMTP(item["host"], int(item.get("port", 587)), timeout=30)
        with smtp:
            smtp.ehlo()
            if item.get("tls", "implicit") == "starttls":
                smtp.starttls(context=context)
                smtp.ehlo()
            if item.get("auth", "password") == "google_oauth2":
                code, response = smtp.docmd("AUTH", "XOAUTH2 " + base64.b64encode(xoauth(item)).decode())
                if code == 334:
                    code, response = smtp.docmd("")
                if code != 235:
                    raise smtplib.SMTPAuthenticationError(code, b"OAuth authentication rejected")
            else:
                smtp.login(item["username"], read_secret(item["password_file"]))
            sending = True
            refused = smtp.send_message(message)
            if refused:
                raise MailFailure("SMTP_RECIPIENT_REJECTED", False)
            # DATA was accepted. QUIT disconnects must not turn a successful send into a retry.
            sending = False
            accepted = True
            return str(message["Message-ID"])
    except MailFailure:
        raise
    except smtplib.SMTPResponseException as exc:
        raise MailFailure("SMTP_RESPONSE_" + str(exc.smtp_code), 400 <= exc.smtp_code < 500) from None
    except smtplib.SMTPRecipientsRefused:
        raise MailFailure("SMTP_RECIPIENT_REJECTED", False) from None
    except (OSError, smtplib.SMTPException):
        if accepted:
            return str(message["Message-ID"])
        raise MailFailure(
            "SMTP_OUTCOME_UNCERTAIN" if sending else "SMTP_CONNECTION_FAILED", not sending, sending
        ) from None


def poll_imap(service) -> dict:
    """Read-only UID polling, bounded messages, durable cursor, ingestion-level dedupe."""
    from .inbox import ingest_message

    account = service.mail_profile
    if "imap" not in account:
        raise ValueError("An IMAP profile is required")
    item, client = account["imap"], account["client_id"]
    mailbox = item.get("folder", "INBOX")
    identity = (
        __import__("hashlib").sha256((item["host"] + ":" + item["username"] + ":" + mailbox).encode()).hexdigest()
    )
    accepted, rejected = 0, 0
    with imaplib.IMAP4_SSL(
        item["host"], int(item.get("port", 993)), ssl_context=ssl.create_default_context(), timeout=30
    ) as inbox:
        if item.get("auth", "password") == "google_oauth2":
            token = xoauth(item)
            inbox.authenticate("XOAUTH2", lambda challenge: token if not challenge else b"")
        else:
            inbox.login(item["username"], read_secret(item["password_file"]))
        status, _ = inbox.select(mailbox, readonly=True)
        if status != "OK":
            raise RuntimeError("IMAP_SELECT_FAILED")
        _, validity = inbox.response("UIDVALIDITY")
        epoch = int(validity[0])
        with service.db.transaction() as tx:
            row = tx.execute("SELECT * FROM inbox_cursor WHERE identity=?", (identity,)).fetchone()
            cursor = int(row["last_uid"]) if row and row["epoch"] == epoch else 0
        status, values = inbox.uid("search", None, "UID", f"{cursor + 1}:*")
        if status != "OK":
            raise RuntimeError("IMAP_SEARCH_FAILED")
        # Some servers return the last UID for an inverted range; filter explicitly.
        uids = sorted(int(uid) for uid in values[0].split() if int(uid) > cursor)[:50]
        for uid in uids:
            rejected_before = rejected
            status, sizes = inbox.uid("fetch", str(uid), "(RFC822.SIZE)")
            size_data = b" ".join(x for x in sizes if isinstance(x, bytes))
            size = re.search(rb"RFC822.SIZE (\d+)", size_data)
            if status != "OK" or not size:
                raise RuntimeError("IMAP_SIZE_FAILED")
            if int(size[1]) > service.settings.max_upload_bytes:
                rejected += 1
            else:
                status, parts = inbox.uid("fetch", str(uid), "(BODY.PEEK[])")
                raw = next((part[1] for part in parts if isinstance(part, tuple)), None)
                if status != "OK" or not isinstance(raw, bytes):
                    raise RuntimeError("IMAP_FETCH_FAILED")
                try:
                    ingest_message(raw, service, client, f"{identity[:32]}:{epoch}:{uid}")
                    accepted += 1
                except ValueError:
                    # Unsupported/oversize messages stay in the inbox and don't block later UIDs.
                    rejected += 1
            with service.db.transaction() as tx:
                tx.execute(
                    """INSERT INTO inbox_cursor(identity,epoch,last_uid,rejected) VALUES(?,?,?,?)
                  ON CONFLICT(identity) DO UPDATE SET epoch=excluded.epoch,
                  last_uid=CASE WHEN inbox_cursor.epoch=excluded.epoch AND inbox_cursor.last_uid>excluded.last_uid
                    THEN inbox_cursor.last_uid ELSE excluded.last_uid END,
                  rejected=inbox_cursor.rejected+excluded.rejected""",
                    (identity, epoch, uid, rejected - rejected_before),
                )
    return {"accepted_messages": accepted, "unsupported_messages": rejected, "read_only": True}
