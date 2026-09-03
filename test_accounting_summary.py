"""Local test for process_accounting_summary() - no Claude Pro required.

Run with:
    python test_accounting_summary.py

Exercises the full flow (raw Day Book summary text -> parser -> row-
building -> Google Sheets) using the credentials.json / SPREADSHEET_ID
already configured in mcp_server.py. The Sheets-writing/idempotency check
writes real rows into the "Accounting" tab (20-column schema, Record Type
column - see accounting_parser.ACCOUNTING_HEADERS). No Anthropic API call
is involved.

Determinism note: the Sheets-writing/idempotency check generates fresh,
uniquely-tagged content on every run (see _build_unique_summary) and uses
a synthetic year (2099) so it can never be mistaken for, or overwrite, a
real admin-submitted report - same approach as test_process_summary.py.
"""

import uuid

from accounting_parser import parse_accounting_summary, build_accounting_rows, ACCOUNTING_HEADERS
from mcp_server import process_accounting_summary, service, SPREADSHEET_ID, ACCOUNTING_TAB
from sheets_service import get_tab_values

# A clean, fully-populated Day Book summary covering every section with
# more than one item where the template allows it (2 exceptions, 2 tax
# watch items, 2 pending items) - proves multiple rows per section work.
CLEAN_SUMMARY = """SUNTROP SOLAR — DAY BOOK SUMMARY | 03 Sep 2026

ISSUES REQUIRING ATTENTION (if any)
- ABC Traders — GST mismatch of ₹12,000 found on vendor invoice; recommend verifying with vendor before payment
- Vendor XYZ Pvt Ltd — Payment of ₹45,000 overdue by 10 days; critical, recommend immediate follow-up

CASH & BANK POSITION
- Opening balance: ₹1,50,000 | Closing balance: ₹1,80,000
- Total receipts: ₹80,000 | Total payments: ₹50,000

SALES
- Invoices raised today: 5 | Total value: ₹2,50,000
- Sales Orders raised: 2 | Total value: ₹90,000
- Outstanding receivables (aging flag if any >45 days): ₹3,20,000, one customer over 45 days

PURCHASE
- Bills booked today: 3 | Total value: ₹1,10,000
- Any vendor GSTIN/HSN mismatches: N

EXPENSES & JOURNAL ENTRIES
- Notable/unusual entries today: Office rent ₹40,000 paid to Landlord Properties

GST/TAX WATCH ITEMS
- ITC discrepancy of ₹8,000 flagged on vendor invoice; recommend CA review this week
- RCM applicability unconfirmed for consulting services received

PENDING FROM YESTERDAY
- Vendor ABC Traders — GST reconciliation still awaiting verification; recommend follow-up call
- Bank reconciliation for account ending 4521 still pending
"""

# Uncertain wording, missing values, "none"/no-mismatch cases - the
# explicit uncertainty phrases from the spec must survive as-is, numeric
# fields must stay blank (never a fabricated 0) when the report itself
# says a figure isn't computable.
UNCERTAIN_SUMMARY = """SUNTROP SOLAR — DAY BOOK SUMMARY | 04 Sep 2026

ISSUES REQUIRING ATTENTION (if any)
- Large cash withdrawal of ₹75,000 recorded without supporting voucher; needs confirmation from accounts team before closing books

CASH & BANK POSITION
- Opening balance: not reliably computable due to two unreconciled bank accounts | Closing balance: ₹2,10,000
- Total receipts: ₹95,000 | Total payments: not derivable without yesterday's ledger

SALES
- Invoices raised today: 8 | Total value: ₹4,10,000
- Outstanding receivables (aging flag if any >45 days): ₹1,05,000, two customers over 60 days

PURCHASE
- Bills booked today: Unconfirmed, vendor portal was down most of the day
- Any vendor GSTIN/HSN mismatches: Y — Global Supplies Ltd HSN code mismatch on invoice #4521, needs confirmation

EXPENSES & JOURNAL ENTRIES
- Notable/unusual entries today: none

GST/TAX WATCH ITEMS
(Only if new/unresolved.)

PENDING FROM YESTERDAY
- Confirm bank reconciliation for account ending 4521
- Vendor Global Supplies Ltd — HSN mismatch still pending vendor response
- TDS deduction on professional fees not yet verified against 26AS, potential issue
"""


def _build_unique_summary(run_id: str, report_date: str) -> str:
    """Clean-shaped summary, uniquely tagged - see module docstring.
    Covers exceptions, cash, both sales sub-types, purchase, an expense,
    a tax item, and a pending item in one pass.
    """
    return f"""
SUNTROP SOLAR — DAY BOOK SUMMARY | {report_date}

ISSUES REQUIRING ATTENTION (if any)
- {run_id} Traders — Discrepancy of ₹5,000 found; recommend review

CASH & BANK POSITION
- Opening balance: ₹10,000 | Closing balance: ₹20,000
- Total receipts: ₹15,000 | Total payments: ₹5,000

SALES
- Invoices raised today: 1 | Total value: ₹5,000
- Sales Orders raised: 1 | Total value: ₹3,000

PURCHASE
- Bills booked today: 1 | Total value: ₹2,000
- Any vendor GSTIN/HSN mismatches: N

EXPENSES & JOURNAL ENTRIES
- Notable/unusual entries today: {run_id} expense entry ₹1,000

GST/TAX WATCH ITEMS
- {run_id} ITC discrepancy of ₹500 flagged

PENDING FROM YESTERDAY
- {run_id} follow-up still pending
"""


if __name__ == "__main__":
    print("=== Test 1: exact 20-column Accounting schema ===")
    assert ACCOUNTING_HEADERS == [
        "Date", "Record Type", "Entity", "Description", "Amount", "Count",
        "Priority", "Status", "Recommended Action", "Opening Balance",
        "Closing Balance", "Total Receipts", "Total Payments", "Sales Value",
        "Outstanding Receivables", "Purchase Value", "GSTIN/HSN Mismatch",
        "GST/Tax Watch", "Pending From Yesterday", "Notes",
    ], f"Unexpected schema: {ACCOUNTING_HEADERS}"
    assert len(ACCOUNTING_HEADERS) == 20
    assert "Section" not in ACCOUNTING_HEADERS
    print("OK\n")

    print("=== Test 2: correct tab name 'Accounting' ===")
    assert ACCOUNTING_TAB == "Accounting"
    print("OK\n")

    print("=== Test 3: clean summary - all sections parse, multiple rows per section ===")
    parsed = parse_accounting_summary(CLEAN_SUMMARY)
    rows = build_accounting_rows(parsed)
    assert parsed["date"] == "2026-09-03"
    assert len(rows["issues"]) == 2, f"Expected 2 exceptions, got {len(rows['issues'])}"
    assert len(rows["cash"]) == 1
    assert len(rows["sales"]) == 2, f"Expected 2 Sale rows (invoices + orders), got {len(rows['sales'])}"
    assert len(rows["purchase"]) == 1
    assert len(rows["expenses"]) == 1
    assert len(rows["tax"]) == 2, f"Expected 2 tax watch rows, got {len(rows['tax'])}"
    assert len(rows["pending"]) == 2, f"Expected 2 pending rows, got {len(rows['pending'])}"
    for section_rows in rows.values():
        for row in section_rows:
            assert len(row) == 20, f"Row has wrong column count: {row}"
    print("OK: all 7 sections produced correct row counts, all rows are 20 columns.\n")

    print("=== Test 4: multiple exceptions mapped correctly (Record Type, Entity, Amount, Priority, Status) ===")
    issue_rows = rows["issues"]
    assert all(row[ACCOUNTING_HEADERS.index("Record Type")] == "Exception" for row in issue_rows)
    entities = {row[ACCOUNTING_HEADERS.index("Entity")] for row in issue_rows}
    assert "ABC Traders" in entities
    assert "Vendor XYZ Pvt Ltd" in entities
    amounts = {row[ACCOUNTING_HEADERS.index("Amount")] for row in issue_rows}
    assert "₹12,000" in amounts and "₹45,000" in amounts
    critical_row = next(r for r in issue_rows if r[ACCOUNTING_HEADERS.index("Entity")] == "Vendor XYZ Pvt Ltd")
    assert critical_row[ACCOUNTING_HEADERS.index("Priority")] == "Critical"
    assert critical_row[ACCOUNTING_HEADERS.index("Status")] == "Open"
    print("OK\n")

    print("=== Test 5: cash position mapped correctly ===")
    cash_row = rows["cash"][0]
    assert cash_row[ACCOUNTING_HEADERS.index("Record Type")] == "Cash"
    assert cash_row[ACCOUNTING_HEADERS.index("Opening Balance")] == "₹1,50,000"
    assert cash_row[ACCOUNTING_HEADERS.index("Closing Balance")] == "₹1,80,000"
    assert cash_row[ACCOUNTING_HEADERS.index("Total Receipts")] == "₹80,000"
    assert cash_row[ACCOUNTING_HEADERS.index("Total Payments")] == "₹50,000"
    print("OK\n")

    print("=== Test 6: sales with invoices AND sales orders as separate rows ===")
    sales_rows = rows["sales"]
    assert all(r[ACCOUNTING_HEADERS.index("Record Type")] == "Sale" for r in sales_rows)
    counts_values = {(r[ACCOUNTING_HEADERS.index("Count")], r[ACCOUNTING_HEADERS.index("Sales Value")]) for r in sales_rows}
    assert (5, "₹2,50,000") in counts_values, counts_values
    assert (2, "₹90,000") in counts_values, counts_values
    receivables_rows = [r for r in sales_rows if r[ACCOUNTING_HEADERS.index("Outstanding Receivables")]]
    assert len(receivables_rows) == 1, "Expected receivables attached to exactly one Sale row"
    assert receivables_rows[0][ACCOUNTING_HEADERS.index("Outstanding Receivables")] == "₹3,20,000"
    assert "45 days" in receivables_rows[0][ACCOUNTING_HEADERS.index("Notes")]
    print("OK\n")

    print("=== Test 7: purchase information mapped correctly ===")
    purchase_row = rows["purchase"][0]
    assert purchase_row[ACCOUNTING_HEADERS.index("Record Type")] == "Purchase"
    assert purchase_row[ACCOUNTING_HEADERS.index("Count")] == 3
    assert purchase_row[ACCOUNTING_HEADERS.index("Purchase Value")] == "₹1,10,000"
    assert purchase_row[ACCOUNTING_HEADERS.index("GSTIN/HSN Mismatch")] == "N"
    print("OK\n")

    print("=== Test 8: expenses/journal entries mapped correctly ===")
    expense_row = rows["expenses"][0]
    assert expense_row[ACCOUNTING_HEADERS.index("Record Type")] == "Expense"
    assert "Office rent" in expense_row[ACCOUNTING_HEADERS.index("Description")]
    assert expense_row[ACCOUNTING_HEADERS.index("Amount")] == "₹40,000"
    print("OK\n")

    print("=== Test 9: GST/Tax watch items mapped correctly, including Unconfirmed status ===")
    tax_rows = rows["tax"]
    assert all(r[ACCOUNTING_HEADERS.index("Record Type")] == "Tax" for r in tax_rows)
    itc_row = next(r for r in tax_rows if "ITC" in r[ACCOUNTING_HEADERS.index("GST/Tax Watch")])
    assert itc_row[ACCOUNTING_HEADERS.index("Amount")] == "₹8,000"
    assert "recommend CA review" in itc_row[ACCOUNTING_HEADERS.index("Recommended Action")]
    rcm_row = next(r for r in tax_rows if "RCM" in r[ACCOUNTING_HEADERS.index("GST/Tax Watch")])
    assert rcm_row[ACCOUNTING_HEADERS.index("Status")] == "Unconfirmed"
    print("OK\n")

    print("=== Test 10: pending-from-yesterday items mapped correctly ===")
    pending_rows = rows["pending"]
    assert all(r[ACCOUNTING_HEADERS.index("Record Type")] == "Pending" for r in pending_rows)
    assert any(r[ACCOUNTING_HEADERS.index("Entity")] == "Vendor ABC Traders" for r in pending_rows)
    assert all(r[ACCOUNTING_HEADERS.index("Pending From Yesterday")] for r in pending_rows)
    print("OK\n")

    print("=== Test 11: uncertain wording preserved, missing values stay blank (never fabricated) ===")
    u_parsed = parse_accounting_summary(UNCERTAIN_SUMMARY)
    u_rows = build_accounting_rows(u_parsed)

    u_cash = u_rows["cash"][0]
    assert u_cash[ACCOUNTING_HEADERS.index("Opening Balance")] == "", "Opening Balance must stay blank, not a fabricated 0"
    assert "not reliably computable" in u_cash[ACCOUNTING_HEADERS.index("Notes")]
    assert u_cash[ACCOUNTING_HEADERS.index("Total Payments")] == "", "Total Payments must stay blank"
    assert "not derivable" in u_cash[ACCOUNTING_HEADERS.index("Notes")]
    assert u_cash[ACCOUNTING_HEADERS.index("Closing Balance")] == "₹2,10,000", "Explicit figures must still be parsed"

    u_purchase = u_rows["purchase"][0]
    assert u_purchase[ACCOUNTING_HEADERS.index("Count")] == "", "Bill count must stay blank when reported as Unconfirmed"
    assert "Unconfirmed" in u_purchase[ACCOUNTING_HEADERS.index("Notes")]
    assert u_purchase[ACCOUNTING_HEADERS.index("GSTIN/HSN Mismatch")] == (
        "Y — Global Supplies Ltd HSN code mismatch on invoice #4521, needs confirmation"
    ), u_purchase[ACCOUNTING_HEADERS.index("GSTIN/HSN Mismatch")]

    assert u_rows["expenses"] == [], "A bare 'none' must not create a fabricated Expense row"
    assert u_rows["tax"] == [], "An empty/placeholder Tax section must not create a fabricated row"

    u_issue = u_rows["issues"][0]
    assert u_issue[ACCOUNTING_HEADERS.index("Status")] == "Unconfirmed", (
        "'needs confirmation' must map to Status=Unconfirmed, never a false Open/Resolved"
    )

    tds_row = next(r for r in u_rows["pending"] if "TDS" in r[ACCOUNTING_HEADERS.index("Description")])
    assert tds_row[ACCOUNTING_HEADERS.index("Status")] == "Unconfirmed", "'potential' must map to Status=Unconfirmed"
    print("OK: 'not reliably computable' / 'not derivable' / 'Unconfirmed' / 'needs confirmation' / 'potential'")
    print("    all preserved without inventing numbers or false statuses.\n")

    print("=== Test 12: Google Sheets integration + idempotency check ===")
    run_uuid = uuid.uuid4()
    run_id = run_uuid.hex[:10]
    synthetic_date = f"2099-{(run_uuid.int % 12) + 1:02d}-{(run_uuid.int % 28) + 1:02d}"
    summary_text = _build_unique_summary(run_id, synthetic_date)
    print(f"Generated run_id={run_id!r}, synthetic date={synthetic_date!r}")

    result_1 = process_accounting_summary(summary_text)
    print(result_1)
    assert "Cash & Bank Position: updated" in result_1
    assert "Purchase: updated" in result_1
    assert "Issues Requiring Attention: 1 row(s)" in result_1
    assert "Sales: 2 row(s)" in result_1
    assert "Expenses & Journal Entries: 1 row(s)" in result_1
    assert "GST/Tax Watch Items: 1 row(s)" in result_1
    assert "Pending From Yesterday: 1 row(s)" in result_1

    all_rows = get_tab_values(service, SPREADSHEET_ID, ACCOUNTING_TAB)
    header = all_rows[0]
    assert header == ACCOUNTING_HEADERS, f"Live sheet header does not match: {header}"
    # Cash/Sales/Purchase lines in the generator don't embed run_id (their
    # template shape leaves no room for a free-text tag without breaking
    # amount extraction), so identify this run's rows by its unique
    # synthetic date instead - equally reliable since a fresh date is
    # generated every run specifically to avoid cross-run collisions.
    date_rows = [row for row in all_rows if row[0] == synthetic_date]
    # 1 issue + 1 cash + 2 sales + 1 purchase + 1 expense + 1 tax + 1 pending = 8
    assert len(date_rows) == 8, f"Expected 8 rows for date {synthetic_date!r} on first write, got {len(date_rows)}"
    tagged_rows = [row for row in date_rows if any(run_id in str(cell) for cell in row)]
    assert len(tagged_rows) == 4, f"Expected 4 run_id-tagged rows (issue/expense/tax/pending), got {len(tagged_rows)}"

    result_2 = process_accounting_summary(summary_text)
    print(result_2)
    assert "Issues Requiring Attention: 0 row(s)" in result_2
    assert "Sales: 0 row(s)" in result_2
    assert "Expenses & Journal Entries: 0 row(s)" in result_2
    assert "GST/Tax Watch Items: 0 row(s)" in result_2
    assert "Pending From Yesterday: 0 row(s)" in result_2
    # Cash/Purchase are upserts, so they still report "updated" (in place), not a count.
    assert "Cash & Bank Position: updated" in result_2
    assert "Purchase: updated" in result_2

    date_rows_after = [row for row in get_tab_values(service, SPREADSHEET_ID, ACCOUNTING_TAB) if row[0] == synthetic_date]
    assert len(date_rows_after) == len(date_rows), (
        f"Row count for date {synthetic_date!r} changed after a duplicate write: {len(date_rows)} -> {len(date_rows_after)}"
    )
    print(f"OK: first write created {len(date_rows)} rows total (incl. 1 Cash + 1 Purchase upsert); "
          "duplicate write created 0 additional rows, and Cash/Purchase were updated in place, not duplicated.\n")

    print("All process_accounting_summary() tests passed.")
