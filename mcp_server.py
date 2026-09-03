import os

from mcp.server.mcpserver import MCPServer
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from starlette.requests import Request
from starlette.responses import JSONResponse

from summary_parser import parse_monitoring_summary, build_monitoring_rows, MONITORING_HEADERS
from accounting_parser import parse_accounting_summary, build_accounting_rows, ACCOUNTING_HEADERS
from sheets_service import append_unique_rows, upsert_row_by_key
from auth_middleware import BearerTokenMiddleware

# -----------------------------
# MCP Server
# -----------------------------
mcp = MCPServer("AI Business Dashboard")


# -----------------------------
# Google Sheets configuration
# -----------------------------
SPREADSHEET_ID = "1bIifUY2LUi5C6is7ZJNzr-F_ov89_ViAdN-RhqoaBFQ"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets"
]

# -----------------------------
# Tab used by process_monitoring_summary
# -----------------------------
# All monitoring sections (Daily Summary, Needs Attention, Actions Taken,
# What's Needed Next, Service Pattern Watch) write into this ONE tab as
# plain business-facing rows - no Section column (see
# summary_parser.MONITORING_HEADERS for the exact column order and
# build_monitoring_rows() for how a parsed summary becomes rows).
# Previously each section had its own tab; those tabs (Daily Summary,
# Needs Attention, etc.) still exist in the spreadsheet but are no longer
# written to.
MONITORING_TAB = "Monitoring"

# Composite key for the Daily Summary row's upsert: (Date, Site) rather
# than Date alone, since this tab also holds other rows for the same
# date. The Daily Summary row is always written with Site = "ALL SITES"
# (see build_monitoring_rows), which no real site name would collide
# with, so this reliably matches only that row - without Site in the
# key, upserting today's Daily Summary row could otherwise match and
# overwrite an unrelated row that happens to share the same date.
_DATE_COL = MONITORING_HEADERS.index("Date")
_SITE_COL = MONITORING_HEADERS.index("Site")

# -----------------------------
# Tab used by process_accounting_summary
# -----------------------------
# Renamed from "Finance" (which held only an unused placeholder header,
# no real data - see accounting_parser.py for the migration performed).
# All accounting record types (Exception, Cash, Sale, Purchase, Expense,
# Tax, Pending) write into this ONE tab as plain rows distinguished by
# their Record Type column - see accounting_parser.ACCOUNTING_HEADERS for
# the exact column order and build_accounting_rows() for how a parsed
# summary becomes rows.
ACCOUNTING_TAB = "Accounting"

# Composite key for the Cash and Purchase rows' upsert: (Date, Record
# Type). Each report has at most one Cash row and one Purchase row, so
# reprocessing the same date's summary should update those rows in place
# rather than accumulate duplicates - the same "one row per date"
# pattern as Monitoring's Daily Summary row.
_ACCT_DATE_COL = ACCOUNTING_HEADERS.index("Date")
_ACCT_RECORD_TYPE_COL = ACCOUNTING_HEADERS.index("Record Type")


# -----------------------------
# Connect to Google Sheets
# -----------------------------
# Path to the service account key file. Defaults to "credentials.json" in
# the project directory, exactly as before, so local/stdio use is
# unchanged. On a host like Render there is no committed credentials.json
# (it's gitignored on purpose) - set GOOGLE_APPLICATION_CREDENTIALS to the
# path of a securely-provided key file instead (see README: "Providing
# Google credentials on Render"). This is the standard Google Cloud env
# var name for this exact purpose.
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")

credentials = Credentials.from_service_account_file(
    GOOGLE_CREDENTIALS_PATH,
    scopes=SCOPES
)

service = build(
    "sheets",
    "v4",
    credentials=credentials
)


# -----------------------------
# Tool 1: Test connection
# -----------------------------
@mcp.tool()
def test_connection(message: str) -> str:
    """Test whether the MCP server is working."""
    return f"Connection successful: {message}"


# -----------------------------
# Health check (HTTP transport only)
# -----------------------------
# Plain HTTP endpoint outside the MCP protocol itself, for hosting-platform
# uptime/liveness checks once this runs as a remote server. Has no effect
# on stdio/MCP Inspector usage - custom_route only applies when the server
# is actually running over HTTP.
@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Basic liveness endpoint for hosting platforms / uptime checks."""
    return JSONResponse({"status": "ok", "service": "ai-business-dashboard-mcp"})


# ---------------------------
# Tool 2: Process a full Plant Monitoring Summary
# ---------------------------

@mcp.tool()
def process_monitoring_summary(summary: str) -> str:
    """Parse a complete SUNTROP SOLAR Plant Monitoring Summary and write
    every section - daily totals, needs attention, actions taken, what's
    needed next, and service pattern watch - into the unified Monitoring
    sheet as plain business-facing rows (one tab, one 14-column schema,
    no Section column - the daily totals become a single row with
    Site = "ALL SITES"). This is the main tool for the Admin -> Claude ->
    Sheets workflow: paste the whole raw summary text as `summary`.

    Parsing is deterministic (no LLM call here) and tied to the fixed
    template. Re-running the same summary will not create duplicate rows.
    """

    # Deterministic parsing of the raw text into a structured dict, then
    # reshaped into per-section row lists sharing MONITORING_HEADERS'
    # column order - see summary_parser.py for both steps.
    parsed = parse_monitoring_summary(summary)
    rows = build_monitoring_rows(parsed)
    report_date = parsed["date"]

    # --- Daily Summary: exactly one row per (Date, "ALL SITES"); re-runs update it in place ---
    upsert_row_by_key(
        service, SPREADSHEET_ID, MONITORING_TAB, MONITORING_HEADERS,
        rows["daily_summary"][0], key_indexes=[_DATE_COL, _SITE_COL],
    )

    # --- Needs Attention: one row per overdue/escalated item, if any ---
    needs_attention_count = append_unique_rows(
        service, SPREADSHEET_ID, MONITORING_TAB, MONITORING_HEADERS,
        rows["needs_attention"], key_indexes=list(range(len(MONITORING_HEADERS))),
    )

    # --- Actions Taken ---
    actions_taken_count = append_unique_rows(
        service, SPREADSHEET_ID, MONITORING_TAB, MONITORING_HEADERS,
        rows["actions_taken"], key_indexes=list(range(len(MONITORING_HEADERS))),
    )

    # --- What's Needed Next ---
    needed_next_count = append_unique_rows(
        service, SPREADSHEET_ID, MONITORING_TAB, MONITORING_HEADERS,
        rows["whats_needed_next"], key_indexes=list(range(len(MONITORING_HEADERS))),
    )

    # --- Service Pattern Watch: only written when the section had real content ---
    pattern_count = append_unique_rows(
        service, SPREADSHEET_ID, MONITORING_TAB, MONITORING_HEADERS,
        rows["service_pattern_watch"], key_indexes=list(range(len(MONITORING_HEADERS))),
    )

    return (
        "Monitoring summary processed successfully.\n"
        f"Date: {report_date}\n"
        "Sections processed: Daily Summary, Needs Attention, Actions Taken, "
        "What's Needed Next, Service Pattern Watch\n"
        "Daily Summary: updated\n"
        f"Needs Attention: {needs_attention_count} row(s)\n"
        f"Actions Taken: {actions_taken_count} row(s)\n"
        f"What's Needed Next: {needed_next_count} row(s)\n"
        f"Service Pattern Watch: {pattern_count} row(s)"
    )


# ---------------------------
# Tool 3: Process a full Day Book (Accounting) Summary
# ---------------------------

@mcp.tool()
def process_accounting_summary(summary: str) -> str:
    """Parse a complete SUNTROP SOLAR Day Book Summary and write every
    section - exceptions requiring attention, cash & bank position,
    sales, purchase, expenses/journal entries, GST/tax watch items, and
    pending-from-yesterday items - into the unified Accounting sheet as
    plain rows (one tab, one 20-column schema, distinguished by a Record
    Type column: Exception, Cash, Sale, Purchase, Expense, Tax, Pending).
    Paste the whole raw Day Book summary text as `summary`.

    Parsing is deterministic (no LLM call here) and tied to the fixed
    template. Re-running the same summary will not create duplicate rows.
    """

    parsed = parse_accounting_summary(summary)
    rows = build_accounting_rows(parsed)
    report_date = parsed["date"]

    # --- Cash & Purchase: exactly one row per (Date, Record Type); re-runs update in place ---
    if rows["cash"]:
        upsert_row_by_key(
            service, SPREADSHEET_ID, ACCOUNTING_TAB, ACCOUNTING_HEADERS,
            rows["cash"][0], key_indexes=[_ACCT_DATE_COL, _ACCT_RECORD_TYPE_COL],
        )
    if rows["purchase"]:
        upsert_row_by_key(
            service, SPREADSHEET_ID, ACCOUNTING_TAB, ACCOUNTING_HEADERS,
            rows["purchase"][0], key_indexes=[_ACCT_DATE_COL, _ACCT_RECORD_TYPE_COL],
        )

    # --- Exceptions, Sales, Expenses, Tax watch, Pending: append-if-not-duplicate ---
    issues_count = append_unique_rows(
        service, SPREADSHEET_ID, ACCOUNTING_TAB, ACCOUNTING_HEADERS,
        rows["issues"], key_indexes=list(range(len(ACCOUNTING_HEADERS))),
    )
    sales_count = append_unique_rows(
        service, SPREADSHEET_ID, ACCOUNTING_TAB, ACCOUNTING_HEADERS,
        rows["sales"], key_indexes=list(range(len(ACCOUNTING_HEADERS))),
    )
    expenses_count = append_unique_rows(
        service, SPREADSHEET_ID, ACCOUNTING_TAB, ACCOUNTING_HEADERS,
        rows["expenses"], key_indexes=list(range(len(ACCOUNTING_HEADERS))),
    )
    tax_count = append_unique_rows(
        service, SPREADSHEET_ID, ACCOUNTING_TAB, ACCOUNTING_HEADERS,
        rows["tax"], key_indexes=list(range(len(ACCOUNTING_HEADERS))),
    )
    pending_count = append_unique_rows(
        service, SPREADSHEET_ID, ACCOUNTING_TAB, ACCOUNTING_HEADERS,
        rows["pending"], key_indexes=list(range(len(ACCOUNTING_HEADERS))),
    )

    return (
        "Accounting summary processed successfully.\n"
        f"Date: {report_date}\n"
        "Sections processed: Issues Requiring Attention, Cash & Bank Position, "
        "Sales, Purchase, Expenses & Journal Entries, GST/Tax Watch Items, "
        "Pending From Yesterday\n"
        f"Cash & Bank Position: {'updated' if rows['cash'] else '0 row(s)'}\n"
        f"Purchase: {'updated' if rows['purchase'] else '0 row(s)'}\n"
        f"Issues Requiring Attention: {issues_count} row(s)\n"
        f"Sales: {sales_count} row(s)\n"
        f"Expenses & Journal Entries: {expenses_count} row(s)\n"
        f"GST/Tax Watch Items: {tax_count} row(s)\n"
        f"Pending From Yesterday: {pending_count} row(s)"
    )


# -----------------------------
# Start MCP server
# -----------------------------
# Transport is chosen at runtime via MCP_TRANSPORT so this one entry point
# covers both phases without touching any tool code:
#
#   MCP_TRANSPORT unset / "stdio"   -> local dev, MCP Inspector (unchanged, default)
#   MCP_TRANSPORT=streamable-http   -> remote HTTP server for a Claude Pro
#                                       custom connector (Phase 2). Not run
#                                       anywhere yet - this just makes it
#                                       possible to opt in later.
#
# host/port for HTTP mode also come from the environment (never hard-coded)
# so the same code works locally and on a future hosting platform:
#   - PORT is read first since most hosting platforms (Render, Railway, etc.)
#     inject it automatically; MCP_PORT is a manual override; 8000 is the
#     final fallback.
#   - MCP_HOST defaults to 0.0.0.0 (not 127.0.0.1) so the server is
#     reachable from outside its container once deployed. This only takes
#     effect in HTTP mode - stdio mode never binds a network port.
#
# HTTP mode is also where bearer-token auth is enforced (see
# auth_middleware.py): MCP_AUTH_TOKEN is required in this branch and
# checked on every request except GET /health. stdio mode has no HTTP
# layer at all, so it is completely unaffected by any of this.
if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "stdio":
        mcp.run()
    elif transport == "streamable-http":
        import uvicorn

        host = os.getenv("MCP_HOST", "0.0.0.0")
        port = int(os.getenv("PORT", os.getenv("MCP_PORT", "8000")))

        auth_token = os.getenv("MCP_AUTH_TOKEN")
        if not auth_token:
            # Fail fast rather than ever silently serving the MCP endpoint
            # without authentication once this is reachable on a public URL.
            raise ValueError(
                "MCP_AUTH_TOKEN must be set when running with "
                "MCP_TRANSPORT=streamable-http - the remote MCP endpoint "
                "would otherwise be reachable by anyone with the URL."
            )

        # Build the same Starlette app mcp.run() would have used internally
        # (same routes: POST /mcp, GET /health), then wrap it with the
        # bearer-token check before handing it to uvicorn ourselves - this
        # is the only way to add middleware, since mcp.run() builds and
        # serves the app in one step without exposing a hook for it.
        app = mcp.streamable_http_app(streamable_http_path="/mcp", host=host)
        app = BearerTokenMiddleware(app, token=auth_token)

        uvicorn.run(app, host=host, port=port, log_level=mcp.settings.log_level.lower())
    else:
        raise ValueError(f"Unknown MCP_TRANSPORT: {transport!r}. Use 'stdio' or 'streamable-http'.")
