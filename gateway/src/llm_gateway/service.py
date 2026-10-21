import asyncio
import secrets
import time

from .config import Settings
from .errors import ErrorCode, GatewayError
from .models import GenerateRequest, GenerateResponse
from .observability import audit
from .providers import LLMProvider
from .providers.codex import CodexCliProvider
from .providers.fixture import FixtureProvider
from .providers.rest import AnthropicProvider, GeminiProvider, OpenAIProvider
from .validation import inline_schema, validate_output, validate_schema


class Gateway:
    def __init__(self, settings: Settings | None = None, provider: LLMProvider | None = None):
        self.settings = settings or Settings.from_env()
        factories = {
            "codex_cli": lambda: CodexCliProvider(self.settings),
            "fixture": FixtureProvider,
            "openai": lambda: OpenAIProvider(self.settings),
            "anthropic": lambda: AnthropicProvider(self.settings),
            "gemini": lambda: GeminiProvider(self.settings),
        }
        self.provider = provider or factories[self.settings.provider]()
        self._semaphore = asyncio.Semaphore(self.settings.concurrency)
        self._inflight = 0
        self.active = 0
        self.metrics = {"requests": 0, "successes": 0, "failures": 0, "retries": 0, "rejected": 0}

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        started = time.perf_counter()
        self.metrics["requests"] += 1
        encoded = request.model_dump_json(by_alias=True).encode()
        if len(encoded) > self.settings.max_input_bytes:
            self.metrics["rejected"] += 1
            raise GatewayError(
                ErrorCode.INPUT_TOO_LARGE, "Request exceeds the input limit", status=413
            )
        validator = validate_schema(request.output_schema)
        provider_request = request.model_copy(
            update={"output_schema": inline_schema(request.output_schema)}
        )
        if self._inflight >= self.settings.concurrency * 4:
            self.metrics["rejected"] += 1
            raise GatewayError(
                ErrorCode.OVERLOADED, "Gateway request queue is full", retryable=True, status=429
            )
        self._inflight += 1
        acquired = False
        try:
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(), self.settings.queue_timeout_seconds
                )
                acquired = True
                self.active += 1
            except TimeoutError:
                self.metrics["rejected"] += 1
                raise GatewayError(
                    ErrorCode.OVERLOADED, "Gateway capacity is busy", retryable=True, status=429
                ) from None
            audit(
                "generation.started",
                trace_id=request.trace_id,
                provider=self.provider.name,
                task=request.task,
            )
            for attempt in range(1, self.settings.max_attempts + 1):
                try:
                    try:
                        value = await asyncio.wait_for(
                            self.provider.generate(provider_request), self.settings.timeout_seconds
                        )
                    except TimeoutError:
                        raise GatewayError(
                            ErrorCode.TIMEOUT,
                            "Provider execution deadline exceeded",
                            retryable=True,
                            status=504,
                        ) from None
                    data = validate_output(value, validator)
                    elapsed = round((time.perf_counter() - started) * 1000, 2)
                    self.metrics["successes"] += 1
                    audit(
                        "generation.completed",
                        trace_id=request.trace_id,
                        provider=self.provider.name,
                        latency_ms=elapsed,
                        attempts=attempt,
                    )
                    return GenerateResponse(
                        trace_id=request.trace_id,
                        provider=self.provider.name,
                        data=data,
                        latency_ms=elapsed,
                        attempts=attempt,
                    )
                except GatewayError as error:
                    audit(
                        "generation.attempt_failed",
                        trace_id=request.trace_id,
                        provider=self.provider.name,
                        code=error.code.value,
                        attempt=attempt,
                        retryable=error.retryable,
                    )
                    if not error.retryable or attempt == self.settings.max_attempts:
                        raise
                    self.metrics["retries"] += 1
                    jitter = secrets.randbelow(100) / 1000
                    await asyncio.sleep(
                        min(error.retry_after or 0.2 * 2 ** (attempt - 1), 10) + jitter
                    )
            raise RuntimeError("Unreachable generation retry state")
        except GatewayError as error:
            self.metrics["failures"] += 1
            audit(
                "generation.failed",
                trace_id=request.trace_id,
                provider=self.provider.name,
                code=error.code.value,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            raise
        finally:
            self._inflight -= 1
            if acquired:
                self.active -= 1
                self._semaphore.release()
