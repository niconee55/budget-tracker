from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal


@dataclass(slots=True)
class Transaction:
    amount: Decimal
    date: str
    merchant: str
    category: str
    source_file: str
    raw_snippet: str
    source_name: str = "generic"
    account_last4: str | None = None
    confidence: float = 0.0
    predicted_category: str | None = None
    predicted_confidence: float | None = None
    predicted_rationale: str | None = None
    predicted_details_summary: str | None = None

    @property
    def source(self) -> str:
        if self.source_name.startswith("venmo"):
            return "venmo"
        if self.source_name.startswith("capital_one_zelle"):
            return "zelle"
        if self.source_name.startswith("discover"):
            return "discover"
        if self.source_name.startswith("capital_one"):
            return "capital_one"
        if self.source_name == "gmail":
            return "gmail"
        return self.source_name

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["amount"] = format(self.amount, "f")
        payload["source"] = self.source
        return payload
