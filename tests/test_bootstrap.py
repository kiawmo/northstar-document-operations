import os

from scripts.bootstrap import main


def test_bootstrap_is_repeat_safe_and_emits_unix_secret_lines(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["bootstrap.py", "--fixture"])
    main(tmp_path)
    files = {path.name: path.read_bytes() for path in (tmp_path / ".secrets").iterdir()}
    assert len(files) == 7
    assert all(value.endswith(b"\n") and b"\r" not in value for value in files.values())
    assert all(len(value.strip()) >= 32 for value in files.values())
    main(tmp_path)
    assert files == {path.name: path.read_bytes() for path in (tmp_path / ".secrets").iterdir()}
    assert "DOCUMENT_LLM_PROVIDER=fixture" in (tmp_path / ".env").read_text()
    if os.name != "nt":
        assert (tmp_path / ".secrets").stat().st_mode & 0o777 == 0o700
        assert all(path.stat().st_mode & 0o777 == 0o444 for path in (tmp_path / ".secrets").iterdir())


def test_bootstrap_production_selection_preserves_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["bootstrap.py", "--fixture"])
    main(tmp_path)
    original = (tmp_path / ".secrets/api_key").read_bytes()
    monkeypatch.setattr("sys.argv", ["bootstrap.py"])
    main(tmp_path)
    assert (tmp_path / ".secrets/api_key").read_bytes() == original
    env = (tmp_path / ".env").read_text()
    assert "LLM_PROVIDER=codex_cli" in env and "DOCUMENT_LLM_PROVIDER=gateway" in env
