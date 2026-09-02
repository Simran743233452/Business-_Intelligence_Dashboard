"""Reusable Google Sheets helper functions.

Keeps raw Sheets API calls (tab creation, reading, appending, upserting) in
one place so MCP tools stay thin and this logic can be reused/tested on its
own. Callers pass in an already-authenticated `service` object plus the
spreadsheet id - this module does not manage credentials itself, so it
doesn't touch the existing auth setup in mcp_server.py.
"""


def _quoted_range(tab_name: str, cell_range: str = "") -> str:
    """Build an A1-notation range, quoting the tab name.

    Sheets requires tab names with spaces (or other special characters) to
    be single-quoted in ranges, e.g. 'Daily Summary'!A1. A literal single
    quote in the tab name (as in "What's Needed Next") must be doubled.
    """
    escaped = tab_name.replace("'", "''")
    quoted_tab = f"'{escaped}'"
    return f"{quoted_tab}!{cell_range}" if cell_range else quoted_tab


def ensure_tab(service, spreadsheet_id: str, tab_name: str, headers: list) -> None:
    """Make sure `tab_name` exists with `headers` as its first row.

    No-op if the tab already exists, so this is safe to call on every
    write without disturbing existing rows.
    """
    metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing_titles = {s["properties"]["title"] for s in metadata.get("sheets", [])}

    if tab_name in existing_titles:
        return

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=_quoted_range(tab_name, "A1"),
        valueInputOption="USER_ENTERED",
        body={"values": [headers]},
    ).execute()


def get_tab_values(service, spreadsheet_id: str, tab_name: str) -> list:
    """Return all rows in a tab, including the header row (may be empty)."""
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=_quoted_range(tab_name)
    ).execute()
    return result.get("values", [])


def append_unique_rows(
    service, spreadsheet_id: str, tab_name: str, headers: list, rows: list, key_indexes: list
) -> int:
    """Append `rows`, skipping any that exactly duplicate an existing row.

    `key_indexes` lists which columns (by position) are compared to detect
    a duplicate - pass every column's index to require an exact full-row
    match (used here to avoid re-adding rows from a re-processed summary).
    Returns the number of rows actually written.
    """
    ensure_tab(service, spreadsheet_id, tab_name, headers)

    if not rows:
        return 0

    existing_rows = get_tab_values(service, spreadsheet_id, tab_name)[1:]
    existing_keys = {
        tuple(row[i] if i < len(row) else "" for i in key_indexes) for row in existing_rows
    }

    new_rows = [
        row
        for row in rows
        if tuple(str(row[i]) if i < len(row) else "" for i in key_indexes) not in existing_keys
    ]

    if not new_rows:
        return 0

    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=_quoted_range(tab_name, "A:Z"),
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": new_rows},
    ).execute()

    return len(new_rows)


def upsert_row_by_key(
    service, spreadsheet_id: str, tab_name: str, headers: list, row: list, key_index: int
) -> str:
    """Insert `row`, or overwrite the existing row sharing the same key column.

    Used for tabs where a column (e.g. Date) should only appear once -
    re-processing the same day's summary updates that row in place instead
    of appending a duplicate. Returns "inserted" or "updated".
    """
    ensure_tab(service, spreadsheet_id, tab_name, headers)

    values = get_tab_values(service, spreadsheet_id, tab_name)
    key_value = str(row[key_index])

    for row_index, existing_row in enumerate(values[1:], start=2):
        existing_key = existing_row[key_index] if key_index < len(existing_row) else ""
        if existing_key == key_value:
            end_col = chr(ord("A") + len(headers) - 1)
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=_quoted_range(tab_name, f"A{row_index}:{end_col}{row_index}"),
                valueInputOption="USER_ENTERED",
                body={"values": [row]},
            ).execute()
            return "updated"

    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=_quoted_range(tab_name, "A:Z"),
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()
    return "inserted"
