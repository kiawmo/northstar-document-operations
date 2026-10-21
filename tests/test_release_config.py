import os

import pytest

from docops.config import Settings


def test_public_bind_requires_operator_key(monkeypatch):
    monkeypatch.setenv("DOCUMENT_BIND_ALL", "1")
    with pytest.raises(ValueError, match="explicitly configured"):
        Settings(api_key="").ensure_api_key()
    Settings(api_key="test-operator-key").ensure_api_key()


def test_local_key_creation_is_private_and_repeat_safe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOCUMENT_BIND_ALL", raising=False)
    first, second = Settings(api_key=""), Settings(api_key="")
    first.ensure_api_key()
    second.ensure_api_key()
    assert len(first.api_key) > 32 and first.api_key == second.api_key
    if os.name != "nt":
        assert (tmp_path / "data/.local-api-key").stat().st_mode & 0o777 == 0o600


def test_empty_existing_key_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOCUMENT_BIND_ALL", raising=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/.local-api-key").touch()
    with pytest.raises(ValueError, match="empty or invalid"):
        Settings(api_key="").ensure_api_key()
