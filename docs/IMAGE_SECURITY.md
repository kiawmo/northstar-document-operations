# Runtime image review — 1.0.0

Reviewed 2026-10-22 using Trivy 0.75.0, vulnerability database updated 2026-10-21 19:18 UTC. Complete package/version/advisory/fixed-version rows and exact built image identities are in [image-findings.json](image-findings.json). Counts are installed-package findings, not distinct remotely exploitable defects. No `.trivyignore` is used.

| Runtime | Scanned image ID | Critical | High |
| --- | --- | ---: | ---: |
| docops | `sha256:4a066c57c6d6ee5ad7defd88f6a9260be918a16050fbf0b8cea3bff7de116410` | 1 | 70 |
| gateway | `sha256:b6f2afa3dd273f42d3da09911ec1c1d9b2ceb18ab6fee951774f794d4dfcc259` | 0 | 44 |
| postgres | `sha256:47d2280886eb73c507b5b1e3fdc2584786e6e028dbbd5597a6d3582a406553b9` | 1 | 61 |
| redis | `sha256:b51665e66f00759be7c3152ad5ac3c66fb2f619c13ef62dea7cc1f9914524635` | 0 | 0 |

## Application dependencies

`pip-audit 2.10.1 --disable-pip --no-deps -r requirements.lock` and the gateway equivalent reported **zero known findings** across the complete production locks. Pillow was updated to 13.0.0 and certifi to 2026.10.19. Unused pip/setuptools/wheel are removed from production images. Development/test images retain build tools and are not the deployed runtime.

## High-impact findings and configured reachability

These are configuration-specific dispositions, not claims that upstream packages are fixed. Every Critical/High row maps to one control group below. Expanding inputs, enabling XML, mounting host sockets, replacing models or changing privileges invalidates this assessment.

| Group | Required prerequisite and why absent |
| --- | --- |
| xml | No XML ingestion or SQL XML interface is exposed. Supported PDF/image/text paths use MuPDF/Pillow and fixed OCR; parser hooks observed no libxml2 entry points with a working positive control. PostgreSQL executes parameterized application SQL, not user SQL. |
| ocr | Requires an attacker-controlled OCR traineddata/model. Only the image-installed English model is selected; callers cannot upload model/config files or change language/path. Read-only parser filesystem and observed fixed eng.traineddata plus generated PNG. |
| tiff | The ingestion contract rejects TIFF and tiffcrop is never invoked. Accepted images are decoded with Pillow and re-encoded as PNG before OCR; PDF rasterization uses MuPDF. Signature, raster and subprocess bounds apply. |
| x11 | Requires a malicious X server. Containers are headless, have no DISPLAY, X socket or X server, and the parser has no network. |
| curl | No curl command, SFTP/SCP URL workflow or HTTP Negotiate authentication exists. Application HTTP uses HTTPX/urllib with configured endpoints; no uploaded URL is fetched by libcurl. |
| privileged | Requires privileged mount, nsenter, ACL, cgroup or local administration operations. Application/gateway/parser run non-root with all capabilities dropped, no-new-privileges and read-only roots. PostgreSQL runs as postgres, not root. No host namespace/cgroup/socket is mounted and uploaded paths do not reach these tools. |
| homed | Requires systemd-homed and a local home-record workflow. No systemd/homed/udev daemon, D-Bus service or host socket is run or mounted in these containers. |
| ncurses | The vulnerable infocmp administrative command is not invoked; no uploaded terminfo or terminal capability data reaches it. Services run non-interactively. |
| perl | The Perl Archive::Tar workflow is not used. The application does not ingest tar archives or execute Perl on documents. |
| gpg | Requires a tpm2daemon PKDECRYPT command for TPM-backed keys. No TPM device, GnuPG agent, key operation or untrusted OpenPGP workflow exists in the database service. |

The Critical libxml2 advisory is **CVE-2026-6653** (`xmlParseInternalSubset` UAF). Debian describes it as a minor issue without a current trixie DSA; scanner severity is retained rather than rewritten. See the [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-6653). The OCR model prerequisite is documented in the [Tesseract advisory](https://github.com/tesseract-ocr/tesseract/security/advisories/GHSA-7j76-5rq5-5jg8); [Debian tracks the installed release](https://security-tracker.debian.org/tracker/CVE-2026-73066). The PostgreSQL GnuPG finding requires TPM-backed decryption through tpm2daemon, as described by the [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-24882).

`validate_parser_calls.py` instruments the included C hooks around XML/D-Bus/file entry points for synthetic TXT, PDF, scanned PDF, PNG and PDF preview. A direct XML call is the positive control. Observed supported paths made no XML/D-Bus calls; OCR loaded only the fixed English model and generated PNG. This dynamic sample supports the static call-path review; it is not exhaustive proof over all malicious files. The parser additionally has no network or credentials and has per-child resource/deadline limits. The diagnostic library is not part of the runtime.

## Image secret finding

Application, gateway and Redis scans contain no secret matches. The upstream PostgreSQL image includes a **public example snakeoil TLS private key in a lower layer**. The derived image removes that key and certificate from the active filesystem and removes unused gosu. The key is not a Northstar credential, is never configured for use, and must never be used for TLS. It remains visible to a layer scanner because deleting a file does not erase its upstream layer. Do not publish raw key matches. PostgreSQL has no published port; use authenticated TLS ingress for the API and separately configure real database TLS if extending beyond the private network.

## Acceptance and residual risk

No reviewed Critical/High finding has its required input/privilege path enabled in the documented configuration. This is a scoped engineering assessment, not a penetration-test certificate or a guarantee of absence of vulnerabilities. Lower-severity findings remain visible in the JSON for vendor maintenance. Malicious documents may still exhaust or crash the bounded parser; jobs retain their source, retry within limits and escalate to review. Container/kernel isolation and the trusted host remain security assumptions. A database administrator can alter records. This release is not anonymous public SaaS and has no per-client RBAC.

Rebuilds can change OS packages installed by apt even with pinned base images. Scan the images actually deployed and repeat this assessment after upgrades/configuration changes. Use `trivy image --scanners vuln,secret --format json IMAGE_ID` and produce an SBOM with `trivy image --format cyclonedx IMAGE_ID`. Keep raw private scanner output outside public issues.
