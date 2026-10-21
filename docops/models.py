from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=240)
    quantity: Decimal = Field(gt=0, le=1000000)
    unit_price: Decimal = Field(ge=0, le=1000000000)
    amount: Decimal = Field(ge=0, le=1000000000)

    @field_validator("quantity", "unit_price", "amount")
    @classmethod
    def finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Amount must be finite")
        return value


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor: str = Field(min_length=2, max_length=200)
    invoice_number: str = Field(min_length=1, max_length=80)
    invoice_date: date
    due_date: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    subtotal: Decimal = Field(ge=0, le=1000000000)
    tax: Decimal = Field(ge=0, le=1000000000)
    total: Decimal = Field(ge=0, le=1000000000)
    line_items: list[LineItem] = Field(min_length=1, max_length=100)
    purchase_order_reference: str | None = Field(max_length=80)
    confidence: float = Field(ge=0, le=1)

    @field_validator("subtotal", "tax", "total")
    @classmethod
    def finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Amount must be finite")
        return value

    def business_errors(self) -> list[str]:
        errors = []
        tolerance = Decimal("0.01")
        for i, item in enumerate(self.line_items, 1):
            if abs(item.quantity * item.unit_price - item.amount) > tolerance:
                errors.append(f"Line {i}: quantity × unit price differs from amount")
        if abs(sum((x.amount for x in self.line_items), Decimal(0)) - self.subtotal) > tolerance:
            errors.append("Line item sum differs from subtotal")
        if abs(self.subtotal + self.tax - self.total) > tolerance:
            errors.append("Subtotal + tax differs from total")
        if self.due_date < self.invoice_date:
            errors.append("Due date is before invoice date")
        if self.confidence < 0.9:
            errors.append("Extraction confidence below 90%; human review required")
        return errors


class TextIngest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(default="invoice.txt", min_length=1, max_length=160)
    text: str = Field(min_length=10, max_length=100000)
    client_id: str = Field(default="northstar", pattern=r"^[a-zA-Z0-9_-]{1,48}$")
    source: Literal["api", "upload", "email"] = "api"


class ReviewAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["save", "approve", "reject", "retry"]
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=500)
    invoice: Invoice | None = None

    @model_validator(mode="after")
    def save_requires_invoice(self) -> "ReviewAction":
        if self.action == "save" and self.invoice is None:
            raise ValueError("Saving requires edited invoice")
        return self
