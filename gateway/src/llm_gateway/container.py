"""Seed only explicitly supplied private Codex auth into its dedicated mutable state."""

import os
from pathlib import Path


def initialize_auth() -> None:
    source_name = os.environ.get("CODEX_AUTH_SOURCE", "")
    home_name = os.environ.get("CODEX_HOME", "")
    if not source_name or not home_name:
        raise RuntimeError("CODEX_AUTH_SOURCE and dedicated CODEX_HOME are required")
    source, home = Path(source_name), Path(home_name)
    if not source.is_absolute() or not home.is_absolute():
        raise RuntimeError("Container auth paths must be absolute")
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    home.chmod(0o700)
    target = home / "auth.json"
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise RuntimeError("Private Codex auth state is invalid")
        target.chmod(0o600)
        return  # Preserve refresh-token rotation performed by Codex itself.
    try:
        if not source.is_file() or source.stat().st_size > 65_536:
            raise ValueError
        with source.open("rb") as stream:
            value = stream.read(65_537)
        if not value or len(value) > 65_536:
            raise ValueError
        # Do not deserialize or print auth tokens. Copy only the authorized file.
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
    except (OSError, ValueError):
        raise RuntimeError("Cannot initialize private Codex auth state") from None


def main() -> None:
    initialize_auth()
    temporary = Path(os.environ.get("LLM_TEMP_DIR", ""))
    if not temporary.is_absolute() or temporary.is_symlink():
        raise RuntimeError("Container LLM temp directory must be absolute")
    temporary.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary.chmod(0o700)
    from .cli import main as serve

    serve()


if __name__ == "__main__":
    main()
