"""Local test for process_monitoring_summary() - no Claude Pro required.

Run with:
    python test_process_summary.py

This exercises the full flow (raw summary text -> parser -> row-building ->
Google Sheets) using the credentials.json / SPREADSHEET_ID already
configured in mcp_server.py. The Sheets-writing/idempotency check writes
real rows into the single unified "Monitoring" tab. No Anthropic API call
is involved.

Determinism note: the Sheets-writing/idempotency check generates a fresh,
uniquely-tagged summary on every run (see _build_unique_summary) instead of
reusing fixed site names and a fixed near-term date, and uses a synthetic
year (2099) so it can never be mistaken for, or overwrite, a real
admin-submitted report. See the "Google Sheets integration + idempotency
check" section below for why this makes the test self-contained regardless
of whatever the real spreadsheet already has in it.
"""

import uuid

from summary_parser import parse_monitoring_summary, build_monitoring_rows, MONITORING_HEADERS
from mcp_server import process_monitoring_summary, service, SPREADSHEET_ID, MONITORING_TAB
from sheets_service import get_tab_values

# Exact repro of a real parsing bug: this summary was returning 0 rows for
# every section. Root cause was the old line-by-line parser swallowing the
# whole message into the title line whenever section headers weren't
# reliably on their own physical line (e.g. an MCP client that collapses
# embedded newlines in a single-line text argument). Kept here verbatim as
# a parser-only regression test - it never touches Google Sheets, so it's
# already fully deterministic.
REPORTED_BUG_SUMMARY = """
SUNTROP SOLAR — PLANT MONITORING SUMMARY | 02 Sep 2026

ISSUES TODAY
- New issues detected: 3
- Issues resolved today: 2
- Total issues currently open: 7

⚠️ NEEDS ATTENTION
- Bengaluru — Inverter failure — open 8 days — awaiting vendor replacement

ACTIONS TAKEN TODAY
- Mysuru — Panel fault — replacement scheduled

WHAT'S NEEDED NEXT
- Approval needed for expedited shipping

SERVICE PATTERN WATCH
- Recurring inverter failures at Bengaluru site
"""

EXPECTED_PARSED_BUG_SUMMARY = {
    "date": "2026-09-02",
    "new_issues": 3,
    "resolved_issues": 2,
    "total_open_issues": 7,
    "needs_attention": [
        {
            "site": "Bengaluru",
            "issue": "Inverter failure",
            "days_open": 8,
            "reason": "awaiting vendor replacement",
        }
    ],
    "actions_taken": [
        {
            "site": "Mysuru",
            "issue": "Panel fault",
            "action": "replacement scheduled",
        }
    ],
    # No dash in the source bullet -> no site prefix -> site stays blank.
    "whats_needed_next": [{"site": "", "requirement": "Approval needed for expedited shipping"}],
    "service_pattern_watch": [
        {
            "pattern": "Recurring inverter failures at Bengaluru site",
            "site": "",
            "vendor": "",
            "notes": "",
        }
    ],
}

# Expected unified-schema rows for REPORTED_BUG_SUMMARY, in MONITORING_HEADERS
# column order: Date, Section, Site, Description, Days Open, Action Taken,
# What's Needed Next, New Issues, Issues Resolved, Total Open Issues,
# Vendor, Notes.
EXPECTED_BUG_SUMMARY_ROWS = {
    "daily_summary": [["2026-09-02", "Daily Summary", "", "", "", "", "", 3, 2, 7, "", ""]],
    "needs_attention": [
        ["2026-09-02", "Needs Attention", "Bengaluru", "Inverter failure", 8, "", "", "", "", "", "", "awaiting vendor replacement"]
    ],
    "actions_taken": [
        ["2026-09-02", "Actions Taken", "Mysuru", "Panel fault", "", "replacement scheduled", "", "", "", "", "", ""]
    ],
    "whats_needed_next": [
        ["2026-09-02", "What's Needed Next", "", "", "", "", "Approval needed for expedited shipping", "", "", "", "", ""]
    ],
    "service_pattern_watch": [
        ["2026-09-02", "Service Pattern Watch", "", "Recurring inverter failures at Bengaluru site", "", "", "", "", "", "", "", ""]
    ],
}

# A summary with nothing to escalate - none of these sections should
# produce fabricated rows. Also parser-only (no Sheets write).
NO_ISSUES_SUMMARY = """
SUNTROP SOLAR — PLANT MONITORING SUMMARY | September 1, 2026

ISSUES TODAY
- New issues detected: 0
- Issues resolved today: 0
- Total issues currently open: 3

⚠️ NEEDS ATTENTION
- No overdue or escalated issues today.

ACTIONS TAKEN TODAY

WHAT'S NEEDED NEXT

SERVICE PATTERN WATCH
(Only when relevant.)
"""

# The SERVICE PATTERN WATCH header is entirely absent (not just empty) -
# proves the parser doesn't require every section header to be present.
NO_SERVICE_PATTERN_WATCH_SUMMARY = """
SUNTROP SOLAR — PLANT MONITORING SUMMARY | 2026-09-03

ISSUES TODAY
- New issues detected: 1
- Issues resolved today: 0
- Total issues currently open: 4

NEEDS ATTENTION
- No overdue or escalated issues today.

ACTIONS TAKEN TODAY
- Kolar Site 2 — Sensor malfunction — Replaced faulty sensor

WHAT'S NEEDED NEXT
- Kolar Site 2 — Firmware update pending
"""


def _build_unique_summary(run_id: str, report_date: str) -> str:
    """Build a monitoring summary in the same shape as REPORTED_BUG_SUMMARY,
    but with `run_id` embedded in every Needs Attention / Actions Taken /
    What's Needed Next / Service Pattern Watch entry.

    All four of those sections de-duplicate on full-row content within the
    shared Monitoring tab, so a run_id that has never been used before
    guarantees every row this produces is new - regardless of what the
    spreadsheet already contains. That's what makes the idempotency check
    below deterministic: run 1 is guaranteed to insert (nothing with this
    run_id can already exist), and run 2 with the *same* generated text is
    guaranteed to find exact duplicates of what run 1 just inserted.
    """
    return f"""
SUNTROP SOLAR — PLANT MONITORING SUMMARY | {report_date}

ISSUES TODAY
- New issues detected: 3
- Issues resolved today: 2
- Total issues currently open: 7

⚠️ NEEDS ATTENTION
- {run_id}-Bengaluru — Inverter failure — open 8 days — awaiting vendor replacement

ACTIONS TAKEN TODAY
- {run_id}-Mysuru — Panel fault — replacement scheduled

WHAT'S NEEDED NEXT
- {run_id}-Mysuru — Follow-up call needed with vendor

SERVICE PATTERN WATCH
- Recurring inverter failures at Bengaluru site [{run_id}]
"""


if __name__ == "__main__":
    print("=== Test 1: MONITORING_HEADERS is the exact 12-column unified schema ===")
    assert MONITORING_HEADERS == [
        "Date", "Section", "Site", "Description", "Days Open", "Action Taken",
        "What's Needed Next", "New Issues", "Issues Resolved", "Total Open Issues",
        "Vendor", "Notes",
    ], f"Unexpected schema: {MONITORING_HEADERS}"
    assert len(MONITORING_HEADERS) == 12
    print("OK\n")

    print("=== Test 2: sections with no real data produce no rows ===")
    parsed = parse_monitoring_summary(NO_ISSUES_SUMMARY)
    print(parsed)
    assert parsed["date"] == "2026-09-01"
    assert parsed["needs_attention"] == [], "Expected no Needs Attention rows"
    assert parsed["actions_taken"] == [], "Expected no Actions Taken rows"
    assert parsed["whats_needed_next"] == [], "Expected no What's Needed Next rows"
    assert parsed["service_pattern_watch"] == [], "Expected no Service Pattern Watch rows"
    print("OK: sections with no real data produced no rows.\n")

    print("=== Test 3: previously-reported bug summary parses exactly as expected ===")
    bug_parsed = parse_monitoring_summary(REPORTED_BUG_SUMMARY)
    print(bug_parsed)
    assert bug_parsed == EXPECTED_PARSED_BUG_SUMMARY, (
        f"Parser output does not match expected structure.\nGot: {bug_parsed}"
    )
    print("OK: parser produces exactly the expected structure.\n")

    print("=== Test 4: MCP Inspector-style collapsed/whitespace-normalized input still parses correctly ===")
    collapsed = REPORTED_BUG_SUMMARY.replace("\n", " ")
    collapsed_parsed = parse_monitoring_summary(collapsed)
    assert collapsed_parsed == EXPECTED_PARSED_BUG_SUMMARY, (
        f"Collapsed input did not parse the same as normal input.\nGot: {collapsed_parsed}"
    )
    print("OK: collapsed single-line input parses identically to normal multi-line input.\n")

    print("=== Test 5: build_monitoring_rows() maps every section to the correct 12-column rows ===")
    bug_rows = build_monitoring_rows(bug_parsed)
    for section, expected_rows in EXPECTED_BUG_SUMMARY_ROWS.items():
        actual_rows = bug_rows[section]
        assert actual_rows == expected_rows, (
            f"{section}: expected {expected_rows}, got {actual_rows}"
        )
        for row in actual_rows:
            assert len(row) == len(MONITORING_HEADERS), f"{section} row has wrong column count: {row}"
    print("OK: Daily Summary, Needs Attention, Actions Taken, What's Needed Next, and")
    print("    Service Pattern Watch each produced the correct 12-column Monitoring row.\n")

    print("=== Test 6: a summary without a Service Pattern Watch section still works ===")
    no_spw_parsed = parse_monitoring_summary(NO_SERVICE_PATTERN_WATCH_SUMMARY)
    print(no_spw_parsed)
    assert no_spw_parsed["service_pattern_watch"] == []
    assert no_spw_parsed["needs_attention"] == []
    assert len(no_spw_parsed["actions_taken"]) == 1
    assert no_spw_parsed["actions_taken"][0]["site"] == "Kolar Site 2"
    assert len(no_spw_parsed["whats_needed_next"]) == 1
    assert no_spw_parsed["whats_needed_next"][0] == {"site": "Kolar Site 2", "requirement": "Firmware update pending"}
    no_spw_rows = build_monitoring_rows(no_spw_parsed)
    assert no_spw_rows["service_pattern_watch"] == []
    print("OK: summary without Service Pattern Watch parses and builds rows correctly.\n")

    print("=== Test 7: Google Sheets integration + idempotency check (writes to 'Monitoring' tab) ===")
    # A fresh id/date every run - see _build_unique_summary docstring for
    # why this is what makes the assertions below deterministic rather
    # than dependent on the spreadsheet's current contents. Year 2099
    # keeps this test from ever being mistaken for (or overwriting) a real
    # admin-submitted report.
    run_uuid = uuid.uuid4()
    run_id = run_uuid.hex[:10]
    synthetic_date = f"2099-{(run_uuid.int % 12) + 1:02d}-{(run_uuid.int % 28) + 1:02d}"
    summary_text = _build_unique_summary(run_id, synthetic_date)
    print(f"Generated run_id={run_id!r}, synthetic date={synthetic_date!r}")

    print("\n--- First write: every section should report exactly 1 new row ---")
    result_1 = process_monitoring_summary(summary_text)
    print(result_1)
    assert "Needs Attention: 1 row(s)" in result_1, f"Expected 1 new Needs Attention row, got: {result_1}"
    assert "Actions Taken: 1 row(s)" in result_1, f"Expected 1 new Actions Taken row, got: {result_1}"
    assert "What's Needed Next: 1 row(s)" in result_1, f"Expected 1 new What's Needed Next row, got: {result_1}"
    assert "Service Pattern Watch: 1 row(s)" in result_1, f"Expected 1 new Service Pattern Watch row, got: {result_1}"
    print("OK: first write created exactly 1 new row for each section.\n")

    print("--- Verifying the written rows actually landed in the 'Monitoring' tab with correct fields ---")
    all_rows = get_tab_values(service, SPREADSHEET_ID, MONITORING_TAB)
    tagged_rows = [row for row in all_rows if any(run_id in str(cell) for cell in row)]
    sections_seen = {row[1] for row in tagged_rows}
    assert sections_seen == {"Needs Attention", "Actions Taken", "What's Needed Next", "Service Pattern Watch"}, (
        f"Expected all 4 tagged sections present, got: {sections_seen}"
    )
    for row in tagged_rows:
        assert row[0] == synthetic_date, f"Row has wrong date: {row}"
    print(f"OK: found {len(tagged_rows)} rows tagged with run_id={run_id!r}, all under the correct sections/date.\n")

    print("--- Second write (identical content): every section should report 0 new rows ---")
    result_2 = process_monitoring_summary(summary_text)
    print(result_2)
    assert "Needs Attention: 0 row(s)" in result_2, f"Expected 0 new Needs Attention rows, got: {result_2}"
    assert "Actions Taken: 0 row(s)" in result_2, f"Expected 0 new Actions Taken rows, got: {result_2}"
    assert "What's Needed Next: 0 row(s)" in result_2, f"Expected 0 new What's Needed Next rows, got: {result_2}"
    assert "Service Pattern Watch: 0 row(s)" in result_2, f"Expected 0 new Service Pattern Watch rows, got: {result_2}"
    print("OK: second write with identical content did not create duplicate rows.\n")

    print("--- Confirming no duplicates actually landed in the sheet ---")
    all_rows_after = get_tab_values(service, SPREADSHEET_ID, MONITORING_TAB)
    tagged_rows_after = [row for row in all_rows_after if any(run_id in str(cell) for cell in row)]
    assert len(tagged_rows_after) == len(tagged_rows), (
        f"Row count for run_id={run_id!r} changed after a duplicate write: "
        f"{len(tagged_rows)} -> {len(tagged_rows_after)}"
    )
    print(f"OK: still exactly {len(tagged_rows_after)} rows tagged with run_id={run_id!r} after the duplicate write.\n")

    print("All process_monitoring_summary() tests passed.")
