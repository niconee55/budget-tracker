from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import Mock

from budget_tracker.categorizer import TransactionCategorizer, categorize_transaction
from budget_tracker.merchant_lookup import MerchantLookupResult
from budget_tracker.parser import ParsedEmail


class CategorizerTests(unittest.TestCase):
    def test_categorizes_requested_categories(self) -> None:
        cases = [
            ("ACME Payroll", "March salary deposit", "Monthly Income"),
            ("Xfinity", "Internet bill", "Utilities"),
            ("Whole Foods Market", "weekly groceries", "Groceries"),
            ("Uber", "airport ride", "Transportation"),
            ("Chipotle", "lunch order", "Eating Out"),
            ("CHEF CHANG EXPRESS", "takeout order", "Eating Out"),
            ("CVS Pharmacy", "prescription refill", "Healthcare"),
            ("Sephora", "skincare and cosmetics", "Clothes/Personal Care"),
            ("Target", "toiletries and clothes", "Clothes/Personal Care"),
            ("Home Depot", "cleaning supplies and bulbs", "Housing Supplies"),
            ("Netflix", "monthly streaming subscription", "Entertainment"),
            ("Venmo", "birthday gift for dad", "Gifts"),
        ]
        for merchant, description, expected in cases:
            with self.subTest(merchant=merchant):
                self.assertEqual(categorize_transaction(merchant, description), expected)

    def test_uses_store_name_similarity_without_explicit_keyword(self) -> None:
        self.assertEqual(
            categorize_transaction("Trader Joe's", "purchase receipt"),
            "Groceries",
        )
        self.assertEqual(
            categorize_transaction("H Mart", "purchase receipt"),
            "Groceries",
        )
        self.assertEqual(
            categorize_transaction("HUDSON MARKET PLACE", "purchase receipt"),
            "Eating Out",
        )
        self.assertEqual(
            categorize_transaction("at Dunkin Donuts", "purchase receipt"),
            "Eating Out",
        )
        self.assertEqual(
            categorize_transaction("AMC Theatres", "ticket purchase"),
            "Entertainment",
        )
        self.assertEqual(
            categorize_transaction("at PARTIFUL CO", "purchase receipt"),
            "Entertainment",
        )
        self.assertEqual(
            categorize_transaction("Lexinton Public", "purchase receipt"),
            "Entertainment",
        )

    def test_routes_beer_weed_and_non_gasoline_gas_descriptions_to_entertainment(self) -> None:
        self.assertEqual(
            categorize_transaction("Venmo", "beer after work"),
            "Entertainment",
        )
        self.assertEqual(
            categorize_transaction("Venmo", "weed run 🍃"),
            "Entertainment",
        )
        self.assertEqual(
            categorize_transaction("Venmo", "gas for the blunt 🔥"),
            "Entertainment",
        )
        self.assertEqual(
            categorize_transaction("Shell", "gas station fill up"),
            "Transportation",
        )
        self.assertEqual(
            categorize_transaction("Ryan Winkler", "Ryan Winkler paid you $22.00 Beer Money credited to your Venmo account"),
            "Entertainment",
        )

    def test_uses_merchant_lookup_to_categorize_unknown_businesses(self) -> None:
        lookup_service = Mock()
        lookup_service.lookup.return_value = MerchantLookupResult(
            category="Eating Out",
            confidence=0.88,
            details_summary="salad chain",
            rationale="Sweetgreen is a fast casual salad restaurant chain.",
            source="codex_web_search",
        )
        transaction = TransactionCategorizer(lookup_service=lookup_service).categorize(
            ParsedEmail(
                source_name="generic",
                date="2026-03-30",
                merchant="Sweetgreen",
                amount=Decimal("18.42"),
                account_last4="1234",
                raw_snippet="purchase receipt",
                source_file="lookup.txt",
            )
        )

        self.assertEqual(transaction.category, "Eating Out")
        self.assertGreaterEqual(transaction.confidence, 0.72)
        lookup_service.lookup.assert_called_once_with("Sweetgreen", "purchase receipt")

    def test_uses_merchant_lookup_for_recent_unknown_merchants(self) -> None:
        cases = [
            ("at FAMOUS FAMIGLIA PIZZER", "Eating Out", "pizza restaurant"),
            ("at CVS", "Healthcare", "drugstore"),
            ("at LE FOURNIL", "Eating Out", "bakery"),
            ("at PARIS BAGUETTE -", "Eating Out", "bakery cafe"),
        ]
        for merchant, category, details in cases:
            with self.subTest(merchant=merchant):
                lookup_service = Mock()
                lookup_service.lookup.return_value = MerchantLookupResult(
                    category=category,
                    confidence=0.9,
                    details_summary=details,
                    rationale=f"{merchant} is a {details}.",
                    source="codex_web_search",
                )

                transaction = TransactionCategorizer(lookup_service=lookup_service).categorize(
                    ParsedEmail(
                        source_name="generic",
                        date="2026-09-22",
                        merchant=merchant,
                        amount=Decimal("12.34"),
                        account_last4="1234",
                        raw_snippet="purchase receipt",
                        source_file="lookup.txt",
                    )
                )

                self.assertEqual(transaction.category, category)
                lookup_service.lookup.assert_called_once_with(merchant, "purchase receipt")

    def test_categorizes_capital_one_deposit_as_monthly_income(self) -> None:
        transaction = TransactionCategorizer(lookup_service=Mock()).categorize(
            ParsedEmail(
                source_name="capital_one_deposit",
                date="2026-04-10",
                merchant="Direct Deposit",
                amount=Decimal("3396.70"),
                account_last4="0140",
                raw_snippet="You received a deposit of $3,396.70 into your account ending in 0140.",
                source_file="deposit.txt",
            )
        )

        self.assertEqual(transaction.category, "Monthly Income")
        self.assertEqual(transaction.amount, Decimal("3396.70"))
