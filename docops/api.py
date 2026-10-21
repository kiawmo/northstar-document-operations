from collections import defaultdict, deque
from contextlib import asynccontextmanager
from hmac import compare_digest
from pathlib import Path
import re
from threading import Event, Thread
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .config import Settings
from .db import Database
from .extraction import create_provider, preview_pdf, PermanentExtractionError, TransientProviderError
from .inbox import ingest_message
from .models import ReviewAction, TextIngest
from .service import Conflict, DocumentService
from .worker import Worker


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_api_key()
    db = Database(settings.database_url, settings.database_schema)
    db.initialize()
    service = DocumentService(db, settings)
    worker = Worker(service, create_provider(settings))
    stop = Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        thread = Thread(target=worker.run, args=(stop,), name="document-worker", daemon=True)
        if settings.worker_enabled:
            thread.start()
        yield
        stop.set()
        if settings.worker_enabled:
            thread.join(timeout=3)

    app = FastAPI(title="Document Operations", version="1.0.0", lifespan=lifespan)
    app.state.service = service
    app.state.worker = worker
    limits: dict[str, deque] = defaultdict(deque)

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        peer = request.client.host if request.client else "local"
        now = time.monotonic()
        recent = limits[peer]
        while recent and recent[0] < now - 60:
            recent.popleft()
        if len(recent) > 180:
            return JSONResponse({"error": "Request rate exceeded"}, status_code=429, headers={"Retry-After": "60"})
        recent.append(now)
        if request.method in {"POST", "PUT", "PATCH"}:
            total, chunks = 0, []
            async for chunk in request.stream():
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    return JSONResponse({"error": "Upload exceeds 5 MB"}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        try:
            response = await call_next(request)
        except (Conflict, ValueError) as exc:
            response = JSONResponse({"error": str(exc)}, status_code=409 if isinstance(exc, Conflict) else 422)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' blob:; frame-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    def authorized(x_api_key: str = Header(default="")) -> None:
        if not compare_digest(x_api_key, settings.api_key):
            raise HTTPException(401, "Valid X-API-Key required")

    def client_id(value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", value):
            raise HTTPException(422, "Invalid client ID")
        return value

    @app.get("/health")
    def health():
        return {"status": "alive", "service": "document-operations"}

    @app.get("/ready")
    def ready():
        try:
            db.health()
        except Exception:
            return JSONResponse({"status": "unavailable", "reason": "database unavailable"}, status_code=503)
        if settings.parser_socket:
            import httpx

            try:
                with httpx.Client(transport=httpx.HTTPTransport(uds=settings.parser_socket), timeout=2) as client:
                    client.get("http://parser/health").raise_for_status()
            except httpx.HTTPError:
                return JSONResponse({"status": "unavailable", "reason": "isolated parser unavailable"}, status_code=503)
        return {
            "status": "ready",
            "database": "postgresql" if db.postgres else "sqlite",
            "llm_provider": settings.provider,
            "redis": service.signals.status(),
            "parser": "isolated Unix socket" if settings.parser_socket else "local native",
        }

    @app.get("/")
    def dashboard():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/app.css")
    def css():
        return FileResponse(Path(__file__).parent / "static" / "app.css", media_type="text/css")

    @app.get("/app.js")
    def javascript():
        return FileResponse(Path(__file__).parent / "static" / "app.js", media_type="text/javascript")

    @app.get("/api/snapshot", dependencies=[Depends(authorized)])
    def snapshot():
        return service.snapshot()

    @app.post("/api/documents/text", status_code=202, dependencies=[Depends(authorized)])
    def text_ingest(document: TextIngest, idempotency_key: str | None = Header(default=None)):
        return service.ingest(
            document.text.encode(),
            document.filename,
            "text/plain",
            document.client_id,
            document.source,
            idempotency_key,
        )

    @app.post("/api/documents/upload", status_code=202, dependencies=[Depends(authorized)])
    async def upload(
        request: Request, filename: str, client: str = "northstar", idempotency_key: str | None = Header(default=None)
    ):
        return service.ingest(
            await request.body(),
            filename,
            request.headers.get("content-type", "").split(";")[0],
            client_id(client),
            "upload",
            idempotency_key,
        )

    @app.post("/api/inbox", status_code=202, dependencies=[Depends(authorized)])
    async def inbox(request: Request, client: str = "northstar"):
        return {"documents": ingest_message(await request.body(), service, client_id(client))}

    @app.get("/api/documents/{job_id}", dependencies=[Depends(authorized)])
    def detail(job_id: str):
        try:
            return service.get(job_id)
        except KeyError:
            raise HTTPException(404, "Document not found") from None

    @app.get("/api/documents/{job_id}/source", dependencies=[Depends(authorized)])
    def source(job_id: str):
        with db.transaction(False) as tx:
            row = tx.execute("SELECT source_bytes,content_type FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Document not found") from None
            return Response(
                bytes(row["source_bytes"]), media_type=row["content_type"], headers={"Content-Disposition": "inline"}
            )

    @app.post("/api/documents/{job_id}/review", dependencies=[Depends(authorized)])
    def review(job_id: str, action: ReviewAction):
        try:
            return service.review(job_id, action)
        except KeyError:
            raise HTTPException(404, "Document not found") from None

    @app.get("/api/documents/{job_id}/preview", dependencies=[Depends(authorized)])
    def preview(job_id: str):
        with db.transaction(False) as tx:
            row = tx.execute("SELECT source_bytes,content_type FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Document not found")
        if row["content_type"] != "application/pdf":
            raise HTTPException(422, "Preview requires a PDF source")
        try:
            return Response(preview_pdf(bytes(row["source_bytes"]), settings), media_type="image/png")
        except PermanentExtractionError:
            raise HTTPException(422, "PDF cannot be previewed; source is unreadable") from None
        except TransientProviderError:
            raise HTTPException(503, "Preview parser unavailable; original source retained") from None

    @app.get("/metrics", dependencies=[Depends(authorized)])
    def metrics():
        state = service.snapshot()
        lines = [f'docops_jobs{{state="{name}"}} {count}' for name, count in state["states"].items()]
        lines += [
            f"docops_retry_events_total {state['retry_events']}",
            f"docops_registered_invoices {state['registered_invoices']}",
            f"docops_ack_delivered {state['delivered_count']}",
            f"docops_average_processing_seconds {state['average_processing_seconds']:.3f}",
            f"docops_redis_available {int(state['redis']['status'] == 'ready')}",
            f"docops_worker_heartbeats {state['redis'].get('active_workers', 0)}",
        ]
        return Response("\n".join(lines) + "\n", media_type="text/plain")

    return app
