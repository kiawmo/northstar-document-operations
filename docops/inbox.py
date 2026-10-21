"""RFC 822 attachment adapter; transport-independent for IMAP/webhook ingestion."""

from email import policy
from email.parser import BytesParser
from .service import DocumentService


def ingest_message(raw: bytes, service: DocumentService, client_id: str, delivery_key: str | None = None) -> list[dict]:
    if len(raw) > service.settings.max_upload_bytes:
        raise ValueError("Inbox message exceeds 5 MB")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    message_id = str(message.get("Message-ID", ""))[:100]
    results = []
    for index, part in enumerate(message.iter_attachments()):
        content = part.get_payload(decode=True)
        if content:
            try:
                result = service.ingest(
                    content,
                    part.get_filename() or "attachment.txt",
                    part.get_content_type(),
                    client_id,
                    "email",
                    f"{delivery_key or message_id}:{index}" if (delivery_key or message_id) else None,
                )
            except ValueError:
                continue
            results.append(result)
    if not results:
        raise ValueError("Email has no supported document attachment")
    return results
