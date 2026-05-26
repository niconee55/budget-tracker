from __future__ import annotations

from decimal import Decimal
import unittest

from budget_tracker.models import Transaction
from budget_tracker.sheets_sync import (
    build_monthly_budget_plan,
    is_monthly_budget_sheet,
    read_budget_sheet_rows,
    sync_monthly_budget_sheet,
)


MONTHLY_HEADERS = [
    "Month",
    "Monthly Income",
    "Monthly Pocket Change",
    "Monthly Expenses",
    "Rent",
    "Utilities",
    "Groceries",
    "Transportation",
    "Eating Out",
    "Gym",
    "Healthcare",
    "Clothes/Personal Care",
    "Housing Supplies",
    "Entertainment",
    "Subscriptions (Spotify)",
    "Travel and other spontaneous wants",
    "Investing",
    "20%",
    "of NYC income a month (minus 401k)",
    "401k",
    "<----",
    "Gifts",
    "Unknown",
]


def _column_number(column_letter: str) -> int:
    column_number = 0
    for char in column_letter:
        column_number = column_number * 26 + (ord(char.upper()) - 64)
    return column_number


def _monthly_sheet_rows() -> list[list[str]]:
    row_two = ["Expected "] + [""] * (len(MONTHLY_HEADERS) - 1)
    row_three = ["March 2026"] + [""] * (len(MONTHLY_HEADERS) - 1)
    row_three[_column_number("F") - 1] = "=10.00+5.00"
    row_three[_column_number("G") - 1] = "$3.00"
    return [MONTHLY_HEADERS, row_two, row_three]


def _template_month_row(row_number: int) -> list[str]:
    return [
        f'=IF(OR(B{row_number}<>"",F{row_number}<>"",G{row_number}<>"",H{row_number}<>"",I{row_number}<>"",K{row_number}<>"",L{row_number}<>"",M{row_number}<>"",N{row_number}<>""), TEXT(TODAY(),"mmmm yyyy"), "")',
        "",
        f'=IF(A{row_number}<>"", B{row_number}-D{row_number}, "")',
        f'=IF(A{row_number}<>"", SUM(E{row_number}:T{row_number}), "")',
        f'=IF(A{row_number}<>"", 2700, "")',
        "",
        "",
        "",
        "",
        f'=IF(A{row_number}<>"", J$2, "")',
        "",
        "",
        "",
        "",
        f'=IF(A{row_number}<>"", O$2, "")',
        f'=IF(A{row_number}<>"", P$2, "")',
        f'=IF(A{row_number}<>"", Q$2, "")',
        "",
        "",
        f'=IF(A{row_number}<>"", T$2, "")',
    ]


def _empty_visible_budget_row() -> list[str]:
    return [""] * len(MONTHLY_HEADERS)


def _placeholder_budget_row(row_number: int) -> list[str]:
    row = [""] * len(MONTHLY_HEADERS)
    for column_letter in ("C", "D", "E", "F", "G", "H", "I", "K", "L", "M", "N", "V", "W"):
        row[_column_number(column_letter) - 1] = f'=IF(A{row_number}<>"", 0, "")'
    return row


def _transaction(category: str, amount: str, merchant: str = "Merchant") -> Transaction:
    return Transaction(
        amount=Decimal(amount),
        date="2026-03-27",
        merchant=merchant,
        category=category,
        source_file="gmail:msg-1",
        raw_snippet=f"{merchant} purchase for {amount}",
        source_name="gmail",
        account_last4="1234",
        confidence=0.98,
    )


class MonthlySheetPlanTests(unittest.TestCase):
    def test_monthly_sheet_detection_accepts_baseline_row(self) -> None:
        rows = _monthly_sheet_rows()
        rows[1][0] = "Baseline"
        self.assertTrue(is_monthly_budget_sheet(rows))

    def test_build_monthly_budget_plan_targets_only_allowed_budget_columns(self) -> None:
        transactions = [
            _transaction("Monthly Income", "1200.00", "Payroll"),
            _transaction("Utilities", "2.25", "Con Edison"),
            _transaction("Groceries", "4.00", "Trader Joe's"),
            _transaction("Transportation", "6.00", "Uber"),
            _transaction("Eating Out", "7.00", "HUDSON MARKET PLACE"),
            _transaction("Healthcare", "8.00", "CVS Pharmacy"),
            _transaction("Clothes/Personal Care", "9.00", "Sephora"),
            _transaction("Housing Supplies", "10.00", "Home Depot"),
            _transaction("Entertainment", "11.00", "Netflix"),
            _transaction("Gifts", "12.34", "Birthday gift"),
            _transaction("Mystery Expense", "1.11", "Unknown Vendor"),
        ]

        plan = build_monthly_budget_plan(_monthly_sheet_rows(), transactions, "Sheet1")

        self.assertEqual(plan.row_updates, [])
        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertEqual(
            set(updates),
            {
                "Sheet1!B3",
                "Sheet1!F3",
                "Sheet1!G3",
                "Sheet1!H3",
                "Sheet1!I3",
                "Sheet1!K3",
                "Sheet1!L3",
                "Sheet1!M3",
                "Sheet1!N3",
                "Sheet1!V3",
                "Sheet1!W3",
            },
        )
        self.assertEqual(updates["Sheet1!B3"], "=1200.00")
        self.assertEqual(updates["Sheet1!F3"], "=10.00+5.00+2.25")
        self.assertEqual(updates["Sheet1!G3"], "=3.00+4.00")
        self.assertEqual(updates["Sheet1!V3"], "=12.34")
        self.assertEqual(updates["Sheet1!W3"], "=1.11")
        self.assertTrue(all(value.startswith("=") for value in updates.values()))

    def test_build_monthly_budget_plan_preserves_negative_reimbursement_amounts(self) -> None:
        transactions = [
            _transaction("Eating Out", "-22.00", "Beer reimbursement"),
        ]
        rows = _monthly_sheet_rows()
        rows[2][_column_number("I") - 1] = "=10.00+8.00"

        plan = build_monthly_budget_plan(rows, transactions, "Sheet1")

        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertEqual(updates["Sheet1!I3"], "=10.00+8.00+-22.00")

    def test_sync_monthly_budget_sheet_updates_only_target_cells(self) -> None:
        transactions = [
            _transaction("Monthly Income", "1200.00", "Payroll"),
            _transaction("Utilities", "2.25", "Con Edison"),
            _transaction("Gifts", "12.34", "Birthday gift"),
            _transaction("Mystery Expense", "1.11", "Unknown Vendor"),
        ]
        sheets_service = FakeSheetsService(_monthly_sheet_rows())

        plan = sync_monthly_budget_sheet(
            sheets_service=sheets_service,
            spreadsheet_id="spreadsheet-123",
            sheet_name="Sheet1",
            transactions=transactions,
            existing_rows=_monthly_sheet_rows(),
        )

        self.assertEqual(plan.row_updates, [])
        self.assertEqual(len(sheets_service.batch_update_payloads), 1)
        batch_payload = sheets_service.batch_update_payloads[0]
        self.assertEqual(
            {item["range"] for item in batch_payload["data"]},
            {"Sheet1!B3", "Sheet1!F3", "Sheet1!V3", "Sheet1!W3"},
        )
        self.assertEqual(
            {item["values"][0][0] for item in batch_payload["data"]},
            {"=1200.00", "=10.00+5.00+2.25", "=12.34", "=1.11"},
        )
        self.assertEqual(sheets_service.update_payloads, [])

    def test_sync_monthly_budget_sheet_uses_underlying_formula_not_display_value(self) -> None:
        formula_rows = _monthly_sheet_rows()
        formatted_rows = _monthly_sheet_rows()
        formula_rows[2][_column_number("I") - 1] = "=6.78+6.78+6.78+8"
        formatted_rows[2][_column_number("I") - 1] = "28.34"
        sheets_service = FakeSheetsService(formula_rows, formatted_rows)

        visible_rows = read_budget_sheet_rows(
            sheets_service=sheets_service,
            spreadsheet_id="spreadsheet-123",
            sheet_name="Sheet1",
        )
        plan = sync_monthly_budget_sheet(
            sheets_service=sheets_service,
            spreadsheet_id="spreadsheet-123",
            sheet_name="Sheet1",
            transactions=[_transaction("Eating Out", "10.00", "CHEF CHANG EXPRESS")],
            existing_rows=visible_rows,
        )

        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertEqual(updates["Sheet1!I3"], "=6.78+6.78+6.78+8+10.00")

    def test_build_monthly_budget_plan_matches_google_date_serial_month_rows(self) -> None:
        rows = _monthly_sheet_rows()
        rows[2][0] = 46082

        plan = build_monthly_budget_plan(rows, [_transaction("Transportation", "2.90", "MTA")], "Sheet1")

        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertNotIn("Sheet1!A4", updates)
        self.assertEqual(updates["Sheet1!H3"], "=2.90")

    def test_build_monthly_budget_plan_matches_date_text_month_rows(self) -> None:
        rows = _monthly_sheet_rows()
        rows[2][0] = "1/1/2026"

        january_transaction = Transaction(
            amount=Decimal("2.90"),
            date="2026-01-15",
            merchant="Trader Joe's",
            category="Groceries",
            source_file="gmail:msg-2",
            raw_snippet="Trader Joe's purchase for 2.90",
            source_name="gmail",
            account_last4="1234",
            confidence=0.98,
        )

        plan = build_monthly_budget_plan(rows, [january_transaction], "Sheet1")

        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertEqual(updates["Sheet1!G3"], "=3.00+2.90")

    def test_build_monthly_budget_plan_accepts_slash_formatted_transaction_dates(self) -> None:
        rows = [
            MONTHLY_HEADERS,
            ["Baseline"] + [""] * (len(MONTHLY_HEADERS) - 1),
            _placeholder_budget_row(3),
        ]
        rows[2][0] = "April 2026"

        april_transaction = Transaction(
            amount=Decimal("10.49"),
            date="04/19/2026",
            merchant="Whole Foods Market",
            category="Groceries",
            source_file="gmail:msg-2",
            raw_snippet="Whole Foods Market purchase for 10.49",
            source_name="gmail",
            account_last4="1234",
            confidence=0.98,
        )

        plan = build_monthly_budget_plan(rows, [april_transaction], "Sheet1")

        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertEqual(updates, {"Sheet1!G3": "=10.49"})

    def test_build_monthly_budget_plan_includes_transportation_transactions_in_sheet_totals(self) -> None:
        transactions = [
            _transaction("Transportation", "2.90", "MTA"),
            _transaction("Transportation", "2.90", "OMNY"),
            _transaction("Transportation", "19.00", "MetroCard"),
            _transaction("Transportation", "28.50", "Uber"),
        ]

        plan = build_monthly_budget_plan(_monthly_sheet_rows(), transactions, "Sheet1")

        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertEqual(updates["Sheet1!H3"], "=2.90+2.90+19.00+28.50")

    def test_build_monthly_budget_plan_does_not_exclude_subway_restaurant(self) -> None:
        transactions = [
            _transaction("Eating Out", "12.75", "Subway"),
        ]

        plan = build_monthly_budget_plan(_monthly_sheet_rows(), transactions, "Sheet1")

        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertEqual(updates["Sheet1!I3"], "=12.75")

    def test_build_monthly_budget_plan_uses_next_available_template_row_and_writes_column_a(self) -> None:
        rows = [
            MONTHLY_HEADERS,
            ["Expected "] + [""] * (len(MONTHLY_HEADERS) - 1),
            _placeholder_budget_row(3),
            _placeholder_budget_row(4),
            _empty_visible_budget_row() + ["", "", "Needs:", "=E2+F2+G2+K2+L2+M2+J2"],
        ]
        rows[2][0] = "March 2026"
        rows[2][_column_number("H") - 1] = "=2.00+3.00"

        april_transaction = Transaction(
            amount=Decimal("4.50"),
            date="2026-04-02",
            merchant="Trader Joe's",
            category="Groceries",
            source_file="gmail:msg-2",
            raw_snippet="Trader Joe's purchase for 4.50",
            source_name="gmail",
            account_last4="1234",
            confidence=0.98,
        )

        plan = build_monthly_budget_plan(rows, [april_transaction], "Sheet1")

        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertEqual(updates, {"Sheet1!A4": "April 2026", "Sheet1!G4": "=4.50"})

    def test_build_monthly_budget_plan_replaces_placeholder_formula_in_matching_month_cell(self) -> None:
        rows = [
            MONTHLY_HEADERS,
            ["Baseline"] + [""] * (len(MONTHLY_HEADERS) - 1),
            _placeholder_budget_row(3),
        ]
        rows[2][0] = "March 2026"

        plan = build_monthly_budget_plan(rows, [_transaction("Groceries", "4.50", "Trader Joe's")], "Sheet1")

        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertEqual(updates, {"Sheet1!G3": "=4.50"})

    def test_build_monthly_budget_plan_replaces_literal_zero_in_cleared_cell(self) -> None:
        rows = [
            MONTHLY_HEADERS,
            ["Baseline"] + [""] * (len(MONTHLY_HEADERS) - 1),
            _placeholder_budget_row(3),
        ]
        rows[2][0] = "March 2026"
        rows[2][_column_number("W") - 1] = "0"

        plan = build_monthly_budget_plan(rows, [_transaction("Mystery Expense", "1.11", "Unknown Vendor")], "Sheet1")

        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertEqual(updates, {"Sheet1!W3": "=1.11"})

    def test_build_monthly_budget_plan_writes_gifts_to_column_v(self) -> None:
        rows = [
            MONTHLY_HEADERS,
            ["Baseline"] + [""] * (len(MONTHLY_HEADERS) - 1),
            _placeholder_budget_row(3),
        ]
        rows[2][0] = "April 2026"
        gift_transaction = Transaction(
            amount=Decimal("273.00"),
            date="2026-04-13",
            merchant="Papa's gift",
            category="Gifts",
            source_file="gmail:msg-2",
            raw_snippet="Papa's gift purchase for 273.00",
            source_name="gmail",
            account_last4="1234",
            confidence=0.98,
        )

        plan = build_monthly_budget_plan(rows, [gift_transaction], "Sheet1")

        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertEqual(updates, {"Sheet1!V3": "=273.00"})

    def test_build_monthly_budget_plan_ignores_content_beyond_column_w_when_finding_open_row(self) -> None:
        rows = [
            MONTHLY_HEADERS,
            ["Expected "] + [""] * (len(MONTHLY_HEADERS) - 1),
            _empty_visible_budget_row(),
            _empty_visible_budget_row() + ["", "", "Needs:", "=123"],
        ]
        rows[2][0] = "March 2026"

        april_transaction = Transaction(
            amount=Decimal("4.50"),
            date="2026-04-02",
            merchant="Trader Joe's",
            category="Groceries",
            source_file="gmail:msg-2",
            raw_snippet="Trader Joe's purchase for 4.50",
            source_name="gmail",
            account_last4="1234",
            confidence=0.98,
        )

        plan = build_monthly_budget_plan(rows, [april_transaction], "Sheet1")

        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertEqual(updates, {"Sheet1!A4": "April 2026", "Sheet1!G4": "=4.50"})

    def test_build_monthly_budget_plan_matches_abbreviated_month_labels_in_column_a(self) -> None:
        rows = [
            MONTHLY_HEADERS,
            ["Expected "] + [""] * (len(MONTHLY_HEADERS) - 1),
            _empty_visible_budget_row(),
        ]
        rows[2][0] = "Mar 2026"
        rows[2][_column_number("G") - 1] = "=3.00"

        march_transaction = Transaction(
            amount=Decimal("4.50"),
            date="2026-03-20",
            merchant="Trader Joe's",
            category="Groceries",
            source_file="gmail:msg-2",
            raw_snippet="Trader Joe's purchase for 4.50",
            source_name="gmail",
            account_last4="1234",
            confidence=0.98,
        )

        plan = build_monthly_budget_plan(rows, [march_transaction], "Sheet1")

        updates = {update.range_name: update.values[0][0] for update in plan.cell_updates}
        self.assertEqual(updates, {"Sheet1!G3": "=3.00+4.50"})


class FakeSheetsService:
    def __init__(
        self,
        formula_rows: list[list[str]],
        formatted_rows: list[list[str]] | None = None,
    ) -> None:
        self.formula_rows = formula_rows
        self.formatted_rows = formatted_rows if formatted_rows is not None else formula_rows
        self.update_payloads: list[dict[str, object]] = []
        self.batch_update_payloads: list[dict[str, object]] = []

    def spreadsheets(self) -> "FakeSheetsService":
        return self

    def values(self) -> "FakeSheetsService":
        return self

    def get(self, **kwargs):
        value_render_option = kwargs.get("valueRenderOption", "FORMULA")
        rows = self.formula_rows if value_render_option == "FORMULA" else self.formatted_rows
        return FakeExecute({"values": rows})

    def update(self, **kwargs):
        self.update_payloads.append(kwargs)
        return FakeExecute({})

    def batchUpdate(self, **kwargs):  # noqa: N802 - matches Google API surface
        self.batch_update_payloads.append(kwargs["body"])
        return FakeExecute({})


class FakeExecute:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, object]:
        return self.payload
