from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from budget_tracker.merchant_lookup import MerchantLookupResult, MerchantLookupService


class FailingLookupService(MerchantLookupService):
    def _fetch_lookup_result(self, merchant: str, raw_snippet: str):
        return None


class SuccessfulLookupService(MerchantLookupService):
    def _fetch_lookup_result(self, merchant: str, raw_snippet: str):
        return MerchantLookupResult(
            category="Eating Out",
            confidence=0.87,
            details_summary="restaurant",
            rationale="Merchant is a restaurant.",
            source="codex_web_search",
        )


class UnknownLookupService(MerchantLookupService):
    def _fetch_lookup_result(self, merchant: str, raw_snippet: str):
        return MerchantLookupResult(
            category="Unknown",
            confidence=0.4,
            details_summary="ambiguous merchant",
            rationale="The merchant cannot be classified reliably.",
            source="codex_web_search",
        )


class MerchantLookupServiceTests(unittest.TestCase):
    def test_uses_codex_cli_default_model_unless_env_overrides(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            service = FailingLookupService(enabled=True, cache_path=Path("/tmp/nonexistent-cache.json"))

        self.assertIsNone(service.model)

    def test_uses_env_model_override_when_present(self) -> None:
        with patch.dict(os.environ, {"BUDGET_TRACKER_MERCHANT_LOOKUP_MODEL": "custom-model"}, clear=True):
            service = FailingLookupService(enabled=True, cache_path=Path("/tmp/nonexistent-cache.json"))

        self.assertEqual(service.model, "custom-model")

    def test_ignores_unsupported_legacy_model_override(self) -> None:
        with patch.dict(os.environ, {"BUDGET_TRACKER_MERCHANT_LOOKUP_MODEL": "gpt-5.4"}, clear=True):
            service = FailingLookupService(enabled=True, cache_path=Path("/tmp/nonexistent-cache.json"))

        self.assertIsNone(service.model)

    def test_does_not_cache_failed_lookup_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "merchant_lookup_cache.json"
            service = FailingLookupService(enabled=True, cache_path=cache_path)

            result = service.lookup("Mystery Merchant", "purchase receipt")

            self.assertIsNone(result)
            self.assertEqual(service.cache, {})
            self.assertFalse(cache_path.exists())

    def test_ignores_empty_entries_from_existing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "merchant_lookup_cache.json"
            cache_path.write_text(json.dumps({"failed merchant": {}}))

            service = FailingLookupService(enabled=True, cache_path=cache_path)

            self.assertEqual(service.cache, {})

    def test_ignores_unknown_entries_from_existing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "merchant_lookup_cache.json"
            cache_path.write_text(json.dumps({
                "ambiguous merchant": {
                    "category": "Unknown",
                    "confidence": 0.4,
                    "details_summary": "ambiguous merchant",
                    "rationale": "The merchant cannot be classified reliably.",
                    "source": "codex_web_search",
                }
            }))

            service = FailingLookupService(enabled=True, cache_path=cache_path)

            self.assertEqual(service.cache, {})

    def test_does_not_cache_unknown_lookup_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "merchant_lookup_cache.json"
            service = UnknownLookupService(enabled=True, cache_path=cache_path)

            result = service.lookup("Ambiguous Merchant", "purchase receipt")

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.category, "Unknown")
            self.assertEqual(service.cache, {})
            self.assertFalse(cache_path.exists())

    def test_caches_successful_lookup_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "merchant_lookup_cache.json"
            service = SuccessfulLookupService(enabled=True, cache_path=cache_path)

            result = service.lookup("Pizza Shop", "purchase receipt")

            self.assertIsNotNone(result)
            self.assertTrue(cache_path.exists())
            payload = json.loads(cache_path.read_text())
            self.assertEqual(len(payload), 1)
            cached = next(iter(payload.values()))
            self.assertEqual(cached["category"], "Eating Out")


if __name__ == "__main__":
    unittest.main()
