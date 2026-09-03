"""Reusable Google Sheets helper functions.

Keeps raw Sheets API calls (tab creation, reading, appending, upserting) in
one place so MCP tools stay thin and this logic can be reused/tested on its
own. Callers pass in an already-authenticated `service` object plus the
spreadsheet id - this module does not manage credentials itself, so it
doesn't touch the existing auth setup in mcp_server.py.

All writes use valueInputOption="RAW", not "USER_ENTERED". USER_ENTERED
makes Sheets auto-parse date-looking strings (e.g. "2026-09-02") into real
date-typed cells - which then only *display* as that string if the cell
also happens to have date number-formatting applied (inherited from
existing rows/columns). A freshly-appended row without that inherited
formatting reads back as a raw date serial number (e.g. "72964") instead
of the string that was written, which silently breaks the exact-string
comparisons append_unique_rows/upsert_row_by_key rely on for dedup. RAW
stores every value as the literal text given, so what's written is always
exactly what's read back.
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
        valueInputOption="RAW",
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
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": new_rows},
    ).execute()

    return len(new_rows)


def upsert_row_by_key(
    service, spreadsheet_id: str, tab_name: str, headers: list, row: list, key_indexes: list
) -> str:
    """Insert `row`, or overwrite the existing row sharing the same values
    in `key_indexes`.

    `key_indexes` can be a single column (e.g. just Date) or a composite
    key (e.g. Date + Section) - composite matters once a tab holds more
    than one kind of row, so upserting one kind can never accidentally
    match a differently-shaped row that merely happens to share one
    column's value. Returns "inserted" or "updated".
    """
    ensure_tab(service, spreadsheet_id, tab_name, headers)

    values = get_tab_values(service, spreadsheet_id, tab_name)
    key_value = tuple(str(row[i]) for i in key_indexes)

    for row_index, existing_row in enumerate(values[1:], start=2):
        existing_key = tuple(
            existing_row[i] if i < len(existing_row) else "" for i in key_indexes
        )
        if existing_key == key_value:
            end_col = chr(ord("A") + len(headers) - 1)
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=_quoted_range(tab_name, f"A{row_index}:{end_col}{row_index}"),
                valueInputOption="RAW",
                body={"values": [row]},
            ).execute()
            return "updated"

    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=_quoted_range(tab_name, "A:Z"),
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()
    return "inserted"
