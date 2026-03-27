from __future__ import annotations

import unittest

from budget_tracker.categorizer import categorize_transaction


class CategorizerTests(unittest.TestCase):
    def test_categorizes_requested_categories(self) -> None:
        cases = [
            ("ACME Payroll", "March salary deposit", "Monthly Income"),
            ("Xfinity", "Internet bill", "Utilities"),
            ("Whole Foods Market", "weekly groceries", "Groceries"),
            ("Uber", "airport ride", "Transportation"),
            ("Chipotle", "lunch order", "Eating Out"),
            ("CVS Pharmacy", "prescription refill", "Healthcare"),
            ("Sephora", "skincare and cosmetics", "Clothes/Personal Care"),
            ("Home Depot", "cleaning supplies and bulbs", "Housing Supplies"),
            ("Netflix", "monthly streaming subscription", "Entertainment"),
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
            categorize_transaction("AMC Theatres", "ticket purchase"),
            "Entertainment",
        )

