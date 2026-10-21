from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets


def environment_secret(name: str, default: str = "") -> str:
    """Support read-only Compose/Kubernetes secrets without logging their contents."""
    path = os.getenv(name + "_FILE")
    if not path:
        return os.getenv(name, default)
    with Path(path).open(encoding="utf-8") as source:
        raw = source.read(8193)
    value = raw.strip()
    if not value or len(raw) > 8192:
        raise ValueError(f"{name} secret file must contain 1-8192 characters")
    return value


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: environment_secret("DOCUMENT_DATABASE_URL", "sqlite:///data/docops.db"), repr=False
    )
    database_schema: str = field(default_factory=lambda: os.getenv("DOCUMENT_DATABASE_SCHEMA", "docops"))
    api_key: str = field(default_factory=lambda: environment_secret("DOCUMENT_API_KEY"), repr=False)
    provider: str = field(default_factory=lambda: os.getenv("DOCUMENT_LLM_PROVIDER", "gateway") or "gateway")
    gateway_url: str = field(default_factory=lambda: os.getenv("LLM_GATEWAY_URL", "http://127.0.0.1:8100"))
    gateway_key: str = field(default_factory=lambda: environment_secret("GATEWAY_API_KEY"), repr=False)
    redis_url: str = field(default_factory=lambda: environment_secret("DOCUMENT_REDIS_URL"), repr=False)
    redis_prefix: str = field(default_factory=lambda: os.getenv("DOCUMENT_REDIS_PREFIX", "docops"))
    worker_heartbeat_file: str = field(default_factory=lambda: os.getenv("DOCUMENT_WORKER_HEARTBEAT_FILE", ""))
    parser_url: str = field(default_factory=lambda: os.getenv("DOCUMENT_PARSER_URL", ""))
    parser_socket: str = field(default_factory=lambda: os.getenv("DOCUMENT_PARSER_SOCKET", ""))
    mail_profile_file: str = field(default_factory=lambda: os.getenv("DOCUMENT_MAIL_PROFILE_FILE", ""))
    inbox_poll_seconds: float = field(
        default_factory=lambda: max(30, float(os.getenv("DOCUMENT_INBOX_POLL_SECONDS", "60")))
    )
    tesseract: str = field(
        default_factory=lambda: os.getenv(
            "TESSERACT_PATH", r"C:\Program Files\Tesseract-OCR\tesseract.exe" if os.name == "nt" else "tesseract"
        )
    )
    lease_seconds: float = 300
    max_attempts: int = 3
    retry_base: float = 1
    max_upload_bytes: int = 5 * 1024 * 1024
    worker_enabled: bool = field(default_factory=lambda: os.getenv("DOCUMENT_WORKER_ENABLED", "1") == "1")

    def ensure_api_key(self) -> None:
        if self.api_key:
            return
        if os.getenv("DOCUMENT_BIND_ALL") == "1":
            raise ValueError("A non-loopback listener requires an explicitly configured DOCUMENT_API_KEY")
        key_path = Path("data/.local-api-key")
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.is_symlink():
            raise ValueError("Refusing a symlinked local API key")
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            with key_path.open(encoding="utf-8") as source:
                value = source.read(8193).strip()
            if not value or len(value) > 8192:
                raise ValueError("Existing local API key is empty or invalid") from None
            self.api_key = value
        else:
            self.api_key = secrets.token_urlsafe(32)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(self.api_key)
