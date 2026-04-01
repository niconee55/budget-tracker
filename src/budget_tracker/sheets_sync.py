from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

from .categorizer import UNKNOWN_CATEGORY
from .models import Transaction


TRANSACTION_HEADER = [
    "date",
    "merchant",
    "amount",
    "category",
    "source_name",
    "source_file",
    "account_last4",
    "confidence",
    "raw_snippet",
]

MONTH_LABEL_RE = re.compile(r"^[A-Z][a-z]+ \d{4}$")

MONTHLY_CATEGORY_COLUMN_MAP = {
    "Monthly Income": "B",
    "Utilities": "F",
    "Groceries": "G",
    "Transportation": "H",
    "Eating Out": "I",
    "Healthcare": "K",
    "Clothes/Personal Care": "L",
    "Housing Supplies": "M",
    "Entertainment": "N",
    UNKNOWN_CATEGORY: "V",
}

MONTHLY_CATEGORY_COLUMN_ORDER = [
    "Monthly Income",
    "Utilities",
    "Groceries",
    "Transportation",
    "Eating Out",
    "Healthcare",
    "Clothes/Personal Care",
    "Housing Supplies",
    "Entertainment",
    UNKNOWN_CATEGORY,
]

VISIBLE_BUDGET_COLUMN_END = "V"
SUBWAY_EXCLUSION_TOKENS = (
    "mta",
    "omny",
    "metrocard",
    "mta subway",
    "nyc subway",
    "nyc transit",
)


@dataclass(slots=True)
class CellUpdate:
    range_name: str
    values: list[list[str]]


@dataclass(slots=True)
class RowUpdate:
    range_name: str
    values: list[list[str]]


@dataclass(slots=True)
class MonthlyBudgetPlan:
    row_updates: list[RowUpdate]
    cell_updates: list[CellUpdate]


def read_rows_from_sheet(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
    value_render_option: str = "FORMULA",
) -> list[list[str]]:
    response = (
        sheets_service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=sheet_name,
            valueRenderOption=value_render_option,
        )
        .execute()
    )
    return response.get("values", [])


def is_monthly_budget_sheet(rows: list[list[str]]) -> bool:
    if len(rows) < 2:
        return False
    first_row = rows[0] if rows else []
    second_row = rows[1] if len(rows) > 1 else []
    if not first_row or not second_row:
        return False
    return first_row[0].strip().lower() == "month" and second_row[0].strip().lower().startswith("expected")


def sync_monthly_budget_sheet(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
    transactions: list[Transaction],
    existing_rows: list[list[str]] | None = None,
) -> MonthlyBudgetPlan:
    if existing_rows is None:
        existing_rows = read_rows_from_sheet(
            sheets_service=sheets_service,
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
        )
    formula_rows = read_rows_from_sheet(
        sheets_service=sheets_service,
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        value_render_option="FORMULA",
    )
    existing_rows = _overlay_budget_formula_cells(existing_rows, formula_rows)
    if not is_monthly_budget_sheet(existing_rows):
        raise ValueError(f"{sheet_name} is not a monthly budget sheet")

    plan = build_monthly_budget_plan(existing_rows, transactions, sheet_name)
    if not plan.cell_updates:
        return plan

    (
        sheets_service.spreadsheets()
        .values()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {"range": update.range_name, "values": update.values}
                    for update in plan.cell_updates
                ],
            },
        )
        .execute()
    )

    return plan


def build_monthly_budget_plan(
    existing_rows: list[list[str]],
    transactions: list[Transaction],
    sheet_name: str,
) -> MonthlyBudgetPlan:
    month_totals = _aggregate_month_category_amounts(transactions)
    if not month_totals:
        return MonthlyBudgetPlan(row_updates=[], cell_updates=[])

    month_row_map = _month_row_map(existing_rows)
    cell_updates: list[CellUpdate] = []

    for month_label in sorted(month_totals.keys(), key=_month_sort_key):
        row_number = month_row_map.get(month_label)
        if row_number is None:
            row_number = _next_available_month_row(existing_rows)
        if row_number is None:
            row_number = len(existing_rows) + 1
            month_row_map[month_label] = row_number

        category_amounts = month_totals[month_label]
        for category in MONTHLY_CATEGORY_COLUMN_ORDER:
            amounts = category_amounts.get(category)
            if not amounts:
                continue
            column_letter = MONTHLY_CATEGORY_COLUMN_MAP[category]
            current_value = _sheet_cell(existing_rows, row_number, _column_number(column_letter))
            formula = _build_additive_formula(current_value, amounts)
            if formula:
                cell_updates.append(
                    CellUpdate(
                        range_name=f"{sheet_name}!{column_letter}{row_number}",
                        values=[[formula]],
                    )
                )

    return MonthlyBudgetPlan(row_updates=[], cell_updates=cell_updates)


def write_transaction_rows_legacy(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
    rows: list[list[str]],
    clear_existing: bool = False,
) -> None:
    del clear_existing
    if not rows:
        return
    existing_rows = read_rows_from_sheet(
        sheets_service=sheets_service,
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
    )
    rows_to_append = _filter_rows_to_append(existing_rows, rows)
    if not rows_to_append:
        return
    (
        sheets_service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows_to_append},
        )
        .execute()
    )


def append_rows_to_sheet(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
    rows: list[list[str]],
) -> None:
    write_transaction_rows_legacy(
        sheets_service=sheets_service,
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        rows=rows,
    )


def write_rows_to_sheet(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
    rows: list[list[str]],
    clear_existing: bool = False,
) -> None:
    write_transaction_rows_legacy(
        sheets_service=sheets_service,
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        rows=rows,
        clear_existing=clear_existing,
    )


def merge_transaction_rows(
    existing_rows: list[list[str]],
    candidate_rows: list[list[str]],
) -> list[list[str]]:
    return _filter_rows_to_append(existing_rows, candidate_rows)


def ensure_sheet_header(
    existing_rows: list[list[str]],
    expected_header: list[str],
) -> list[list[str]]:
    if existing_rows:
        return []
    return [expected_header]


def _aggregate_month_category_amounts(transactions: list[Transaction]) -> dict[str, dict[str, list[Decimal]]]:
    month_totals: dict[str, dict[str, list[Decimal]]] = defaultdict(lambda: defaultdict(list))
    for transaction in transactions:
        if _should_exclude_from_budget_sheet(transaction):
            continue
        month_label = _month_label(transaction.date)
        category = _sheet_category_name(transaction.category)
        month_totals[month_label][category].append(transaction.amount)
    return month_totals


def _should_exclude_from_budget_sheet(transaction: Transaction) -> bool:
    if transaction.category != "Transportation":
        return False
    searchable = " ".join(
        part.strip().lower()
        for part in [transaction.merchant, transaction.raw_snippet]
        if part and part.strip()
    )
    return any(token in searchable for token in SUBWAY_EXCLUSION_TOKENS)


def _month_row_map(rows: list[list[str]]) -> dict[str, int]:
    month_rows: dict[str, int] = {}
    for row_index in range(3, len(rows) + 1):
        if _row_is_available_for_month(_sheet_row(rows, row_index)):
            continue
        month_label = _month_label_from_cell(_sheet_cell(rows, row_index, 1))
        if month_label:
            month_rows[month_label] = row_index
    return month_rows


def _sheet_category_name(category: str) -> str:
    if category in MONTHLY_CATEGORY_COLUMN_MAP:
        return category
    return UNKNOWN_CATEGORY


def _build_additive_formula(existing_value: str, amounts: list[Decimal]) -> str:
    terms: list[str] = []
    existing_term = _formula_term_from_cell(existing_value)
    if existing_term:
        terms.append(existing_term)
    terms.extend(_formula_term_from_decimal(amount) for amount in amounts)
    if not terms:
        return ""
    return "=" + "+".join(terms)


def _overlay_budget_formula_cells(
    existing_rows: list[list[str]],
    formula_rows: list[list[str]],
) -> list[list[str]]:
    merged_rows = [list(row) for row in existing_rows]
    total_rows = max(len(merged_rows), len(formula_rows))
    budget_columns = {_column_number(letter) for letter in MONTHLY_CATEGORY_COLUMN_MAP.values()}
    for row_index in range(total_rows):
        existing_row = merged_rows[row_index] if row_index < len(merged_rows) else []
        formula_row = formula_rows[row_index] if row_index < len(formula_rows) else []
        merge_width = max(len(existing_row), len(formula_row))
        if row_index >= len(merged_rows):
            existing_row = []
            merged_rows.append(existing_row)
        if len(existing_row) < merge_width:
            existing_row.extend([""] * (merge_width - len(existing_row)))
        for column_number in budget_columns:
            if column_number - 1 >= len(formula_row):
                continue
            formula_value = formula_row[column_number - 1]
            existing_row[column_number - 1] = "" if formula_value is None else str(formula_value)
    return merged_rows


def _formula_term_from_cell(value: str) -> str | None:
    cleaned = str(value).strip() if value is not None else ""
    if not cleaned:
        return None
    if cleaned.startswith("="):
        return cleaned[1:].strip()
    numeric = _try_parse_sheet_amount(cleaned)
    if numeric is None:
        return None
    return format(numeric, ".2f")


def _formula_term_from_decimal(value: Decimal) -> str:
    return format(value, ".2f")


def read_budget_sheet_rows(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
) -> list[list[str]]:
    formula_rows = read_rows_from_sheet(
        sheets_service=sheets_service,
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        value_render_option="FORMULA",
    )
    formatted_rows = read_rows_from_sheet(
        sheets_service=sheets_service,
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        value_render_option="FORMATTED_VALUE",
    )
    merged_rows: list[list[str]] = []
    total_rows = max(len(formula_rows), len(formatted_rows))
    for index in range(total_rows):
        formula_row = list(formula_rows[index]) if index < len(formula_rows) else []
        formatted_row = formatted_rows[index] if index < len(formatted_rows) else []
        merge_width = max(len(formula_row), len(formatted_row), _column_number(VISIBLE_BUDGET_COLUMN_END))
        if len(formula_row) < merge_width:
            formula_row.extend([""] * (merge_width - len(formula_row)))
        for column_number in range(1, _column_number(VISIBLE_BUDGET_COLUMN_END) + 1):
            if _should_use_formatted_value_for_column(column_number):
                formatted_value = formatted_row[column_number - 1] if column_number - 1 < len(formatted_row) else ""
                formula_row[column_number - 1] = "" if formatted_value is None else str(formatted_value)
        merged_rows.append(formula_row)
    return merged_rows


def _sheet_cell(rows: list[list[str]], row_number: int, column_number: int) -> str:
    if row_number <= 0 or column_number <= 0:
        return ""
    row = _sheet_row(rows, row_number)
    if column_number - 1 >= len(row):
        return ""
    value = row[column_number - 1]
    return "" if value is None else str(value)


def _sheet_row(rows: list[list[str]], row_number: int) -> list[str]:
    if row_number <= 0 or row_number - 1 >= len(rows):
        return []
    return rows[row_number - 1]


def _month_label(date_text: str) -> str:
    return datetime.fromisoformat(date_text).strftime("%B %Y")


def _month_label_from_cell(value: str) -> str | None:
    cleaned = str(value).strip() if value is not None else ""
    if cleaned and MONTH_LABEL_RE.match(cleaned):
        return cleaned
    serial_month_label = _month_label_from_serial(cleaned)
    if serial_month_label:
        return serial_month_label
    return None


def _month_label_from_serial(value: str) -> str | None:
    if not value:
        return None
    try:
        serial = float(value)
    except ValueError:
        return None
    if serial <= 0:
        return None
    date_value = datetime(1899, 12, 30) + timedelta(days=serial)
    return date_value.strftime("%B %Y")


def _month_sort_key(month_label: str) -> tuple[int, int]:
    dt = datetime.strptime(month_label, "%B %Y")
    return dt.year, dt.month


def _column_number(column_letter: str) -> int:
    column_number = 0
    for char in column_letter:
        column_number = column_number * 26 + (ord(char.upper()) - 64)
    return column_number


def _try_parse_sheet_amount(value: str) -> Decimal | None:
    if not value:
        return None
    cleaned = str(value).strip()
    if cleaned.startswith("="):
        cleaned = cleaned[1:]
        parts = [part.strip() for part in cleaned.split("+")]
        total = Decimal("0")
        for part in parts:
            if not part:
                continue
            try:
                total += Decimal(part.replace("$", "").replace(",", ""))
            except InvalidOperation:
                return None
        return total
    try:
        return Decimal(cleaned.replace("$", "").replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _parse_sheet_amount(value: str) -> Decimal:
    amount = _try_parse_sheet_amount(value)
    return amount if amount is not None else Decimal("0")


def format_currency(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):.2f}"


def _next_available_month_row(rows: list[list[str]]) -> int | None:
    for row_index in range(3, len(rows) + 1):
        if _row_is_available_for_month(_sheet_row(rows, row_index)):
            return row_index
    return None


def _row_is_available_for_month(row: list[str]) -> bool:
    if not row:
        return True
    for column_number in range(1, _column_number(VISIBLE_BUDGET_COLUMN_END) + 1):
        if _sheet_value_present(_row_cell_by_number(row, column_number)):
            return False
    return True


def _row_cell(row: list[str], column_letter: str) -> str:
    index = _column_number(column_letter) - 1
    if index >= len(row):
        return ""
    value = row[index]
    return "" if value is None else str(value)


def _row_cell_by_number(row: list[str], column_number: int) -> str:
    index = column_number - 1
    if index >= len(row):
        return ""
    value = row[index]
    return "" if value is None else str(value)


def _sheet_value_present(value: str) -> bool:
    return bool(str(value).strip()) if value is not None else False


def _should_use_formatted_value_for_column(column_number: int) -> bool:
    column_letter = _column_letter(column_number)
    return column_letter not in MONTHLY_CATEGORY_COLUMN_MAP


def _column_letter(column_number: int) -> str:
    letters = ""
    value = column_number
    while value:
        value, rem = divmod(value - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _filter_rows_to_append(
    existing_rows: list[list[str]],
    candidate_rows: list[list[str]],
) -> list[list[str]]:
    if not candidate_rows:
        return []

    normalized_existing = _normalize_existing_rows(existing_rows)
    existing_signatures = {_row_signature(row) for row in normalized_existing[1:] if row}

    if existing_rows and candidate_rows and candidate_rows[0] == existing_rows[0]:
        candidate_data_rows = candidate_rows[1:]
    else:
        candidate_data_rows = candidate_rows

    rows_to_append: list[list[str]] = []
    for row in candidate_data_rows:
        if not row:
            continue
        signature = _row_signature(row)
        if signature in existing_signatures:
            continue
        existing_signatures.add(signature)
        rows_to_append.append(row)

    if not existing_rows:
        return candidate_rows
    return rows_to_append


def _normalize_existing_rows(existing_rows: list[list[str]]) -> list[list[str]]:
    if not existing_rows:
        return [TRANSACTION_HEADER]
    if existing_rows[0] == TRANSACTION_HEADER:
        return existing_rows
    return [TRANSACTION_HEADER, *existing_rows]


def _row_signature(row: list[str]) -> tuple[str, ...]:
    padded = [cell.strip() for cell in row[:9]]
    if len(padded) < 9:
        padded.extend([""] * (9 - len(padded)))
    return tuple(padded)
