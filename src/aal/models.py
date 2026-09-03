from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CanonicalRecord:
    record_id: str
    source: str
    amount: Decimal
    date: date
    vendor: str = ""
    reference: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CanonicalRecord":
        required = {"record_id", "source", "amount", "date"}
        missing = required - raw.keys()
        if missing:
            raise ValidationError(f"missing fields: {sorted(missing)}")
        if raw["source"] not in {"gl", "bank", "vendor", "support"}:
            raise ValidationError(f"invalid source: {raw['source']}")
        try:
            amount = Decimal(str(raw["amount"])).quantize(Decimal("0.01"))
            parsed_date = date.fromisoformat(str(raw["date"]))
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError(f"invalid amount/date: {exc}") from exc
        if not str(raw["record_id"]).strip():
            raise ValidationError("record_id is empty")
        return cls(str(raw["record_id"]), str(raw["source"]), amount, parsed_date,
                   str(raw.get("vendor", "")), str(raw.get("reference", "")),
                   dict(raw.get("metadata", {})))


@dataclass(frozen=True)
class ReconciliationResult:
    case_id: str
    status: str
    matched_ids: tuple[str, ...]
    reason: str
