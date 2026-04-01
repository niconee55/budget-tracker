from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .config import load_merchant_knowledge
from .merchant_lookup import MerchantLookupService
from .models import Transaction
from .parser import ParsedEmail
from .text import cosine_similarity, normalize_text, vectorize


CATEGORIES = [
    "Monthly Income",
    "Utilities",
    "Groceries",
    "Transportation",
    "Eating Out",
    "Healthcare",
    "Clothes/Personal Care",
    "Housing Supplies",
    "Entertainment",
]

UNKNOWN_CATEGORY = "Unknown"
UNKNOWN_CONFIDENCE_THRESHOLD = 0.25
ENTERTAINMENT_SIGNAL_TOKENS = {
    "beer",
    "beers",
    "brew",
    "brews",
    "weed",
    "dispensary",
    "cannabis",
    "joint",
    "joints",
    "blunt",
    "blunts",
    "edible",
    "edibles",
    "preroll",
    "prerolls",
    "pre roll",
    "pre rolls",
    "gas pack",
    "tree",
    "trees",
}
ENTERTAINMENT_SIGNAL_EMOJIS = ("🍺", "🍻", "🍃", "🌿", "🔥", "💨", "😮‍💨")
GASOLINE_DISAMBIGUATION_TOKENS = {
    "gas station",
    "gasoline",
    "fuel",
    "shell",
    "exxon",
    "mobil",
    "chevron",
    "bp",
}


class TransactionCategorizer:
    def __init__(self, lookup_service: MerchantLookupService | None = None) -> None:
        self.knowledge = load_merchant_knowledge()
        self.lookup_service = lookup_service or MerchantLookupService()
        self.category_profiles = self._build_category_profiles()
        self.category_vectors = {
            category: vectorize(profile_text)
            for category, profile_text in self.category_profiles.items()
        }
        self.keyword_map = defaultdict(list)
        for category, entry in self.knowledge["categories"].items():
            for keyword in entry["keywords"]:
                self.keyword_map[normalize_text(keyword)].append(category)
        self.alias_map = {}
        self.merchant_vectors = {}
        for merchant, entry in self.knowledge["merchants"].items():
            merchant_vector = vectorize(" ".join([merchant, entry["category"], entry["description"], *entry["aliases"]]))
            self.merchant_vectors[merchant] = {
                "category": entry["category"],
                "vector": merchant_vector,
            }
            for alias in [merchant, *entry["aliases"]]:
                self.alias_map[normalize_text(alias)] = {
                    "merchant": merchant,
                    "category": entry["category"],
                    "vector": merchant_vector,
                }

    def categorize(self, parsed: ParsedEmail) -> Transaction:
        merchant_text = normalize_text(parsed.merchant)
        body_text = normalize_text(parsed.raw_snippet)
        if parsed.source_name in {"venmo_incoming", "venmo_received"} and self._looks_like_income(body_text):
            return Transaction(
                amount=parsed.amount.copy_abs(),
                date=parsed.date,
                merchant=parsed.merchant,
                category="Monthly Income",
                source_file=parsed.source_file,
                raw_snippet=parsed.raw_snippet,
                source_name=parsed.source_name,
                account_last4=parsed.account_last4,
                confidence=0.99,
            )
        entertainment_override = self._entertainment_signal_override(merchant_text, body_text, parsed.raw_snippet)
        if entertainment_override is not None:
            return Transaction(
                amount=self._signed_amount(parsed, entertainment_override),
                date=parsed.date,
                merchant=parsed.merchant,
                category=entertainment_override,
                source_file=parsed.source_file,
                raw_snippet=parsed.raw_snippet,
                source_name=parsed.source_name,
                account_last4=parsed.account_last4,
                confidence=0.97,
            )
        category, confidence = self._keyword_or_alias_match(merchant_text, body_text)
        predicted_category = None
        predicted_confidence = None
        predicted_rationale = None
        predicted_details_summary = None
        if category is None:
            category, confidence, predicted_category, predicted_confidence, predicted_rationale, predicted_details_summary = self._lookup_or_vector_match(parsed)
        if category is None or confidence < UNKNOWN_CONFIDENCE_THRESHOLD:
            category = UNKNOWN_CATEGORY
        return Transaction(
            amount=self._signed_amount(parsed, category),
            date=parsed.date,
            merchant=parsed.merchant,
            category=category,
            source_file=parsed.source_file,
            raw_snippet=parsed.raw_snippet,
            source_name=parsed.source_name,
            account_last4=parsed.account_last4,
            confidence=round(confidence, 4),
            predicted_category=predicted_category,
            predicted_confidence=predicted_confidence,
            predicted_rationale=predicted_rationale,
            predicted_details_summary=predicted_details_summary,
        )

    def summarize(self, transactions: list[Transaction]) -> dict[str, str]:
        totals = defaultdict(float)
        for transaction in transactions:
            totals[transaction.category] += float(transaction.amount)
        return {category: f"{totals.get(category, 0.0):.2f}" for category in CATEGORIES}

    def _keyword_or_alias_match(self, merchant_text: str, body_text: str) -> tuple[str | None, float]:
        for alias, entry in self.alias_map.items():
            if alias in merchant_text or alias in body_text:
                return entry["category"], 0.98
        for keyword, categories in self.keyword_map.items():
            if keyword in merchant_text or keyword in body_text:
                return categories[0], 0.9
        return None, 0.0

    def _vector_match(self, parsed: ParsedEmail) -> tuple[str, float]:
        return self._vector_match_text(" ".join([parsed.merchant, parsed.raw_snippet]))

    def _vector_match_text(self, text: str) -> tuple[str, float]:
        transaction_vector = vectorize(text)
        best_merchant_category = None
        best_merchant_score = 0.0
        for entry in self.merchant_vectors.values():
            score = cosine_similarity(transaction_vector, entry["vector"])
            if score > best_merchant_score:
                best_merchant_score = score
                best_merchant_category = entry["category"]

        best_category = "Entertainment"
        best_score = 0.0
        for category, category_vector in self.category_vectors.items():
            score = cosine_similarity(transaction_vector, category_vector)
            if score > best_score:
                best_category = category
                best_score = score

        if best_merchant_category is not None and best_merchant_score >= best_score:
            return best_merchant_category, best_merchant_score
        return best_category, best_score

    def _lookup_or_vector_match(self, parsed: ParsedEmail) -> tuple[str | None, float, str | None, float | None, str | None, str | None]:
        category, confidence, predicted_category, predicted_confidence, predicted_rationale, predicted_details_summary = self._lookup_match(parsed)
        if category is not None:
            return category, confidence, predicted_category, predicted_confidence, predicted_rationale, predicted_details_summary
        category, confidence = self._vector_match(parsed)
        return category, confidence, predicted_category, predicted_confidence, predicted_rationale, predicted_details_summary

    def _lookup_match(self, parsed: ParsedEmail) -> tuple[str | None, float, str | None, float | None, str | None, str | None]:
        lookup_result = self.lookup_service.lookup(parsed.merchant, parsed.raw_snippet)
        if lookup_result is None:
            return None, 0.0, None, None, None, None
        category = lookup_result.category
        confidence = lookup_result.confidence
        predicted_rationale = lookup_result.rationale
        predicted_details_summary = lookup_result.details_summary
        if category == UNKNOWN_CATEGORY or confidence < UNKNOWN_CONFIDENCE_THRESHOLD:
            return None, 0.0, category, confidence, predicted_rationale, predicted_details_summary
        return category, confidence, category, confidence, predicted_rationale, predicted_details_summary

    def _signed_amount(self, parsed: ParsedEmail, category: str) -> Decimal:
        amount = parsed.amount.copy_abs()
        if parsed.source_name in {"venmo_incoming", "venmo_received"} and category != "Monthly Income":
            return -amount
        return amount

    def _looks_like_income(self, body_text: str) -> bool:
        income_markers = (
            "nil club payout",
            "payout",
            "salary",
            "payroll",
            "direct deposit",
            "paycheck",
            "income",
        )
        return any(marker in body_text for marker in income_markers)

    def _entertainment_signal_override(
        self,
        merchant_text: str,
        body_text: str,
        raw_snippet: str,
    ) -> str | None:
        combined = " ".join(part for part in [merchant_text, body_text] if part)
        if any(token in combined for token in GASOLINE_DISAMBIGUATION_TOKENS):
            return None
        if any(token in combined for token in ENTERTAINMENT_SIGNAL_TOKENS):
            return "Entertainment"
        if " gas " in f" {combined} ":
            return "Entertainment"
        if any(emoji in raw_snippet for emoji in ENTERTAINMENT_SIGNAL_EMOJIS):
            return "Entertainment"
        return None

    def _build_category_profiles(self) -> dict[str, str]:
        merchant_names_by_category: dict[str, list[str]] = defaultdict(list)
        for merchant, entry in self.knowledge["merchants"].items():
            merchant_names_by_category[entry["category"]].append(merchant)
            merchant_names_by_category[entry["category"]].extend(entry["aliases"])

        profiles: dict[str, str] = {}
        for category, entry in self.knowledge["categories"].items():
            tokens = [category, *entry["keywords"], *entry["descriptors"], *merchant_names_by_category[category]]
            profiles[category] = " ".join(tokens)
        return profiles


def categorize_transaction(merchant: str, description: str = "") -> str:
    synthetic = ParsedEmail(
        source_name="generic",
        date="1970-01-01",
        merchant=merchant,
        amount=Decimal("0.00"),
        account_last4=None,
        raw_snippet=description,
        source_file="",
    )
    return TransactionCategorizer().categorize(synthetic).category
