"""Serialize amounts stored natively in Indian rupees for API responses."""
from decimal import Decimal
from typing import Any


def api_native_inr_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep persisted INR values unchanged; convert Decimal only for JSON numbers."""
    return [{**row, "price": float(row["price"]), "currency": "INR"} for row in rows]


def native_inr_amount(amount: int | float | Decimal) -> float:
    """Return a JSON-safe number without applying an exchange-rate conversion."""
    return float(amount)
