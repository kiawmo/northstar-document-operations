import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path

import psutil
import pytest

from llm_gateway import GatewayError, Settings
from llm_gateway.errors import ErrorCode
from llm_gateway.process import run_process
from llm_gateway.providers.codex import codex_arguments, resolve_executable, restricted_environment


def test_stdin_is_literal_and_never_shell_syntax(tmp_path):
    payload = b"$(echo secret); `write-secret`; & del *.py"
    result = asyncio.run(
        run_process(
            [sys.executable, "-c", "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            cwd=str(tmp_path),
            env=dict(os.environ),
            stdin=payload,
        )
    )
    assert result.returncode == 0
    assert result.stdout == payload


def test_process_streams_are_bounded(tmp_path):
    with pytest.raises(GatewayError) as error:
        asyncio.run(
            run_process(
                [sys.executable, "-c", "print('x'*200000)"],
                cwd=str(tmp_path),
                env=dict(os.environ),
                output_limit=1024,
            )
        )
    assert error.value.code == ErrorCode.INVALID_OUTPUT


def test_timeout_kills_child_process_tree(tmp_path):
    child_script = (
        "import os,pathlib,time;"
        "ready=pathlib.Path('child.pid.tmp');ready.write_text(str(os.getpid()));"
        "ready.replace('child.pid');time.sleep(120)"
    )
    script = (
        "import os,subprocess,sys,time,pathlib;"
        "ready=pathlib.Path('parent.pid.tmp');ready.write_text(str(os.getpid()));"
        "ready.replace('parent.pid');"
        f"subprocess.Popen([sys.executable,'-c',{child_script!r}]);time.sleep(120)"
    )

    async def exercise():
        runner = asyncio.create_task(
            run_process(
                [sys.executable, "-c", script], cwd=str(tmp_path), env=dict(os.environ), timeout=30
            )
        )
        observed = []
        try:
            # A deadline shorter than cold interpreter startup did not exercise
            # child termination. Confirm both real processes first, within a
            # separate bounded startup window; production timeouts are unchanged.
            startup_deadline = time.monotonic() + 20
            while not (tmp_path / "child.pid").exists():
                if runner.done():
                    await runner
                    pytest.fail("Provider exited before the child readiness signal")
                assert time.monotonic() < startup_deadline, "Child startup deadline exceeded"
                await asyncio.sleep(0.05)
            observed = [
                psutil.Process(int((tmp_path / name).read_text()))
                for name in ("parent.pid", "child.pid")
            ]
            parent, child = observed
            # Windows virtualenv executables can introduce an interpreter launcher
            # between these two processes. The child must remain in the same tree.
            assert parent.pid in {ancestor.pid for ancestor in child.parents()}
            assert all(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in observed)
            with pytest.raises(GatewayError) as error:
                await asyncio.wait_for(asyncio.shield(runner), timeout=45)
            assert error.value.code == ErrorCode.TIMEOUT
            reap_deadline = time.monotonic() + 10
            while any(p.is_running() for p in observed) and time.monotonic() < reap_deadline:
                await asyncio.sleep(0.05)
            assert not parent.is_running(), "Timed-out provider parent was not reaped"
            assert not child.is_running(), "Timed-out provider child was not reaped"
        finally:
            # Assertion/startup failures must not leave sleeping test processes.
            # This cleanup runs after the termination assertions, so it cannot
            # make a broken production termination path appear to pass.
            if not runner.done():
                runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
            if not observed and (tmp_path / "parent.pid").exists():
                with contextlib.suppress(psutil.NoSuchProcess):
                    parent = psutil.Process(int((tmp_path / "parent.pid").read_text()))
                    observed = [parent, *parent.children(recursive=True)]
            for process in reversed(observed):
                with contextlib.suppress(psutil.NoSuchProcess):
                    if process.is_running():
                        process.kill()
            if observed:
                await asyncio.to_thread(psutil.wait_procs, observed, timeout=5)

    asyncio.run(exercise())


def test_unrelated_app_secrets_are_not_in_codex_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-real")
    monkeypatch.setenv("CODEX_API_KEY", "test-only-not-real")
    monkeypatch.setenv("CUSTOMER_DATABASE_PASSWORD", "test-only-not-real")
    env = restricted_environment()
    assert "OPENAI_API_KEY" not in env
    assert "CODEX_API_KEY" not in env
    assert "CUSTOMER_DATABASE_PASSWORD" not in env


def test_security_controls_are_cli_configuration(tmp_path):
    args = codex_arguments(Path("codex.exe"), tmp_path)
    assert "--ignore-user-config" in args
    assert "--ephemeral" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "mcp_servers={}" in args
    assert 'web_search="disabled"' in args
    assert "shell_tool" in args
    assert "apps" in args
    assert "plugins" in args
    assert args[-1] == "-"
    assert "--dangerously-bypass-approvals-and-sandbox" not in args


def test_windows_script_wrapper_is_rejected(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows native executable rule")
    path = tmp_path / "codex.cmd"
    path.write_text("@echo unsafe")
    with pytest.raises(GatewayError) as error:
        resolve_executable(str(path))
    assert error.value.code == ErrorCode.CONFIGURATION


def test_settings_never_print_credentials():
    config = Settings(api_key="not-real-test-key", openai_key="also-not-real")
    assert "not-real-test-key" not in repr(config)
    assert "also-not-real" not in config.model_dump_json()
