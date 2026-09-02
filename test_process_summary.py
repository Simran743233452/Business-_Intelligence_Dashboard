"""Local test for process_monitoring_summary() - no Claude Pro required.

Run with:
    python test_process_summary.py

This exercises the full flow (raw summary text -> parser -> Google Sheets)
using the credentials.json / SPREADSHEET_ID already configured in
mcp_server.py, so the Sheets-writing checks below write real rows into the
"Daily Summary", "Needs Attention", "Actions Taken", "What's Needed Next"
and "Service Pattern Watch" tabs of the existing spreadsheet. No Anthropic
API call is involved.

Determinism note: the Sheets-writing/idempotency check generates a fresh,
uniquely-tagged summary on every run (see _build_unique_summary below)
instead of reusing fixed site names and a fixed near-term date. Earlier
versions of this test used static content, which meant its "first write
creates 1 row" assertion depended on the spreadsheet *not already
containing* a row from some previous run - it failed once that stopped
being true. Generating fresh content each run makes the assertions
self-contained: they hold regardless of whatever the real spreadsheet
already has in it, and the synthetic year (2099) guarantees this test
never reads as, or overwrites, a real admin-submitted report for today.
"""

import uuid

from summary_parser import parse_monitoring_summary
from mcp_server import process_monitoring_summary

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
    "whats_needed_next": ["Approval needed for expedited shipping"],
    "service_pattern_watch": [
        {
            "pattern": "Recurring inverter failures at Bengaluru site",
            "site": "",
            "vendor": "",
            "notes": "",
        }
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


def _build_unique_summary(run_id: str, report_date: str) -> str:
    """Build a monitoring summary in the same shape as REPORTED_BUG_SUMMARY,
    but with `run_id` embedded in every Needs Attention / Actions Taken /
    What's Needed Next / Service Pattern Watch entry.

    Those four tabs de-duplicate on full-row content, so a run_id that has
    never been used before guarantees every row this produces is new -
    regardless of what the spreadsheet already contains. That's what makes
    the idempotency check below deterministic: run 1 is guaranteed to
    insert (nothing with this run_id can already exist), and run 2 with
    the *same* generated text is guaranteed to find exact duplicates of
    what run 1 just inserted.
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
- Approval needed for expedited shipping [{run_id}]

SERVICE PATTERN WATCH
- Recurring inverter failures at Bengaluru site [{run_id}]
"""


if __name__ == "__main__":
    print("=== Parser test 1/2: sections with no real data produce no rows ===")
    parsed = parse_monitoring_summary(NO_ISSUES_SUMMARY)
    print(parsed)
    assert parsed["date"] == "2026-09-01"
    assert parsed["needs_attention"] == [], "Expected no Needs Attention rows"
    assert parsed["actions_taken"] == [], "Expected no Actions Taken rows"
    assert parsed["whats_needed_next"] == [], "Expected no What's Needed Next rows"
    assert parsed["service_pattern_watch"] == [], "Expected no Service Pattern Watch rows"
    print("OK: sections with no real data produced no rows.\n")

    print("=== Parser test 2/2: previously-reported bug summary parses exactly as expected ===")
    bug_parsed = parse_monitoring_summary(REPORTED_BUG_SUMMARY)
    print(bug_parsed)
    assert bug_parsed == EXPECTED_PARSED_BUG_SUMMARY, (
        f"Parser output does not match expected structure.\nGot: {bug_parsed}"
    )
    print("OK: parser produces exactly the expected structure.\n")

    print("=== Google Sheets integration + idempotency check ===")
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
    print("OK: first write created exactly 1 new row in each section tab.\n")

    print("--- Second write (identical content): every section should report 0 new rows ---")
    result_2 = process_monitoring_summary(summary_text)
    print(result_2)
    assert "Needs Attention: 0 row(s)" in result_2, f"Expected 0 new Needs Attention rows, got: {result_2}"
    assert "Actions Taken: 0 row(s)" in result_2, f"Expected 0 new Actions Taken rows, got: {result_2}"
    assert "What's Needed Next: 0 row(s)" in result_2, f"Expected 0 new What's Needed Next rows, got: {result_2}"
    assert "Service Pattern Watch: 0 row(s)" in result_2, f"Expected 0 new Service Pattern Watch rows, got: {result_2}"
    print("OK: second write with identical content did not create duplicate rows.\n")

    print("All process_monitoring_summary() tests passed.")
