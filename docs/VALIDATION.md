# Release validation — 1.0.0

Executed on 2026-10-22 against the independent source tree, using Linux x86-64 Docker Engine and real PostgreSQL 18.6 / authenticated Redis 8.2.10. All records and attachments used for validation are synthetic. CI repeats the documented container matrix from a fresh checkout.

| Check | Observed result |
| --- | --- |
| Application with SQLite + real Redis | 96 passed, zero skipped |
| Application with PostgreSQL + real Redis | 96 passed, zero skipped |
| Gateway Linux contracts/process handling | 43 passed; one Windows-only test skipped |
| Gateway Windows process/contract suite | 44 passed |
| Ruff application/tests/scripts | Passed |
| Deterministic document demo | 7 checks passed, including PDF, OCR, duplicate handling and review |
| Controlled failure exercise | 7 checks passed: corrupted PDF, malformed model output, 429/503 recovery, actual worker subprocess loss/stale lease, database write lock/recovery, mailbox 503 recovery |
| HTTP API smoke | 7 checks passed from both Linux and Windows |
| Six-service runtime/lifecycle | Readiness, non-root/read-only boundaries, isolated parser, restricted SQL role, worker/database restart persistence, dump/restore counts and source hashes passed |
| Full locked Python dependency audit | Zero known findings in application and gateway production locks |
| Actual runtime image scans | Reviewed individually; see [image security](IMAGE_SECURITY.md), including retained upstream findings and residual risk |

The gateway tests exercise structured output, process-tree timeout cleanup, malformed output, bounded retries, disabled paid fallback and argument-array invocation. Deterministic provider output is explicitly identified as fixture mode. Real Codex use requires the operator's supported authenticated account; no access, credits or subscription are bundled. External IMAP/SMTP accounts were not contacted during these tests: their complete implementations are covered by controlled protocol contracts, including UID/idempotency and delivery uncertainty. Verify account-specific permissions and TLS settings before enabling a real mailbox.

The deployed scope is a private single-organization processing/review service. Setup, database privileges, secrets, TLS assumptions and backup procedures are part of acceptance; it is not a claim of production customer traffic, measured business savings, regulatory certification or universal deployment suitability. No customer information or account credentials are included.

## Repeat the checks

Follow [Testing](TESTING.md) for exact container commands. `scripts/smoke.py` probes the normal authenticated upload/API interface. `scripts/validate_containers.py` restarts only the selected Compose services and restores a dump to a disposable database. Run it on synthetic staging data with real mailbox intake disabled. The workflow in `.github/workflows/ci.yml` builds every runtime/test target and executes both SQL backends, Redis, gateway, demo, failure and lifecycle checks.

For live extraction, follow [Setup](SETUP.md) and run `python scripts/smoke.py --live`. Credentials stay in ignored local files or volumes. Failure of live authentication must be resolved explicitly; fixture output never counts as live-provider validation.
