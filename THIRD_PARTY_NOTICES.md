# Third-party notices

Northstar's original code is AGPL-3.0-or-later. Dependencies retain their own copyrights, licenses and notices. The lockfiles identify exact Python releases and hashes; container build definitions identify base images and the Codex archive.

| Component | License / upstream |
| --- | --- |
| PyMuPDF / MuPDF | AGPL-3.0 or commercial terms; https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright |
| Tesseract OCR | Apache-2.0; https://github.com/tesseract-ocr/tesseract |
| FastAPI, Starlette, Pydantic, HTTPX, Uvicorn | MIT / BSD-family; consult each installed distribution's metadata |
| Psycopg | LGPL-3.0; bundled libpq and related libraries retain their own notices |
| Redis server 8 | Tri-licensed upstream; the AGPLv3 option is applicable to this AGPL project; https://redis.io/legal/licenses/ |
| PostgreSQL | PostgreSQL License; https://www.postgresql.org/about/licence/ |
| Codex CLI | Apache-2.0; https://github.com/openai/codex |
| Python, Debian and Alpine packages | Individual upstream licenses preserved in their installed distributions |

This list explains prominent boundaries; it does not replace the complete notices installed with each dependency. Container images retain `/usr/share/doc`, package metadata and Python `.dist-info` license files. A software bill of materials is produced by the documented scanner command. Obtain appropriate third-party commercial permissions if AGPL obligations do not suit your deployment.
