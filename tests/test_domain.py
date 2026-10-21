from decimal import Decimal
import pytest
from pydantic import ValidationError

from docops.extraction import FixtureInvoiceProvider, PermanentExtractionError
from docops.fixtures import invoice_text
from docops.models import Invoice


def extracted():
    return FixtureInvoiceProvider().extract(invoice_text(), "test")


def test_decimal_arithmetic_and_valid_invoice():
    invoice = extracted()
    assert invoice.total == Decimal("220.00")
    assert invoice.business_errors() == []


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"total": "221.00"}, "Subtotal + tax"),
        ({"subtotal": "201.00", "total": "221.00"}, "Line item sum"),
        ({"due_date": "2026-09-01"}, "Due date"),
        ({"confidence": 0.89}, "confidence"),
    ],
)
def test_independent_business_rules(changes, message):
    data = extracted().model_dump(mode="json")
    data.update(changes)
    assert any(message in problem for problem in Invoice.model_validate(data).business_errors())


def test_line_item_multiplication_is_validated():
    data = extracted().model_dump(mode="json")
    data["line_items"][0]["quantity"] = "3"
    assert any("quantity" in error for error in Invoice.model_validate(data).business_errors())


@pytest.mark.parametrize(
    "field,value", [("total", "NaN"), ("tax", "Infinity"), ("total", "-1"), ("currency", "US"), ("confidence", 1.1)]
)
def test_invalid_schema_values_rejected(field, value):
    data = extracted().model_dump(mode="json")
    data[field] = value
    with pytest.raises(ValidationError):
        Invoice.model_validate(data)


def test_unknown_fields_rejected():
    data = extracted().model_dump(mode="json")
    data["execute_shell"] = "untrusted"
    with pytest.raises(ValidationError):
        Invoice.model_validate(data)


def test_malformed_provider_output_safe():
    with pytest.raises(PermanentExtractionError, match="malformed JSON"):
        FixtureInvoiceProvider().extract(invoice_text(extra="Fixture: malformed-ai"), "test")
