# Security policy and threat model

## Reporting

Report vulnerabilities through this repository's private GitHub security advisory workflow when available. Otherwise contact the maintainer through the profile listed on this repository and request a private channel; do not post exploit details, credentials or private documents in a public issue. Only the latest reviewed release is supported. There is no guaranteed response-time SLA.

## Trust boundaries

This is a private, single-organization operator application. The workspace API key authorizes all namespaces. It is not a multi-tenant identity system. Use private authenticated TLS ingress; do not expose the service as an anonymous public upload endpoint. Configuration, mailbox hosts/recipients, database operators and the host kernel are trusted. Uploaded documents and LLM output are untrusted.

Mutations and document reads require a constant-time compared header key, not an ambient browser cookie. The UI retains the key only in session storage, escapes provider/source strings and uses CSP, frame restrictions, no-sniff, no-referrer and no-store. Cross-origin access is not enabled. Apply edge rate/connection limits; built-in rate limiting is local to one process.

Uploads have a 5 MiB bound, normalized filenames, strict schema/content types and PDF/image signature checks. Only five unencrypted PDF pages are supported. The isolated parser has no network, credentials, database or Docker socket. Each child has memory, CPU, file-size, descriptor, raster, text and wall-clock limits. A malicious document can still crash or exhaust the bounded parser. Container isolation is defense in depth, not proof against kernel escape. Restrict sources, patch images/host and add malware screening appropriate to your deployment.

The LLM gateway bounds admission, deadlines, process output and retry attempts. It runs Codex with an argument array, no shell interpolation, isolated request directories, disabled tool features and structured schema validation. Unexpected tool activity is rejected. Invalid model output cannot bypass decimal/business validation or reviewer authorization. Source text may leave your infrastructure when a live provider is selected; document processing needs the owner's authorization and retention/data-policy review.

PostgreSQL commits sources, receipts and queue records transactionally; leases are attempt fenced. The application role has no superuser or role/database-creation privilege. Redis hints never own durable work. The acknowledgment outbox handles external delivery uncertainty explicitly; it cannot guarantee exactly-once SMTP delivery. Audit entries are application append-only, not tamper proof against a database administrator.

## Secrets and dependencies

No keys, authenticated account state or customer documents are part of the release. Bootstrap generates local secrets in an ignored, permission-restricted directory. Hash-locked Python dependencies, pinned base images and a checked Codex archive make inputs reviewable. Digest pins must be updated and re-scanned; they do not remove vulnerabilities. Preserve installed third-party license notices.

Run the documented dependency and actual-image scans before rollout. Assess reachability, affected APIs, enabled configuration, fixes and compensating controls individually. Never treat raw scanner counts as a clean bill of health or suppress an applicable finding merely because it is transitive. The current validation record is in [docs/VALIDATION.md](docs/VALIDATION.md) and the row-level image review is in [docs/IMAGE_SECURITY.md](docs/IMAGE_SECURITY.md); it must distinguish executed checks, external account checks and residual risks.

Secrets, real documents, dumps, logs with private values and auth volumes must never be attached to public issues. Rotate any accidentally exposed credential immediately and follow the service provider's revocation process; deleting a Git commit does not revoke a leaked key.
