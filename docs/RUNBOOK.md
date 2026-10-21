# Operations and recovery

Use `docker compose ps`, API `/health` and `/ready`, gateway `/ready`, worker heartbeat and the authenticated snapshot. Liveness alone does not prove extraction works. Run a fictional end-to-end probe after upgrades. Alert on unavailable readiness, expired heartbeats, growing queue/review backlog and uncertain acknowledgments. Logs contain states and trace IDs; do not enable document/token dumps.

## Recovery

- **Provider outage:** source remains committed. Bounded retries lead to recovery or review. Restore provider readiness, inspect the audit and request a reviewed retry for exhausted jobs.
- **Worker restart:** SQL leases expire and are reclaimed. Old attempt tokens cannot commit over a newer claim. Do not manually mark jobs complete.
- **Redis outage:** polling continues against SQL. Restore Redis; no SQL job reconstruction is needed.
- **PostgreSQL outage:** readiness fails; workers retain recovery behavior. Restore database connectivity before replay. Do not delete the volume as a repair step.
- **Invalid extraction:** inspect the original source, correct validated fields and supply a decision reason. Resolve stale revision conflicts by reloading current state.
- **Unknown SMTP outcome:** reconcile the stable Message-ID before retry, as documented in MAIL.md.

## Backups and restore

Back up PostgreSQL using `pg_dump` custom format from the running database, with credentials loaded inside the container rather than command arguments. For a consistent operational checkpoint, stop only this stack's worker first. Keep the original database volume; write the dump to an encrypted backup location with restricted access. Capture the release commit, private configuration and secret-manager references separately. The optional Codex state volume contains rotating auth credentials and needs credential-level protection.

Restore the dump with `pg_restore --no-owner` into a **new database in a separate Compose project** initialized with the application role. Pause the original worker and disable real SMTP/IMAP in the restore environment to avoid external duplicates. Check row counts, retained source hashes, idempotency receipts, invoice uniqueness, review state and outbox before switching ingress. Run a synthetic smoke check and a restore rehearsal on the actual backup destination. Backup success without a restore test is insufficient.

`python scripts/validate_containers.py` performs a synthetic PostgreSQL dump/restore test using a separate disposable database; it does not back up operational documents. Never use `docker compose down -v` on irreplaceable data. Routine `docker compose down` preserves named volumes. Test projects and real deployments must use different project names, ports, secrets and credentials.

## Upgrades

Read the changelog, make an encrypted backup and test the new image against a restored copy first. Schema initialization is additive and repeat safe in this release; do not assume that makes every future downgrade safe. Record image digests and migration/restore outcomes. Revert ingress only after checking data compatibility; retain the previous image and original backup until acceptance.
