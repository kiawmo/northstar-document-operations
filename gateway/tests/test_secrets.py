import os

import pytest

from llm_gateway.config import Settings
from llm_gateway.container import initialize_auth
from llm_gateway.secrets import secret_value


def test_secret_file_authorizes_nonloopback_without_env_value(tmp_path, monkeypatch):
    token = tmp_path / "key"
    token.write_text("synthetic-container-token\n")
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("GATEWAY_API_KEY_FILE", str(token))
    monkeypatch.setenv("GATEWAY_HOST", "0.0.0.0")  # noqa: S104 - verifies authenticated binding
    settings = Settings.from_env()
    assert settings.api_key.get_secret_value() == "synthetic-container-token"
    assert "GATEWAY_API_KEY" not in os.environ
    assert "synthetic-container-token" not in repr(settings)


def test_conflicting_secret_sources_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / "token"
    path.write_text("file-secret")
    monkeypatch.setenv("GATEWAY_API_KEY", "environment-secret")
    monkeypatch.setenv("GATEWAY_API_KEY_FILE", str(path))
    with pytest.raises(RuntimeError, match="exactly one") as error:
        secret_value("GATEWAY_API_KEY")
    assert "environment-secret" not in str(error.value)
    assert "file-secret" not in str(error.value)


@pytest.mark.parametrize("content", [b"", b"x" * 16_385, b"secret\x00", b"line\nbreak", b"\xff"])
def test_invalid_mounted_secrets_do_not_echo_values(tmp_path, monkeypatch, content):
    path = tmp_path / "secret"
    path.write_bytes(content)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("GATEWAY_API_KEY_FILE", str(path))
    with pytest.raises(RuntimeError, match="unreadable or invalid") as error:
        secret_value("GATEWAY_API_KEY")
    assert str(path) not in str(error.value)


def test_container_only_copies_auth_and_preserves_codex_refresh(tmp_path, monkeypatch):
    source, home = tmp_path / "supplied-auth", tmp_path / "state"
    source.write_bytes(b'{"synthetic_auth":"initial"}')
    monkeypatch.setenv("CODEX_AUTH_SOURCE", str(source))
    monkeypatch.setenv("CODEX_HOME", str(home))
    initialize_auth()
    assert list(x.name for x in home.iterdir()) == ["auth.json"]
    assert (home / "auth.json").read_bytes() == source.read_bytes()
    (home / "auth.json").write_bytes(b'{"synthetic_auth":"refreshed"}')
    initialize_auth()
    assert b"refreshed" in (home / "auth.json").read_bytes()
    if os.name != "nt":
        assert (home.stat().st_mode & 0o777) == 0o700
        assert ((home / "auth.json").stat().st_mode & 0o777) == 0o600


def test_container_auth_size_is_bounded(tmp_path, monkeypatch):
    source = tmp_path / "too-large-auth"
    source.write_bytes(b"x" * 65_537)
    monkeypatch.setenv("CODEX_AUTH_SOURCE", str(source))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "state"))
    with pytest.raises(RuntimeError, match="Cannot initialize"):
        initialize_auth()
    assert not (tmp_path / "state/auth.json").exists()
