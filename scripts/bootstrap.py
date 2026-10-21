"""Create deployment-specific local secrets without replacing existing credentials."""

import argparse
import csv
import io
import os
from pathlib import Path
import secrets
import subprocess


def main(root: Path | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true", help="Explicitly select deterministic test extraction")
    args = parser.parse_args()
    root = root or Path(__file__).resolve().parents[1]
    directory = root / ".secrets"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink():
        raise SystemExit("Refusing a symlinked secrets directory")
    if os.name == "nt":
        identity = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
        sid = next(csv.reader(io.StringIO(identity.strip())))[1]
        subprocess.run(
            ["icacls", str(directory), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
            check=True, capture_output=True,
        )
    else:
        directory.chmod(0o700)

    def private_file(name, value):
        path = directory / name
        if path.is_symlink():
            raise SystemExit("Refusing a symlinked secret file")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if not path.is_file() or not path.read_text().strip():
                raise SystemExit("Existing secret is empty or invalid; resolve it without automatic replacement") from None
        else:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
                output.write(value + "\n")
        if os.name != "nt":
            # The 0700 parent protects host traversal. File-backed Compose secrets
            # retain host UID, so read-only files must support each service UID.
            path.chmod(0o444)
        return path.read_text().strip()

    for name in ("api_key", "gateway_api_key", "postgres_admin_password", "postgres_password", "redis_password"):
        private_file(name, secrets.token_hex(32))
    password = (directory / "postgres_password").read_text().strip()
    redis_password = (directory / "redis_password").read_text().strip()
    private_file("database_url", f"postgresql://docops:{password}@postgres:5432/docops")
    private_file("redis_url", f"redis://:{redis_password}@redis:6379/0")
    env = root / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    lines = [line for line in lines if not line.startswith(("LLM_PROVIDER=", "DOCUMENT_LLM_PROVIDER="))]
    lines.append("LLM_PROVIDER=" + ("fixture" if args.fixture else "codex_cli"))
    lines.append("DOCUMENT_LLM_PROVIDER=" + ("fixture" if args.fixture else "gateway"))
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Local secrets prepared; existing credentials preserved. No secret values printed.")
    print("Extraction provider: " + ("fixture (explicit test mode)" if args.fixture else "codex_cli"))


if __name__ == "__main__":
    main()
