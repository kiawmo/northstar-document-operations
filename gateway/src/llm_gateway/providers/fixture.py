import copy

from ..errors import ErrorCode, GatewayError
from ..models import GenerateRequest

DEFAULT_FIXTURES = {
    "generic": {"ok": True},
    "revenue": {
        "score": 92,
        "tier": "HOT",
        "reason": "Synthetic lead requests an integration with an explicit budget and deadline.",
        "intent": "purchase_automation",
        "recommended_action": "Request human approval for a discovery meeting invitation.",
        "confidence": 0.96,
    },
    "support": {
        "intent": "FAQ",
        "rationale": "The supplied shipping policy answers the standard delivery question.",
        "citation_ids": ["shipping#standard"],
        "confidence": 0.94,
        "tool": "none",
        "order_id": "",
        "appointment_at": "",
    },
    "invoice": {
        "invoice_number": "NS-1042",
        "vendor": "Northstar Office Supply",
        "invoice_date": "2026-10-01",
        "due_date": "2026-10-31",
        "currency": "USD",
        "subtotal": "200.00",
        "tax": "20.00",
        "total": "220.00",
        "line_items": [
            {
                "description": "Office supplies",
                "quantity": "2",
                "unit_price": "100.00",
                "amount": "200.00",
            }
        ],
        "purchase_order_reference": "PO-2026-17",
        "confidence": 0.97,
    },
}
TASK_ALIASES = {
    "lead_qualification": "revenue",
    "support_response": "support",
    "invoice_extraction": "invoice",
}


class FixtureProvider:
    """Explicit test provider; never a silent fallback for a live provider."""

    name = "fixture"

    def __init__(self, outputs: dict[str, dict] | None = None) -> None:
        self.outputs = copy.deepcopy(DEFAULT_FIXTURES if outputs is None else outputs)

    async def generate(self, request: GenerateRequest) -> dict:
        if request.fixture == "outage":
            raise GatewayError(ErrorCode.UNAVAILABLE, "Simulated provider outage", retryable=True)
        if request.fixture == "invalid_schema":
            return {"unexpected_field": "deliberately invalid test output"}
        task = request.task if request.task in self.outputs else TASK_ALIASES.get(request.task)
        if task not in self.outputs:
            raise GatewayError(
                ErrorCode.CONFIGURATION, "No named fixture exists for this task", status=422
            )
        data = copy.deepcopy(self.outputs[task])
        if request.fixture == "low_confidence":
            data["confidence"] = 0.35
        elif request.fixture != "default":
            raise GatewayError(ErrorCode.CONFIGURATION, "Unknown fixture scenario", status=422)
        return data
