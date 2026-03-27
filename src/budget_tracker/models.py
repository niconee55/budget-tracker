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

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["amount"] = format(self.amount, "f")
        return payload
