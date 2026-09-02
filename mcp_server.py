import os

from mcp.server.mcpserver import MCPServer
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from starlette.requests import Request
from starlette.responses import JSONResponse

from summary_parser import parse_monitoring_summary
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
SHEET_NAME = "Monitoring"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets"
]

# -----------------------------
# Tabs used by process_monitoring_summary
# -----------------------------
# Each section of the monitoring summary gets its own tab (created
# automatically on first write) instead of being crammed into the single
# issue-tracker-shaped "Monitoring" sheet used by the older tools above.
DAILY_SUMMARY_TAB = "Daily Summary"
DAILY_SUMMARY_HEADERS = ["Date", "New Issues", "Issues Resolved", "Total Open Issues", "Notes"]

NEEDS_ATTENTION_TAB = "Needs Attention"
NEEDS_ATTENTION_HEADERS = ["Date", "Site", "Issue", "Days Open", "Reason", "Status"]

ACTIONS_TAKEN_TAB = "Actions Taken"
ACTIONS_TAKEN_HEADERS = ["Date", "Site", "Issue", "Action Taken"]

WHATS_NEEDED_NEXT_TAB = "What's Needed Next"
WHATS_NEEDED_NEXT_HEADERS = ["Date", "Requirement", "Status"]

SERVICE_PATTERN_WATCH_TAB = "Service Pattern Watch"
SERVICE_PATTERN_WATCH_HEADERS = ["Date", "Pattern", "Site", "Vendor", "Notes"]


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
# Tool 2: Read Google Sheet
# -----------------------------
@mcp.tool()
def read_sheet() -> str:
    """Read all data from the Monitoring Google Sheet."""

    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=SHEET_NAME
    ).execute()

    values = result.get("values", [])

    if not values:
        return "The sheet is empty."

    return str(values)
@mcp.tool()
def add_issue(
    section: str,
    site: str,
    issue: str,
    count: int,
    days_open: int,
    action_taken: str,
    next_action: str,
    status: str,
    notes: str
) -> str:
    """Add a new business issue to the Monitoring Google Sheet."""

    from datetime import date

    row = [[
        str(date.today()),
        section,
        site,
        issue,
        count,
        days_open,
        action_taken,
        next_action,
        status,
        notes
    ]]

    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A:J",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": row}
    ).execute()

    return f"Issue added successfully: {issue}"
# ---------------------------
# Tool 4: Analyze Business Data
# ---------------------------

@mcp.tool()
def analyze_sheet() -> str:
    """Analyze the Monitoring Google Sheet and generate business insights."""

    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=SHEET_NAME
    ).execute()

    values = result.get("values", [])

    if len(values) <= 1:
        return "There is not enough data to analyze."

    headers = values[0]
    rows = values[1:]

    # Convert rows into dictionaries
    data = []

    for row in rows:
        item = {}

        for i, header in enumerate(headers):
            item[header] = row[i] if i < len(row) else ""

        data.append(item)

    # Basic analysis
    total_issues = len(data)

    open_issues = [
        row for row in data
        if row.get("Status", "").lower() == "open"
    ]

    resolved_issues = [
        row for row in data
        if row.get("Status", "").lower() == "resolved"
    ]

    # Find issue with highest count
    highest_issue = max(
        data,
        key=lambda x: int(x.get("Count", 0) or 0)
    )

    # Find oldest issue
    oldest_issue = max(
        data,
        key=lambda x: int(x.get("Days Open", 0) or 0)
    )

    return f"""
Business Analysis:

Total issues: {total_issues}

Open issues: {len(open_issues)}

Resolved issues: {len(resolved_issues)}

Highest impact issue:
{highest_issue.get("Issue", "Unknown")}
Count: {highest_issue.get("Count", "0")}

Oldest open issue:
{oldest_issue.get("Issue", "Unknown")}
Days open: {oldest_issue.get("Days Open", "0")}

Recommendation:
Prioritize the highest-count issue and investigate issues
that have remained open for the longest time.
"""
@mcp.tool()
def get_high_priority_issues() -> str:
    """Get business issues that need immediate attention."""

    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=SHEET_NAME
    ).execute()

    values = result.get("values", [])

    if len(values) <= 1:
        return "There are no issues to analyze."

    headers = values[0]
    rows = values[1:]

    data = []

    for row in rows:
        item = {}

        for i, header in enumerate(headers):
            item[header] = row[i] if i < len(row) else ""

        data.append(item)

    # High priority:
    # Count >= 5 OR issue open for 3+ days
    high_priority = [
        row for row in data
        if (
            int(row.get("Count", 0) or 0) >= 5
            or int(row.get("Days Open", 0) or 0) >= 3
        )
    ]

    if not high_priority:
        return "No high-priority issues found."

    result_text = "High-Priority Business Issues:\n\n"

    for issue in high_priority:
        result_text += (
            f"Issue: {issue.get('Issue', 'Unknown')}\n"
            f"Section: {issue.get('Section', 'Unknown')}\n"
            f"Site: {issue.get('Site', 'Unknown')}\n"
            f"Count: {issue.get('Count', '0')}\n"
            f"Days Open: {issue.get('Days Open', '0')}\n"
            f"Status: {issue.get('Status', 'Unknown')}\n"
            f"Next Action: {issue.get('Next Action', 'None')}\n"
            f"Notes: {issue.get('Notes', 'None')}\n"
            f"{'-' * 40}\n"
        )

    return result_text
# ---------------------------
# Tool 6: Update Business Issue
# ---------------------------

@mcp.tool()
def update_issue(
    issue: str,
    status: str,
    action_taken: str,
    next_action: str,
    notes: str
) -> str:
    """Update an existing business issue in the Monitoring Google Sheet."""

    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=SHEET_NAME
    ).execute()

    values = result.get("values", [])

    if len(values) <= 1:
        return "No issues found."

    headers = values[0]
    rows = values[1:]

    # Find the issue
    for index, row in enumerate(rows, start=2):

        current_issue = row[3] if len(row) > 3 else ""

        if current_issue.lower() == issue.lower():

            # Keep existing values for fields we are not changing
            date_value = row[0] if len(row) > 0 else ""
            section_value = row[1] if len(row) > 1 else ""
            site_value = row[2] if len(row) > 2 else ""
            count_value = row[4] if len(row) > 4 else 0
            days_open_value = row[5] if len(row) > 5 else 0

            updated_row = [[
                date_value,
                section_value,
                site_value,
                issue,
                count_value,
                days_open_value,
                action_taken,
                next_action,
                status,
                notes
            ]]

            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{SHEET_NAME}!A{index}:J{index}",
                valueInputOption="USER_ENTERED",
                body={"values": updated_row}
            ).execute()

            return f"Issue updated successfully: {issue}"

    return f"Issue not found: {issue}"

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
# Tool 7: Process a full Plant Monitoring Summary
# ---------------------------

@mcp.tool()
def process_monitoring_summary(summary: str) -> str:
    """Parse a complete SUNTROP SOLAR Plant Monitoring Summary and write
    every section - daily totals, needs attention, actions taken, what's
    needed next, and service pattern watch - into its own tab in the
    Google Sheet. This is the main tool for the Admin -> Claude -> Sheets
    workflow: paste the whole raw summary text as `summary`.

    Parsing is deterministic (no LLM call here) and tied to the fixed
    template. Re-running the same summary will not create duplicate rows.
    """

    # Deterministic parsing of the raw text into a structured dict.
    data = parse_monitoring_summary(summary)
    report_date = data["date"]

    # --- Daily Summary: exactly one row per date; re-runs update it in place ---
    daily_row = [
        report_date,
        data["new_issues"],
        data["resolved_issues"],
        data["total_open_issues"],
        "",  # Notes: not part of the current template, left for manual use
    ]
    upsert_row_by_key(
        service, SPREADSHEET_ID, DAILY_SUMMARY_TAB, DAILY_SUMMARY_HEADERS,
        daily_row, key_index=0,
    )

    # --- Needs Attention: one row per overdue/escalated item, if any ---
    needs_attention_rows = [
        [report_date, item["site"], item["issue"], item["days_open"], item["reason"], "Open"]
        for item in data["needs_attention"]
    ]
    needs_attention_count = append_unique_rows(
        service, SPREADSHEET_ID, NEEDS_ATTENTION_TAB, NEEDS_ATTENTION_HEADERS,
        needs_attention_rows, key_indexes=list(range(len(NEEDS_ATTENTION_HEADERS))),
    )

    # --- Actions Taken ---
    actions_taken_rows = [
        [report_date, item["site"], item["issue"], item["action"]]
        for item in data["actions_taken"]
    ]
    actions_taken_count = append_unique_rows(
        service, SPREADSHEET_ID, ACTIONS_TAKEN_TAB, ACTIONS_TAKEN_HEADERS,
        actions_taken_rows, key_indexes=list(range(len(ACTIONS_TAKEN_HEADERS))),
    )

    # --- What's Needed Next ---
    needed_next_rows = [
        [report_date, requirement, "Open"]
        for requirement in data["whats_needed_next"]
    ]
    needed_next_count = append_unique_rows(
        service, SPREADSHEET_ID, WHATS_NEEDED_NEXT_TAB, WHATS_NEEDED_NEXT_HEADERS,
        needed_next_rows, key_indexes=list(range(len(WHATS_NEEDED_NEXT_HEADERS))),
    )

    # --- Service Pattern Watch: only written when the section had real content ---
    pattern_rows = [
        [report_date, item["pattern"], item["site"], item["vendor"], item["notes"]]
        for item in data["service_pattern_watch"]
    ]
    pattern_count = append_unique_rows(
        service, SPREADSHEET_ID, SERVICE_PATTERN_WATCH_TAB, SERVICE_PATTERN_WATCH_HEADERS,
        pattern_rows, key_indexes=list(range(len(SERVICE_PATTERN_WATCH_HEADERS))),
    )

    return (
        "Monitoring summary processed successfully.\n"
        f"Date: {report_date}\n"
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
