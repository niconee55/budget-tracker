from __future__ import annotations


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


def read_rows_from_sheet(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
) -> list[list[str]]:
    response = (
        sheets_service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=sheet_name,
        )
        .execute()
    )
    return response.get("values", [])


def write_rows_to_sheet(
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
    write_rows_to_sheet(
        sheets_service=sheets_service,
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        rows=rows,
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
