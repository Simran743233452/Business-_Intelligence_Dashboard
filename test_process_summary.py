"""Local test for process_monitoring_summary() - no Claude Pro required.

Run with:
    python test_process_summary.py

This exercises the full flow (raw summary text -> parser -> row-building ->
Google Sheets) using the credentials.json / SPREADSHEET_ID already
configured in mcp_server.py. The Sheets-writing/idempotency checks write
real rows into the single unified "Monitoring" tab. No Anthropic API call
is involved.

Covers two input shapes:
  A. The clean template (REPORTED_BUG_SUMMARY, NO_ISSUES_SUMMARY).
  B. A real-world messy report (MESSY_SUMMARY) where values aren't clean:
     "Unconfirmed" metrics, multi-site bullets (some with per-site values
     mapped by position), numbered What's Needed Next items, and free-text
     qualifiers ("not explicitly dated to today", "not appearing in
     today's report") that must survive rather than being dropped.

Determinism note: the Sheets-writing/idempotency checks generate fresh,
uniquely-tagged content on every run (see _build_unique_summary and
_build_unique_messy_summary) instead of reusing fixed site names and a
fixed near-term date, and use a synthetic year (2099) so they can never be
mistaken for, or overwrite, a real admin-submitted report.
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
    "issues_today_notes": "",
    "needs_attention": [
        {
            "site": "Bengaluru",
            "description": "Inverter failure — awaiting vendor replacement",
            "days_open": 8,
        }
    ],
    "actions_taken": [
        {"site": "Mysuru", "description": "Panel fault", "action": "replacement scheduled"}
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
# Vendor, Pattern/Notes.
EXPECTED_BUG_SUMMARY_ROWS = {
    "daily_summary": [["2026-09-02", "Daily Summary", "", "", "", "", "", 3, 2, 7, "", ""]],
    "needs_attention": [
        ["2026-09-02", "Needs Attention", "Bengaluru", "Inverter failure — awaiting vendor replacement", 8, "", "", "", "", "", "", ""]
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

# The real-world report that previously "did not parse/work correctly":
# unclean/free-text metrics, multi-site bullets (some with per-site values
# in matching order), numbered What's Needed Next items, and qualifiers
# that must not be dropped. No title line, on purpose - proves the parser
# still degrades gracefully (falls back to today's date) rather than
# crashing or losing the rest of the content when the title is missing.
MESSY_SUMMARY = """ISSUES TODAY

- New issues detected: Unconfirmed — can't distinguish new vs. recurring without tracker
- Issues resolved today: Unconfirmed — need tracker to verify any issue moved to "Done at Site"
- Total issues currently open: 18 sites reporting today (5 inverter-only, 12 optimizer-only, 1 with both — see below)

NEEDS ATTENTION

- 079 Oaza Global Krishnagiri — full outage today (all 4 inverters down, 280 min collectively) plus 31 optimizers at 100% down and 59 at 50% down out of 615, with remark "inverter no 5 is not working."
- 034 IIM Bangalore-2023, 017 Prabhu Kanakpura Road, 075 nVent Rajadhani Paper Bidadi, 066 Matrinox Riddhi Siddhi Metal Jigani — not appearing in today's report at all.
- 027 Ranganath babu Mahalakshmi layout, 055 MediTech, 072 Harish Pillai — repeated inverter tripping today (10x/350min, 14x/135min, 7x/205min respectively).

ACTIONS TAKEN TODAY

- 011 R P Metal Sections Pvt Ltd Bidadi — 3 optimizers received; noted "tomorrow going for replacement"
- 084 Bentley India Pvt Ltd — 1 optimizer received; noted "tomorrow going for replacement"
- 088 Mechano Unit 1 — assigned to Rakesh for on-site check
- 008, 009, 012, 022, 024, 060 — remarks show "case filed/approved/received," but these read as status labels, not explicitly dated to today.
- 042 Jain Mission Trust — "one received, one case filed"
- 056 Shree LakshmiNarasimha Agro — one optimizer received, one still needs on-site check

WHAT'S NEEDED NEXT

1. Upload the Master Issue Tracker so I can reconcile today's 18 sites...
2. Confirm which remarks above reflect actions taken today...
3. Status check needed on 034, 017, 075, 066...
4. Site visit confirmation needed for 011 and 084 tomorrow...

SERVICE PATTERN WATCH

- 079 Oaza Global Krishnagiri showing simultaneous full inverter outage...
- Three separate sites (027, 055, 072) logged inverter tripping today...
"""


def _build_unique_summary(run_id: str, report_date: str) -> str:
    """Clean-template summary, uniquely tagged - see module docstring."""
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


def _build_unique_messy_summary(run_id: str, report_date: str) -> str:
    """Messy-style summary, uniquely tagged, covering the same tricky
    shapes as MESSY_SUMMARY (Unconfirmed metric, a multi-site bullet with
    per-site values mapped by position, a numbered What's Needed Next
    list, and a qualifier that must survive) so the live Sheets
    round-trip test also exercises that code path, deterministically.
    """
    return f"""
SUNTROP SOLAR — PLANT MONITORING SUMMARY | {report_date}

ISSUES TODAY
- New issues detected: Unconfirmed — needs tracker [{run_id}]
- Issues resolved today: 1
- Total issues currently open: 5

NEEDS ATTENTION
- 101 {run_id} Site A, 102 {run_id} Site B — repeated tripping today (3x/50min, 5x/70min respectively).

ACTIONS TAKEN TODAY
- 201 {run_id} Site C — case filed, not yet confirmed, follow up tomorrow

WHAT'S NEEDED NEXT
1. Confirm status for {run_id} sites tomorrow...
2. Escalate {run_id} priority items...

SERVICE PATTERN WATCH
- Chronic recurring pattern at {run_id} sites, priority escalation recommended
"""


if __name__ == "__main__":
    print("=== Test 1: MONITORING_HEADERS is the exact 12-column unified schema ===")
    assert MONITORING_HEADERS == [
        "Date", "Section", "Site", "Description", "Days Open", "Action Taken",
        "What's Needed Next", "New Issues", "Issues Resolved", "Total Open Issues",
        "Vendor", "Pattern/Notes",
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
        assert actual_rows == expected_rows, f"{section}: expected {expected_rows}, got {actual_rows}"
        for row in actual_rows:
            assert len(row) == len(MONITORING_HEADERS), f"{section} row has wrong column count: {row}"
    print("OK: all five sections produced the correct 12-column Monitoring row(s).\n")

    print("=== Test 6: a summary without a Service Pattern Watch section still works ===")
    no_spw = MESSY_SUMMARY.split("SERVICE PATTERN WATCH")[0]
    no_spw_parsed = parse_monitoring_summary(no_spw)
    assert no_spw_parsed["service_pattern_watch"] == []
    assert len(no_spw_parsed["actions_taken"]) > 0
    no_spw_rows = build_monitoring_rows(no_spw_parsed)
    assert no_spw_rows["service_pattern_watch"] == []
    print("OK: summary without Service Pattern Watch parses and builds rows correctly.\n")

    print("=== Test 7: real-world messy summary - all five sections detected, nothing crashes ===")
    messy_parsed = parse_monitoring_summary(MESSY_SUMMARY)
    for key in ("needs_attention", "actions_taken", "whats_needed_next", "service_pattern_watch"):
        assert len(messy_parsed[key]) > 0, f"Expected at least one {key} row"
    assert messy_parsed["new_issues"] != 0 and messy_parsed["resolved_issues"] != ""
    print(f"needs_attention: {len(messy_parsed['needs_attention'])} rows")
    print(f"actions_taken: {len(messy_parsed['actions_taken'])} rows")
    print(f"whats_needed_next: {len(messy_parsed['whats_needed_next'])} rows")
    print(f"service_pattern_watch: {len(messy_parsed['service_pattern_watch'])} rows")
    print("OK: all five sections detected.\n")

    print("=== Test 8: Unconfirmed metrics are preserved, not converted to 0 ===")
    assert isinstance(messy_parsed["new_issues"], str) and "Unconfirmed" in messy_parsed["new_issues"], (
        f"Expected new_issues to preserve 'Unconfirmed' text, got: {messy_parsed['new_issues']!r}"
    )
    assert isinstance(messy_parsed["resolved_issues"], str) and "Unconfirmed" in messy_parsed["resolved_issues"], (
        f"Expected resolved_issues to preserve 'Unconfirmed' text, got: {messy_parsed['resolved_issues']!r}"
    )
    assert messy_parsed["total_open_issues"] == 18, (
        f"Expected total_open_issues to parse the explicit number 18, got: {messy_parsed['total_open_issues']!r}"
    )
    assert "sites reporting today" in messy_parsed["issues_today_notes"], (
        "Expected the extra context after '18' to be preserved in issues_today_notes, "
        f"got: {messy_parsed['issues_today_notes']!r}"
    )
    print("new_issues:", messy_parsed["new_issues"])
    print("resolved_issues:", messy_parsed["resolved_issues"])
    print("total_open_issues:", messy_parsed["total_open_issues"])
    print("OK: 'Unconfirmed' preserved verbatim; explicit numeric value still parsed when present.\n")

    print("=== Test 9: multiple-site bullets are split into rows, not silently lost ===")
    na_sites = [item["site"] for item in messy_parsed["needs_attention"]]
    # The 4-site "not appearing in today's report" bullet -> 4 separate rows.
    for code in ("034 IIM Bangalore-2023", "017 Prabhu Kanakpura Road",
                 "075 nVent Rajadhani Paper Bidadi", "066 Matrinox Riddhi Siddhi Metal Jigani"):
        assert code in na_sites, f"Expected site {code!r} to have its own Needs Attention row"
    not_appearing_rows = [item for item in messy_parsed["needs_attention"] if "not appearing" in item["description"]]
    assert len(not_appearing_rows) == 4, f"Expected 4 rows for the 'not appearing' bullet, got {len(not_appearing_rows)}"

    # The 3-site tripping bullet with per-site values, mapped by position.
    tripping_by_site = {
        item["site"]: item["description"]
        for item in messy_parsed["needs_attention"]
        if "tripping" in item["description"]
    }
    assert tripping_by_site["027 Ranganath babu Mahalakshmi layout"].endswith("(10x/350min)")
    assert tripping_by_site["055 MediTech"].endswith("(14x/135min)")
    assert tripping_by_site["072 Harish Pillai"].endswith("(7x/205min)")

    # The 6 bare-code Actions Taken bullet -> 6 separate rows, same text.
    at_sites = [item["site"] for item in messy_parsed["actions_taken"]]
    for code in ("008", "009", "012", "022", "024", "060"):
        assert code in at_sites, f"Expected site code {code!r} to have its own Actions Taken row"
    print(f"Needs Attention rows: {len(messy_parsed['needs_attention'])} (1 + 4 + 3 sites = 8 expected)")
    assert len(messy_parsed["needs_attention"]) == 8
    print("OK: multi-site bullets correctly split into per-site rows, with per-site values mapped by position.\n")

    print("=== Test 10: numbered What's Needed Next items are preserved individually ===")
    requirements = [item["requirement"] for item in messy_parsed["whats_needed_next"]]
    assert len(requirements) == 4, f"Expected 4 numbered items, got {len(requirements)}: {requirements}"
    assert any("Master Issue Tracker" in r for r in requirements)
    assert any("Status check needed on 034, 017, 075, 066" in r for r in requirements)
    assert any(r.rstrip().endswith("tomorrow...") for r in requirements), (
        "Expected the last numbered item's trailing '...' to survive intact"
    )
    print("OK: all 4 numbered items preserved as individual rows, full text intact.\n")

    print("=== Test 11: Service Pattern Watch information is preserved ===")
    patterns = [item["pattern"] for item in messy_parsed["service_pattern_watch"]]
    assert len(patterns) == 2
    assert any("Oaza Global Krishnagiri" in p and "outage" in p for p in patterns)
    assert any("Three separate sites (027, 055, 072)" in p for p in patterns)
    print("OK: both Service Pattern Watch entries preserved in full.\n")

    print("=== Test 12: Actions Taken qualifiers are preserved, not stripped ===")
    actions_text = " | ".join(item["action"] for item in messy_parsed["actions_taken"])
    for qualifier in ("tomorrow", "not explicitly dated to today"):
        assert qualifier in actions_text, f"Expected qualifier {qualifier!r} to survive somewhere in Actions Taken"
    print("OK: qualifiers like 'tomorrow' and 'not explicitly dated to today' survived.\n")

    print("=== Test 13: no useful information disappears (spot-check a few distinctive phrases) ===")
    full_text_dump = str(messy_parsed)
    for phrase in (
        "inverter no 5 is not working",
        "case filed/approved/received",
        "one received, one case filed",
        "Confirm which remarks above reflect actions taken today",
    ):
        assert phrase in full_text_dump, f"Expected {phrase!r} to appear somewhere in the parsed output"
    print("OK: spot-checked distinctive phrases all survived somewhere in the parsed output.\n")

    print("=== Test 14: Google Sheets integration + idempotency check (clean-template style) ===")
    run_uuid = uuid.uuid4()
    run_id = run_uuid.hex[:10]
    synthetic_date = f"2099-{(run_uuid.int % 12) + 1:02d}-{(run_uuid.int % 28) + 1:02d}"
    summary_text = _build_unique_summary(run_id, synthetic_date)
    print(f"Generated run_id={run_id!r}, synthetic date={synthetic_date!r}")

    result_1 = process_monitoring_summary(summary_text)
    print(result_1)
    assert "Needs Attention: 1 row(s)" in result_1
    assert "Actions Taken: 1 row(s)" in result_1
    assert "What's Needed Next: 1 row(s)" in result_1
    assert "Service Pattern Watch: 1 row(s)" in result_1

    all_rows = get_tab_values(service, SPREADSHEET_ID, MONITORING_TAB)
    tagged_rows = [row for row in all_rows if any(run_id in str(cell) for cell in row)]
    sections_seen = {row[1] for row in tagged_rows}
    assert sections_seen == {"Needs Attention", "Actions Taken", "What's Needed Next", "Service Pattern Watch"}
    for row in tagged_rows:
        assert row[0] == synthetic_date, f"Row has wrong date: {row}"

    result_2 = process_monitoring_summary(summary_text)
    print(result_2)
    assert "Needs Attention: 0 row(s)" in result_2
    assert "Actions Taken: 0 row(s)" in result_2
    assert "What's Needed Next: 0 row(s)" in result_2
    assert "Service Pattern Watch: 0 row(s)" in result_2

    tagged_rows_after = [row for row in get_tab_values(service, SPREADSHEET_ID, MONITORING_TAB) if any(run_id in str(cell) for cell in row)]
    assert len(tagged_rows_after) == len(tagged_rows), (
        f"Row count for run_id={run_id!r} changed after a duplicate write: {len(tagged_rows)} -> {len(tagged_rows_after)}"
    )
    print(f"OK: first write created {len(tagged_rows)} new rows; duplicate write created 0 more.\n")

    print("=== Test 15: Google Sheets integration + idempotency check (messy-style, multi-row bullets) ===")
    messy_run_uuid = uuid.uuid4()
    messy_run_id = messy_run_uuid.hex[:10]
    messy_synthetic_date = f"2099-{(messy_run_uuid.int % 12) + 1:02d}-{(messy_run_uuid.int % 28) + 1:02d}"
    messy_summary_text = _build_unique_messy_summary(messy_run_id, messy_synthetic_date)
    print(f"Generated run_id={messy_run_id!r}, synthetic date={messy_synthetic_date!r}")

    messy_result_1 = process_monitoring_summary(messy_summary_text)
    print(messy_result_1)
    # The 2-site tripping bullet produces 2 Needs Attention rows.
    assert "Needs Attention: 2 row(s)" in messy_result_1, messy_result_1
    assert "Actions Taken: 1 row(s)" in messy_result_1, messy_result_1
    assert "What's Needed Next: 2 row(s)" in messy_result_1, messy_result_1
    assert "Service Pattern Watch: 1 row(s)" in messy_result_1, messy_result_1

    messy_tagged_before = [
        row for row in get_tab_values(service, SPREADSHEET_ID, MONITORING_TAB)
        if any(messy_run_id in str(cell) for cell in row)
    ]

    messy_result_2 = process_monitoring_summary(messy_summary_text)
    print(messy_result_2)
    assert "Needs Attention: 0 row(s)" in messy_result_2, messy_result_2
    assert "Actions Taken: 0 row(s)" in messy_result_2, messy_result_2
    assert "What's Needed Next: 0 row(s)" in messy_result_2, messy_result_2
    assert "Service Pattern Watch: 0 row(s)" in messy_result_2, messy_result_2

    messy_tagged_after = [
        row for row in get_tab_values(service, SPREADSHEET_ID, MONITORING_TAB)
        if any(messy_run_id in str(cell) for cell in row)
    ]
    assert len(messy_tagged_after) == len(messy_tagged_before), (
        f"Row count changed after a duplicate write of the messy-style summary: "
        f"{len(messy_tagged_before)} -> {len(messy_tagged_after)}"
    )
    print(f"OK: first write created {len(messy_tagged_before)} new rows (including split multi-site rows); "
          "duplicate write created 0 more.\n")

    print("All process_monitoring_summary() tests passed.")
