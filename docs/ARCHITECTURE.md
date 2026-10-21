# Architecture

## Durable boundaries

The API commits source bytes, a hash-bound idempotency receipt and a queue record in one SQL transaction. A repeated key with different bytes returns 409. Repeated bytes reuse the original job, including alternate delivery keys; the receipt is tenant/client scoped. Redis notification happens after commit and is advisory. A missing wakeup cannot lose committed work.

Workers claim SQL leases with attempt tokens. A stale process cannot stage or finish a newer attempt. Expired leases return to the queue; transient failures receive bounded exponential retry. Permanent parsing errors, low confidence and invalid model/decimal output enter operator review. Approval requires the current revision and a reason; simultaneous edits return 409. The database's vendor/invoice uniqueness boundary prevents a duplicate accounting-register entry even when concurrent deliveries differ in bytes.

## State and review

The primary path is `QUEUED → PROCESSING → EXTRACTING → VALIDATING → COMPLETED`. Controlled alternatives include `RETRYING`, `REVIEW_REQUIRED`, `DEAD_LETTER`, `DUPLICATE` and `REJECTED`; exhausted automatic attempts enter `DEAD_LETTER` for explicit operator recovery rather than being silently discarded. The review API can save validated fields, approve, reject or request a retry. The original source and audit trail remain available. This is an internal invoice register, not a payment processor or an ERP adapter.

## Parser and extraction

The API and worker share only a Unix socket with the parser. The parser has no network, application secrets or database mounts. It serializes bounded child processes with address-space, CPU, output-file, descriptor and wall-clock limits. MuPDF handles PDF rendering/text, Tesseract handles raster OCR. Preview images and parser output are bounded and revalidated upstream.

Extracted text passes to a schema-constrained provider. The gateway controls process invocation, queue admission, deadlines and output schemas. Document content is untrusted input, not instructions. The provider cannot mutate jobs or access reviewer credentials. Confidence is a routing signal rather than proof of correctness; deterministic invoice checks remain mandatory.

## Acknowledgment outbox

Invoice completion and acknowledgment scheduling share a SQL transaction. Delivery uses reservations and a stable message ID. The local adapter records delivery transactionally; SMTP is an external side effect. When a connection fails after SMTP DATA may have been accepted, the outbox enters an uncertain state requiring operator reconciliation. The system does not promise exactly-once external email delivery. Fixed operator-configured recipients prevent document text from selecting arbitrary destinations.

## Authorization scope

The workspace key authorizes all records in one trusted organization. Client labels separate idempotency namespaces and intake routes; they do not isolate mutually untrusted users. Deploy behind private authenticated ingress. Multi-organization SaaS access, delegated reviewer roles and public upload portals require an additional authorization design.
