"""Private, no-secret parser guardian. Child crashes cannot obtain queue credentials."""

import asyncio
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI(title="Isolated Document Parser", docs_url=None, redoc_url=None, openapi_url=None)
gate = asyncio.Semaphore(1)
maximum_source = 5 * 1024**2


@app.get("/health")
def health():
    return {"status": "ok", "isolated_parser": True, "child_memory_mib": 768, "child_cpu_seconds": 45}


async def parse(request: Request, operation: str):
    content_type = request.headers.get("content-type", "").split(";")[0]
    allowed = {"application/pdf", "image/png", "image/jpeg", "text/plain"}
    if content_type not in allowed or (operation == "preview" and content_type != "application/pdf"):
        raise HTTPException(422, "Unsupported parser source")
    chunks, length = [], 0
    async for chunk in request.stream():
        length += len(chunk)
        if length > maximum_source:
            raise HTTPException(413, "Parser source exceeds 5 MB")
        chunks.append(chunk)
    if not length:
        raise HTTPException(422, "Empty parser source")
    payload = json.dumps(
        {
            "operation": operation,
            "content_type": content_type,
            "content": base64.b64encode(b"".join(chunks)).decode(),
        }
    ).encode()

    def child():
        return subprocess.run(
            [sys.executable, "-m", "docops.parser_task"],
            input=payload,
            capture_output=True,
            timeout=60,
            check=False,
        )

    async with gate:
        try:
            process = await asyncio.to_thread(child)
        except (OSError, subprocess.TimeoutExpired):
            raise HTTPException(503, "Bounded parser unavailable or timed out") from None
    if process.returncode or len(process.stdout) > 16 * 1024**2:
        raise HTTPException(503, "Bounded parser process failed; source retained upstream")
    try:
        result = json.loads(process.stdout)
        if result.get("error"):
            raise HTTPException(422 if result["error"] == "permanent" else 503, "Source parsing failed")
        if operation == "extract":
            if set(result) != {"text", "used_ocr"} or not isinstance(result["text"], str):
                raise ValueError("Invalid parser response")
            if len(result["text"]) > 100000 or type(result["used_ocr"]) is not bool:
                raise ValueError("Parser response exceeds boundary")
            return JSONResponse(result)
        png = base64.b64decode(result["png"], validate=True)
        if len(png) > 12 * 1024**2 or not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Invalid preview")
        return Response(png, media_type="image/png", headers={"X-Content-Type-Options": "nosniff"})
    except (ValueError, KeyError, TypeError):
        raise HTTPException(503, "Bounded parser returned invalid output") from None


@app.post("/extract")
async def extract(request: Request):
    return await parse(request, "extract")


@app.post("/preview")
async def preview(request: Request):
    return await parse(request, "preview")


if __name__ == "__main__":
    import uvicorn

    os.umask(0o077)
    address = Path("/ipc/parser.sock")
    address.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(address))
        address.chmod(0o600)
        uvicorn.Server(uvicorn.Config(app, limit_concurrency=8, timeout_keep_alive=2)).run(sockets=[listener])
