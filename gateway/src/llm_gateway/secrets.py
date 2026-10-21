"""Read mounted secrets without placing their values in process environments."""

import os
from pathlib import Path


def secret_value(name: str, maximum_bytes: int = 16_384) -> str:
    direct = os.environ.get(name, "")
    filename = os.environ.get(name + "_FILE", "")
    if direct and filename:
        raise RuntimeError(f"Configure exactly one of {name} or {name}_FILE")
    if not filename:
        return direct
    try:
        path = Path(filename)
        if not path.is_absolute() or not path.is_file() or path.stat().st_size > maximum_bytes:
            raise ValueError
        with path.open("rb") as stream:
            content = stream.read(maximum_bytes + 1)
        if len(content) > maximum_bytes:
            raise ValueError
        value = content.decode("utf-8").rstrip("\r\n")
        if not value or "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError
        return value
    except (OSError, ValueError, UnicodeError):
        raise RuntimeError(f"Mounted secret {name}_FILE is unreadable or invalid") from None
