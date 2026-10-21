# Setup

## Container installation

Use Docker Compose v2 and x86-64 Linux containers. Docker Desktop/WSL on Windows is supported; choose a container disk location with sufficient free space before pulling images. The application does not alter the host's Docker configuration or touch other Compose projects.

When Docker Engine runs inside a WSL distribution, execute Docker/build/probe commands inside that same distribution. For a checkout on a Windows-mounted drive, run bootstrap with Windows Python so it applies the Windows directory ACL. Alternatively keep the checkout and secrets on the WSL Linux filesystem and run bootstrap there. Linux mode bits on a Windows-mounted drive are not a substitute for a Windows ACL. Docker Desktop's normal Windows integration does not need a custom forwarding service.

Run all commands from the repository root. First run `python scripts/bootstrap.py --fixture`, then `docker compose pull --ignore-buildable`, `docker compose build`, and `docker compose up -d --wait`. `python scripts/smoke.py` submits a new fictional invoice through the authenticated API, waits for completion and verifies idempotency, source retrieval and unauthorized-access rejection. Repeated smoke runs use distinct invoice numbers.

The dashboard is at http://127.0.0.1:18113. Read `.secrets/api_key` locally and paste its contents in the password field. Do not put keys in URLs, screenshots or issue reports. Change the published loopback port using `DOCOPS_PORT` in a private `.env` file. The API, worker, parser and gateway are separate services; PostgreSQL and Redis have no host listeners.

For live extraction, run `python scripts/bootstrap.py` to select `codex_cli`. Set `CODEX_AUTH_FILE` in `.env` to an absolute private auth-file path; include both files with `docker compose -f compose.yaml -f compose.codex.yml up -d --build --wait`. The gateway's provider readiness must pass. Authentication belongs to the operator, is not distributed, and must comply with the provider's account and data-processing rules. The pinned gateway's reviewed CLI version is 0.155.1; upgrades require compatibility tests.

For complete deterministic scenarios, use the test image in a writable disposable working directory. These runs use fresh SQLite databases, not operational PostgreSQL records. The [testing guide](TESTING.md) gives the exact executable commands used by validation.

## Native development

Install Python 3.12+ and Tesseract with English data. Set `TESSERACT_PATH` to the executable when it is not on PATH. Create and activate `.venv`; install `requirements-dev.lock` with `python -m pip install --require-hashes -r requirements-dev.lock`. Linux wheels are hash locked; Windows-only platform packages are included where needed. Container tests are authoritative for Linux resource limits.

`python -m docops serve --llm fixture` starts a loopback development server and integrated worker with SQLite under `data/`. It generates a local operator key at `data/.local-api-key`. `python -m docops fixtures` regenerates the fictional PDF, image, text and email samples; no customer data is bundled. Native parsing runs in-process and is intended for trusted development inputs. Use the isolated parser stack for untrusted operational documents.

## API example

```python
import os
import httpx

headers = {"X-API-Key": os.environ["DOCUMENT_API_KEY"], "Idempotency-Key": "supplier-intake-001"}
with open("invoice.pdf", "rb") as source:
    response = httpx.post(
        "http://127.0.0.1:18113/api/documents/upload",
        params={"filename": "invoice.pdf", "client": "accounts"},
        headers={**headers, "Content-Type": "application/pdf"},
        content=source.read(), timeout=30,
    )
response.raise_for_status()
print(response.json()["id"])
```

Set environment credentials privately; never paste them into source. See `/docs` locally for the generated API contract. Accepted input is limited to 5 MiB, five unencrypted PDF pages, supported text/images and bounded raster/text output. Unsupported files fail safely rather than being converted without limits.
