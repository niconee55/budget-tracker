from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from tempfile import NamedTemporaryFile
from typing import Any

from .config import LOOKUP_CACHE_FILE, ROOT
from .text import normalize_text


DEFAULT_CODEX_BIN = "codex"
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_TIMEOUT_SECONDS = 90.0
DISABLED_VALUES = {"0", "false", "no", "off"}
ALLOWED_CATEGORIES = [
    "Monthly Income",
    "Utilities",
    "Groceries",
    "Transportation",
    "Eating Out",
    "Healthcare",
    "Clothes/Personal Care",
    "Housing Supplies",
    "Entertainment",
    "Gifts",
    "Unknown",
]
OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "category": {"type": "string", "enum": ALLOWED_CATEGORIES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "details_summary": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["category", "confidence", "details_summary", "rationale"],
}


@dataclass(slots=True)
class MerchantLookupResult:
    category: str
    confidence: float
    details_summary: str
    rationale: str
    source: str


class MerchantLookupService:
    def __init__(
        self,
        *,
        enabled: bool | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        model: str | None = None,
        codex_bin: str | None = None,
        cache_path=LOOKUP_CACHE_FILE,
    ) -> None:
        self.cache_path = cache_path
        self.timeout_seconds = timeout_seconds
        self.model = model or os.getenv("BUDGET_TRACKER_MERCHANT_LOOKUP_MODEL", DEFAULT_MODEL)
        self.codex_bin = codex_bin or os.getenv("BUDGET_TRACKER_CODEX_BIN", DEFAULT_CODEX_BIN)
        self.enabled = self._resolve_enabled(enabled)
        self.cache = self._load_cache()

    def lookup(self, merchant: str, raw_snippet: str = "") -> MerchantLookupResult | None:
        normalized = normalize_text(merchant)
        cache_key = self._cache_key(normalized, raw_snippet)
        if not self.enabled or not self._should_lookup(normalized):
            return None
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if not cached:
                return None
            return _result_from_cache_entry(cached)
        result = self._fetch_lookup_result(merchant, raw_snippet)
        self.cache[cache_key] = asdict(result) if result is not None else {}
        self._save_cache()
        return result

    def _resolve_enabled(self, enabled: bool | None) -> bool:
        if enabled is not None:
            return enabled
        raw = os.getenv(
            "BUDGET_TRACKER_ENABLE_MERCHANT_LOOKUP",
            os.getenv("BUDGET_TRACKER_ENABLE_MERCHANT_WEB_SEARCH", "1"),
        ).strip().lower()
        return raw not in DISABLED_VALUES

    def _should_lookup(self, normalized_merchant: str) -> bool:
        if not normalized_merchant or normalized_merchant == "unknown merchant":
            return False
        return normalized_merchant not in {"venmo", "cash app", "paypal", "zelle"}

    def _cache_key(self, normalized_merchant: str, raw_snippet: str) -> str:
        detail = normalize_text(raw_snippet)[:160]
        if not detail:
            return normalized_merchant
        return f"{normalized_merchant}||{detail}"

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        if not self.cache_path.exists():
            return {}
        try:
            payload = json.loads(self.cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            key: value
            for key, value in payload.items()
            if isinstance(key, str) and isinstance(value, dict)
        }

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, indent=2, sort_keys=True))

    def _fetch_lookup_result(self, merchant: str, raw_snippet: str) -> MerchantLookupResult | None:
        prompt = self._build_prompt(merchant, raw_snippet)
        try:
            with NamedTemporaryFile("w", suffix=".json", delete=True) as schema_file, NamedTemporaryFile(
                "r", suffix=".json", delete=True
            ) as output_file:
                json.dump(OUTPUT_SCHEMA, schema_file)
                schema_file.flush()
                command = [
                    self.codex_bin,
                    "--search",
                    "exec",
                    "--skip-git-repo-check",
                    "--ephemeral",
                    "--color",
                    "never",
                    "--output-schema",
                    schema_file.name,
                    "-o",
                    output_file.name,
                    "-m",
                    self.model,
                    "-C",
                    str(ROOT),
                    prompt,
                ]
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
                if completed.returncode != 0:
                    return None
                output_text = output_file.read().strip()
        except (OSError, subprocess.SubprocessError):
            return None
        return _parse_lookup_result(output_text)

    def _build_prompt(self, merchant: str, raw_snippet: str) -> str:
        categories = ", ".join(ALLOWED_CATEGORIES)
        return (
            "Classify a personal finance transaction into exactly one of these categories: "
            f"{categories}. Use web search if needed. "
            "Prefer the merchant name first, then use the transaction text for context. "
            "If the merchant is ambiguous or a general marketplace, use best judgment but lower confidence. "
            "Provide details_summary as a very short phrase of a few words, not a sentence. "
            "Keep rationale to one short sentence only. "
            "If it cannot be determined reliably, return Unknown. "
            f"Merchant: {merchant}\n"
            f"Transaction text: {raw_snippet[:500]}"
        )


def _parse_lookup_result(text: str) -> MerchantLookupResult | None:
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    category = str(parsed.get("category", "")).strip()
    if category not in ALLOWED_CATEGORIES:
        return None
    confidence = _clamp_confidence(parsed.get("confidence", 0.0))
    details_summary = " ".join(str(parsed.get("details_summary", "")).split())
    rationale = " ".join(str(parsed.get("rationale", "")).split())
    return MerchantLookupResult(
        category=category,
        confidence=confidence,
        details_summary=details_summary,
        rationale=rationale,
        source="codex_web_search",
    )


def _result_from_cache_entry(payload: dict[str, Any]) -> MerchantLookupResult | None:
    category = str(payload.get("category", "")).strip()
    if category not in ALLOWED_CATEGORIES:
        return None
    confidence = _clamp_confidence(payload.get("confidence", 0.0))
    details_summary = " ".join(str(payload.get("details_summary", "")).split())
    rationale = " ".join(str(payload.get("rationale", "")).split())
    source = " ".join(str(payload.get("source", "codex_web_search")).split()) or "codex_web_search"
    return MerchantLookupResult(
        category=category,
        confidence=confidence,
        details_summary=details_summary,
        rationale=rationale,
        source=source,
    )


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    return round(max(0.0, min(1.0, confidence)), 4)
