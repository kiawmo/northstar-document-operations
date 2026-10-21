# Northstar LLM Gateway

An authenticated HTTP boundary for schema-constrained extraction. The complete gateway source, provider adapters and tests are included here so deployments do not depend on a separately hosted private service.

The default production provider is `codex_cli`. Its argument array, JSON schema, input, timeout and process tree are controlled by the gateway. It disables tool features, isolates request directories and rejects unexpected tool events. The reviewed CLI version is **0.155.1**; the image verifies its archive SHA256. A different version fails closed until its compatibility/security controls are reviewed.

The CLI session must belong to an authorized account. The optional `compose.codex.yml` mounts an existing auth file read-only; the entrypoint seeds a dedicated private state volume and preserves subsequent token refreshes. Never mount a whole user home, browser profile, Docker socket or unrelated repository. Do not commit auth files. Back up and protect the state volume as credential material.

Compose enables an init process to reap orphaned descendants after timeouts. Supply `--init` when running these images directly with Docker; other orchestrators must provide equivalent child reaping. The process-tree timeout test checks that both the provider and its child are actually gone, including zombie state.

`GET /health` reports process liveness; `GET /ready` checks the selected provider. `POST /generate` requires a bearer token and accepts a bounded task, input and JSON schema. Errors distinguish retryable provider failures from invalid input. Queue waits, process output and retries are bounded. Logs omit prompts, source documents and tokens.

Fixture mode is explicitly selected for deterministic tests. OpenAI, Anthropic and Gemini REST adapters remain available in source, but require `ALLOW_PAID_LLM=true`, a model and a private key. The supplied Compose configuration sets this flag false and includes no paid keys. Northstar never switches to these adapters automatically.

Run tests with `python -m pytest -q` from this directory after installing `requirements-dev.lock` in a separate environment. The outer [testing guide](../docs/TESTING.md) includes container commands. This component shares the repository's AGPL-3.0-or-later license.
