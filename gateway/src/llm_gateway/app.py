import secrets
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import Settings
from .errors import ErrorCode, GatewayError
from .middleware import BodyLimitMiddleware
from .models import GenerateRequest, GenerateResponse
from .observability import audit
from .service import Gateway


def create_app(settings: Settings | None = None, gateway: Gateway | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    service = gateway or Gateway(config)
    app = FastAPI(title="Northstar LLM Gateway", version="1.0.0", docs_url="/docs")
    app.state.gateway = service
    app.add_middleware(BodyLimitMiddleware, max_bytes=config.max_input_bytes)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        trace_id = request.headers.get("x-trace-id", "")
        if (
            not trace_id
            or len(trace_id) > 80
            or not all(c.isalnum() or c in "_.:-" for c in trace_id)
        ):
            trace_id = str(uuid.uuid4())
        request.state.trace_id = trace_id
        if request.url.path not in {"/health", "/ready"} and config.api_key.get_secret_value():
            supplied = request.headers.get("authorization", "")
            expected = "Bearer " + config.api_key.get_secret_value()
            if not secrets.compare_digest(supplied, expected):
                return JSONResponse(
                    {
                        "error": {
                            "code": "unauthorized",
                            "message": "Gateway credentials required",
                            "retryable": False,
                            "trace_id": trace_id,
                        }
                    },
                    status_code=401,
                )
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse(
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "Invalid content length",
                        "retryable": False,
                        "trace_id": trace_id,
                    }
                },
                status_code=400,
            )
        if length > config.max_input_bytes:
            return JSONResponse(
                GatewayError(ErrorCode.INPUT_TOO_LARGE, "Request exceeds the input limit").as_dict(
                    trace_id
                ),
                status_code=413,
            )
        response = await call_next(request)
        response.headers["X-Trace-ID"] = request.state.trace_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(GatewayError)
    async def gateway_error(request: Request, error: GatewayError):
        return JSONResponse(error.as_dict(request.state.trace_id), status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Pydantic's default details can echo customer input. Return only safe metadata.
        return JSONResponse(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "Request fields failed validation",
                    "retryable": False,
                    "trace_id": request.state.trace_id,
                }
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, error: Exception):
        audit(
            "gateway.internal_error",
            trace_id=request.state.trace_id,
            error_type=type(error).__name__,
        )
        return JSONResponse(
            {
                "error": {
                    "code": "internal_error",
                    "message": "Gateway encountered an internal error",
                    "retryable": False,
                    "trace_id": request.state.trace_id,
                }
            },
            status_code=500,
        )

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "provider": service.provider.name,
            "active": service.active,
            "metrics": service.metrics,
        }

    @app.get("/ready")
    async def ready():
        if service.provider.name == "codex_cli":
            await service.provider.preflight()
        return {"status": "ready", "provider": service.provider.name}

    @app.post("/generate", response_model=GenerateResponse)
    async def generate(request: GenerateRequest, http_request: Request):
        http_request.state.trace_id = request.trace_id
        return await service.generate(request)

    return app
