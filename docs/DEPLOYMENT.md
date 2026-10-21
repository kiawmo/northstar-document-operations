# Deployment

## Supported operating model

Northstar supports one organization's trusted operators on private ingress. Default Compose publishes only `127.0.0.1:18113`, uses separate PostgreSQL/Redis volumes and generates different database, Redis, workspace and gateway secrets. It creates no public firewall rule, host service or global Docker change. Keep the host and container engine patched.

Production operation requires a private network/VPN or an authenticated reverse proxy under your control. For remote browser access, terminate valid TLS at that boundary, enforce identity there and restrict reachability to authorized reviewers. Keep the application key as a second boundary. Never expose the editor/API directly to the public Internet. For several API processes use an edge rate limiter; the built-in limit is per process and peer address. Do not trust arbitrary forwarded-IP headers.

## Release procedure

1. Check out a reviewed release commit. Inspect lockfiles, Dockerfile arguments and scanner reports. Run the documented container tests on the target architecture.
2. Choose storage capacity, document retention, operator access, backup interval and restore objectives for your organization. Source documents remain in SQL until intentionally removed under an approved retention procedure; there is no automatic deletion policy.
3. Run the bootstrap script in a protected local directory. Supply your own Codex credentials through `compose.codex.yml`, or deliberately configure another supported provider. Obtain permission before transmitting document content to any external model.
4. Build both application and gateway images; pin the resulting images by registry digest in your deployment override. The base image references and Codex archive checksum are pinned in source. Rebuild and re-scan to incorporate OS security updates; a digest pin is not a promise of permanent security.
5. Start with `docker compose -f compose.yaml -f compose.codex.yml up -d --wait`. Check API `/ready`, gateway `/ready`, worker heartbeat and an actual synthetic extraction. Exercise a human review and duplicate receipt before enabling inbox polling.
6. Configure monitoring and encrypted backups outside the container host. Test restore into a separate Compose project. Restrict access to backup contents, including document bytes and credential state.

## Isolation and permissions

API/worker/parser use UID 10001, read-only root filesystems, dropped capabilities, no-new-privileges, memory/process limits and bounded temporary storage. The parser has `network_mode: none`, no secrets and only the socket volume. PostgreSQL has an application role without superuser/role/database-creation rights. Redis is password protected and internal. The API has a dedicated ingress bridge for its loopback-published port; this bridge also permits API egress. The gateway has a separate outbound bridge, while the worker uses internal networks by default. The optional mail overlay permits worker egress for configured mailbox hosts. No arbitrary document-controlled URL fetching is implemented.

Compose secrets are files mounted read-only; local Compose does not encrypt them at rest. On Unix the bootstrap uses a host-owner-only 0700 directory and 0444 files inside it: the parent prevents other host users from traversing it, while individual read-only mounts work across differing container UIDs. File-backed local Compose secrets do not remap ownership. On Windows the directory ACL is restricted to the current user's SID. Host administrators remain privileged. Use your platform's secret manager/encrypted host volume for production. Do not share `.secrets`, `.env`, credential volumes or database dumps.

## Operational limits

The current release is designed for low-volume invoice intake with a single parser and a SQL-backed durable queue. It has no measured public throughput/SLA, automatic multi-region failover, user-management service or malware scanner. Limit incoming document sources, add scanning/quarantine appropriate to your threat model and set edge request/connection/time limits. SQL audit events are application append-only; a privileged database administrator can change them. Export to an independently controlled audit sink if tamper resistance is required.
