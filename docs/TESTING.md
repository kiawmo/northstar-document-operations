# Testing

Run commands from the repository root. Deterministic tests never require a paid API or a real mailbox. Real provider and mailbox acceptance are separate checks using operator-owned credentials.

## Unit and contract suites

After installing `requirements-dev.lock`, run `python -m pytest -q` and `python -m ruff check docops tests scripts`. The default suite includes parser boundary cases. Windows native runs cannot enforce Linux `resource` limits; run the container suite before release. Tests requiring `TEST_DOCUMENT_REDIS_URL` skip only when a real Redis endpoint is absent; these skips are not container acceptance.

The gateway has a separate lockfile/environment: install `gateway/requirements-dev.lock`, change to `gateway` and run `python -m pytest -q`. Provider HTTP responses and subprocess fixtures are controlled; no account credentials are used by this suite.

## Container matrix

```sh
python scripts/bootstrap.py --fixture
docker compose pull --ignore-buildable
docker compose build
docker compose --profile test build test gateway-test
docker compose up -d --wait
docker compose run --rm test
docker compose run --rm -e TEST_DOCUMENT_DATABASE_URL_FILE=/run/secrets/database_url test
docker compose run --rm gateway-test
docker compose run --rm --no-deps -w /tmp test python -m docops demo --llm fixture --database sqlite:////tmp/demo.db
docker compose run --rm --no-deps -w /tmp test python -m docops chaos --llm fixture --database sqlite:////tmp/chaos.db
python scripts/smoke.py
python scripts/validate_containers.py
```

The two application runs cover SQLite and a separate PostgreSQL test schema, both with authenticated real Redis. Do not point `TEST_DOCUMENT_DATABASE_URL` or its file at a database/schema containing important data: test fixtures clear their test tables. The operational schema is `docops`; the test schema is `docops_test`.

Coverage includes upload/auth/schema rejection, PDF/OCR/source previews, numerical validation, client-scoped idempotency receipts and conflicting replays, concurrent claims/initialization, stale revisions and lease fencing, worker subprocess crash, temporary database unavailability, bounded provider failures/retry exhaustion, review/replay, Redis failure/polling and real heartbeat, IMAP UID receipts, SMTP uncertainty/reconciliation and parser response/network boundaries.

`validate_containers.py` checks actual container isolation, PostgreSQL privileges, persistent rows across worker/database restarts and a disposable database dump/restore. It restarts only services in the current Compose project. Run it on a staging installation with synthetic data and no real mailbox enabled. Tests intentionally pause workers and databases.

## Fresh reviewer installation

Clone into a fresh directory and use a new Compose project name/port (`COMPOSE_PROJECT_NAME=northstar-review`, `DOCOPS_PORT=18123`). Generate its own secrets with the bootstrap script; never copy another deployment's secrets. Follow the matrix above, passing `--url http://127.0.0.1:18123` to both host-side probe scripts when using that alternate port. `docker compose down` preserves data; a second `up -d --wait` should recover the same synthetic documents. Cleanup with volume deletion is appropriate only for a positively identified disposable test project.

## Dependency and image review

Use `pip-audit --disable-pip --no-deps -r requirements.lock` and the equivalent command for `gateway/requirements.lock` in a dedicated audit environment. Scan the actual built images with Trivy, for example `trivy image --scanners vuln,secret northstar-docops:local` and `trivy image --scanners vuln,secret northstar-gateway:local`. Record immutable image IDs/digests and scanner database dates. Scan pinned PostgreSQL and Redis images too. `trivy image --format cyclonedx northstar-docops:local` produces an SBOM.

Do not publish raw scanner secret matches or full private runtime inspection output. Record per-finding version, fixed release, affected path, reachability and control evidence. A clean package audit alone does not establish container or application safety.
