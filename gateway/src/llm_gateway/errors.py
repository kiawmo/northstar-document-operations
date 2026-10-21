from enum import StrEnum


class ErrorCode(StrEnum):
    CONFIGURATION = "configuration_error"
    AUTHENTICATION = "authentication_error"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "provider_timeout"
    INVALID_OUTPUT = "invalid_output"
    INVALID_SCHEMA = "invalid_schema"
    POLICY = "policy_violation"
    OVERLOADED = "gateway_overloaded"
    INPUT_TOO_LARGE = "input_too_large"


class GatewayError(Exception):
    """Public errors contain predefined safe text, never provider bodies or prompts."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        status: int = 502,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after

    def as_dict(self, trace_id: str) -> dict:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "retryable": self.retryable,
                "trace_id": trace_id,
            }
        }
