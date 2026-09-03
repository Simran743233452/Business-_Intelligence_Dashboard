import os

from mcp.server.mcpserver import MCPServer
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from starlette.requests import Request
from starlette.responses import JSONResponse

from summary_parser import parse_monitoring_summary, build_monitoring_rows, MONITORING_HEADERS
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
# What's Needed Next, Service Pattern Watch) now write into this ONE tab,
# distinguished by its Section column - see summary_parser.MONITORING_HEADERS
# for the exact column order and build_monitoring_rows() for how a parsed
# summary becomes rows. Previously each section had its own tab; those tabs
# (Daily Summary, Needs Attention, etc.) still exist in the spreadsheet but
# are no longer written to.
MONITORING_TAB = "Monitoring"

# Composite key for the Daily Summary row's upsert: (Date, Section) rather
# than Date alone, since this tab now also holds other sections' rows for
# the same date - without Section in the key, upserting today's Daily
# Summary row could otherwise match and overwrite a Needs Attention row
# that happens to share the same date.
_DATE_COL = MONITORING_HEADERS.index("Date")
_SECTION_COL = MONITORING_HEADERS.index("Section")


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
    sheet (one tab, one 12-column schema, distinguished by its Section
    column). This is the main tool for the Admin -> Claude -> Sheets
    workflow: paste the whole raw summary text as `summary`.

    Parsing is deterministic (no LLM call here) and tied to the fixed
    template. Re-running the same summary will not create duplicate rows.
    """

    # Deterministic parsing of the raw text into a structured dict, then
    # reshaped into per-section row lists sharing MONITORING_HEADERS'
    # column order - see summary_parser.py for both steps.
    parsed = parse_monitoring_summary(summary)
    rows = build_monitoring_rows(parsed)
    report_date = parsed["date"]

    # --- Daily Summary: exactly one row per (Date, Section); re-runs update it in place ---
    upsert_row_by_key(
        service, SPREADSHEET_ID, MONITORING_TAB, MONITORING_HEADERS,
        rows["daily_summary"][0], key_indexes=[_DATE_COL, _SECTION_COL],
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
