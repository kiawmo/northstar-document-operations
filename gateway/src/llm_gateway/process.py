"""Bounded subprocess execution. No input ever becomes shell syntax."""

import asyncio
import contextlib
import os
import signal
import subprocess
from dataclasses import dataclass

import psutil

from .errors import ErrorCode, GatewayError


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


async def kill_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        # taskkill is an explicit executable with a numeric pid, not a shell command.
        taskkill = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe"
        )
        killer = await asyncio.create_subprocess_exec(
            taskkill,
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(killer.wait(), 5)
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, psutil.NoSuchProcess):
        parent = psutil.Process(process.pid)
        for child in parent.children(recursive=True):
            with contextlib.suppress(psutil.NoSuchProcess):
                child.kill()
        parent.kill()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), 5)


async def run_process(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    stdin: bytes = b"",
    timeout: float = 120,
    output_limit: int = 2_000_000,
) -> ProcessResult:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
    except (OSError, ValueError):
        raise GatewayError(
            ErrorCode.CONFIGURATION,
            "Unable to start the configured provider executable",
            status=503,
        ) from None

    async def read_bounded(stream: asyncio.StreamReader) -> bytes:
        buffer = bytearray()
        while chunk := await stream.read(65_536):
            buffer.extend(chunk)
            if len(buffer) > output_limit:
                raise GatewayError(
                    ErrorCode.INVALID_OUTPUT, "Provider output exceeded the size limit"
                )
        return bytes(buffer)

    async def write_input() -> None:
        try:
            process.stdin.write(stdin)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await process.stdin.wait_closed()

    operations = [
        asyncio.create_task(read_bounded(process.stdout)),
        asyncio.create_task(read_bounded(process.stderr)),
        asyncio.create_task(write_input()),
        asyncio.create_task(process.wait()),
    ]
    try:
        output, errors, _, code = await asyncio.wait_for(asyncio.gather(*operations), timeout)
        return ProcessResult(code, output, errors)
    except TimeoutError:
        await asyncio.shield(kill_process_tree(process))
        raise GatewayError(
            ErrorCode.TIMEOUT,
            "Provider exceeded its execution deadline",
            retryable=True,
            status=504,
        ) from None
    except BaseException:
        await asyncio.shield(kill_process_tree(process))
        raise
    finally:
        for task in operations:
            if not task.done():
                task.cancel()
        await asyncio.gather(*operations, return_exceptions=True)
        # A cancelled bounded reader may leave a Windows Proactor pipe open after
        # its child has exited. Drain the small remaining pipe buffers to EOF before
        # returning to callers that may close their event loop immediately.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.gather(process.stdout.read(), process.stderr.read()), 2)
