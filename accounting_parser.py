"""Deterministic, loss-minimizing parser for the SUNTROP SOLAR Day Book
(Accounting) Summary.

No LLM calls happen here on purpose - matches the design of
summary_parser.py (the Monitoring parser). This module is intentionally
self-contained (does not import from summary_parser.py) even though the
two share a similar architecture - Monitoring must not change as a result
of this work, so nothing here is refactored into a shared module that
Monitoring would also depend on.

Expected template:

    SUNTROP SOLAR — DAY BOOK SUMMARY | [Date]

    ISSUES REQUIRING ATTENTION (if any)
    - [Issue 1: one-line description + amount + recommended action]
    - [Issue 2: ...]

    CASH & BANK POSITION
    - Opening balance: ₹___ | Closing balance: ₹___
    - Total receipts: ₹___ | Total payments: ₹___

    SALES
    - Invoices raised today: [count] | Total value: ₹___
    - Sales Orders raised: [count] | Total value: ₹___
    - Outstanding receivables (aging flag if any >45 days): ₹___

    PURCHASE
    - Bills booked today: [count] | Total value: ₹___
    - Any vendor GSTIN/HSN mismatches: [Y/N — details if Y]

    EXPENSES & JOURNAL ENTRIES
    - Notable/unusual entries today: [list or "none"]

    GST/TAX WATCH ITEMS
    - [Any flagged ITC, RCM, or TDS items this week — only if new/unresolved]

    PENDING FROM YESTERDAY
    - [Carry-forward items still awaiting verification]

Guiding rule: LOSS-MINIMIZING and NEVER INVENT. When a value can't be
confidently mapped to a specific column (an amount, entity, priority, or
recommended action isn't clearly present), that field is left blank
rather than guessed, and the original text stays in Description/Notes.
Uncertain wording ("not reliably computable", "not derivable",
"Unconfirmed", "needs confirmation", "potential", "if any", "worth a
quick CA confirmation") is preserved, never turned into a confirmed
number or a false "Resolved"/"Open" status.
"""

import re
from datetime import date, datetime

TITLE_RE = re.compile(r"DAY\s+BOOK\s+SUMMARY", re.IGNORECASE)

# Same non-ASCII-only decoration tolerance as the Monitoring parser (emoji
# etc. immediately before a header) - see summary_parser.py for why this
# is restricted to non-ASCII so it never swallows real trailing content
# like a "..." ending the previous section.
_DECORATION = r"(?:[^\x00-\x7F\s]{1,4}\s+)?"

# Each section header is matched two ways: "anchored" (must start a
# physical line, allowing only whitespace/decoration before it) is tried
# first since it can't accidentally match the word appearing inside a
# sentence in an earlier section (this matters here more than in the
# Monitoring parser - "SALES" and "PURCHASE" are short, common words that
# could otherwise show up in prose). "loose" (matches anywhere) is the
# fallback, tried only if the anchored form finds nothing - this is what
# keeps the parser working when an MCP client has stripped every embedded
# newline from the pasted text (see summary_parser.py's docstring for the
# same collapsed-newline scenario).
def _anchored(pattern_body: str):
    return re.compile(r"(?:^|\n)[ \t]*" + _DECORATION + pattern_body, re.IGNORECASE)


def _loose(pattern_body: str):
    return re.compile(_DECORATION + pattern_body, re.IGNORECASE)


_ISSUES_BODY = r"ISSUES\s+REQUIRING\s+ATTENTION"
_CASH_BODY = r"CASH\s*(?:&|AND)\s*BANK\s+POSITION"
_SALES_BODY = r"SALES\b"
_PURCHASE_BODY = r"PURCHASE\b"
_EXPENSES_BODY = r"EXPENSES\s*(?:&|AND)\s*JOURNAL\s+ENTRIES"
_TAX_BODY = r"GST\s*/?\s*TAX\s+WATCH\s+ITEMS"
_PENDING_BODY = r"PENDING\s+FROM\s+YESTERDAY"

SECTION_DEFS = [
    ("issues", _anchored(_ISSUES_BODY), _loose(_ISSUES_BODY)),
    ("cash", _anchored(_CASH_BODY), _loose(_CASH_BODY)),
    ("sales", _anchored(_SALES_BODY), _loose(_SALES_BODY)),
    ("purchase", _anchored(_PURCHASE_BODY), _loose(_PURCHASE_BODY)),
    ("expenses", _anchored(_EXPENSES_BODY), _loose(_EXPENSES_BODY)),
    ("tax", _anchored(_TAX_BODY), _loose(_TAX_BODY)),
    ("pending", _anchored(_PENDING_BODY), _loose(_PENDING_BODY)),
]

NOTE_LINE_RE = re.compile(r"^\(.*\)$")
BULLET_PREFIX_RE = re.compile(r"^(?:\d+[.\):]\s+|[\-•\*]+\s*)")
_MID_ITEM_SPLIT_RE = re.compile(r"\s+(?=\d+\.\s|-\s+[A-Z0-9])")

DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y",
    "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
    "%d %B %Y", "%d %b %Y",
]

# Matches an amount with an explicit currency marker (₹, Rs, Rs., INR) -
# deliberately does NOT match a bare number, so a count (e.g. "5 bills")
# is never mistaken for an amount.
_AMOUNT_RE = re.compile(r"(?:₹|Rs\.?|INR)\s?[\d][\d,]*(?:\.\d+)?", re.IGNORECASE)


def _clean_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[​‌‍﻿]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _parse_date(raw: str) -> str:
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return date.today().isoformat()


def _strip_bullet(line: str) -> str:
    return BULLET_PREFIX_RE.sub("", line).strip()


def _block_lines(block: str) -> list:
    """Split a section's raw text into individual items - same algorithm
    as summary_parser.py's _block_lines (see there for full rationale):
    a line starting with a bullet/numbered marker starts a new item, a
    line that doesn't is a continuation of the previous item, and a
    single collapsed item is split back apart if it still contains
    multiple embedded markers glued together with spaces.
    """
    items = []
    for raw_line in block.split("\n"):
        line = raw_line.strip()
        if not line or NOTE_LINE_RE.match(line):
            continue
        if BULLET_PREFIX_RE.match(line) or not items:
            items.append(line)
        else:
            items[-1] = f"{items[-1]} {line}"

    if len(items) == 1:
        pieces = [p.strip() for p in _MID_ITEM_SPLIT_RE.split(items[0]) if p.strip()]
        if len(pieces) > 1:
            items = pieces

    return items


def _extract_amount(text: str) -> str:
    match = _AMOUNT_RE.search(text)
    if not match:
        return ""
    # [\d,]* can end on a trailing comma (e.g. "₹1,05,000," when a comma
    # in the source text immediately follows the figure) - trim it so the
    # stored amount is exactly the number, not a formatting artifact.
    return match.group(0).strip().rstrip(",")


# --- Priority / Status classification -----------------------------------
# Deliberately conservative: only an explicit signal in the text yields a
# value, matching "do not invent priorities" / "do not invent statuses".
_CRITICAL_RE = re.compile(r"\bcritical\b|\burgent\b|\bimmediate(?:ly)?\b", re.IGNORECASE)
_HIGH_RE = re.compile(r"\bhigh\s+priority\b", re.IGNORECASE)
_MEDIUM_RE = re.compile(r"\bmedium\s+priority\b", re.IGNORECASE)
_LOW_RE = re.compile(r"\blow\s+priority\b", re.IGNORECASE)


def _classify_priority(text: str) -> str:
    if _CRITICAL_RE.search(text):
        return "Critical"
    if _HIGH_RE.search(text):
        return "High"
    if _MEDIUM_RE.search(text):
        return "Medium"
    if _LOW_RE.search(text):
        return "Low"
    return ""


# Every uncertainty phrase explicitly called out in the spec, plus close
# variants. Checked before "resolved" so uncertainty always wins - e.g.
# "cannot confirm this is resolved" must never come out as Resolved.
_UNCONFIRMED_RE = re.compile(
    r"\bunconfirmed\b|\bnot\s+reliably\s+computable\b|\bnot\s+derivable\b|"
    r"\bneeds?\s+confirmation\b|\bcannot\s+confirm\b|\bcan.t\s+confirm\b|"
    r"\bpotential\b|\bif\s+any\b|\bworth\s+a\s+quick\s+ca\s+confirmation\b",
    re.IGNORECASE,
)
_RESOLVED_RE = re.compile(r"\bresolved\b|\bcleared\b|\bclosed\b|\bsettled\b", re.IGNORECASE)


def _classify_status(text: str, default: str = "") -> str:
    if _UNCONFIRMED_RE.search(text):
        return "Unconfirmed"
    if _RESOLVED_RE.search(text):
        return "Resolved"
    return default


# --- Entity / recommended-action extraction ------------------------------
def _looks_like_entity(text: str) -> bool:
    """Conservative check for "does this look like a company/vendor/
    customer name" - short, capitalized, no sentence punctuation. Used so
    Entity is only ever populated on a real signal, never guessed from an
    arbitrary word."""
    if not text or len(text) > 60:
        return False
    words = text.split()
    if not (1 <= len(words) <= 8):
        return False
    if not text[0].isupper():
        return False
    if any(ch in text for ch in ".!?"):
        return False
    return True


def _extract_entity(content: str) -> tuple:
    """Returns (entity, remaining_text). Only splits off an entity when
    the text before the separator passes _looks_like_entity - otherwise
    the whole content is returned as remaining text, untouched."""
    for sep in ("—", "–", ":"):
        if sep in content:
            head, _, rest = content.partition(sep)
            head = head.strip()
            if _looks_like_entity(head):
                return head, rest.strip()
            break
    hyphen_match = re.match(r"^(.+?)\s-\s(.*)$", content)
    if hyphen_match:
        head = hyphen_match.group(1).strip()
        if _looks_like_entity(head):
            return head, hyphen_match.group(2).strip()
    return "", content


_RECOMMEND_TRIGGER_RE = re.compile(
    r"\brecommend(?:ed|ation)?\b|\bsuggest(?:ed|ion)?\b|\badvis(?:e|ed|ory)\b|\bplease\b",
    re.IGNORECASE,
)
_CLAUSE_DELIM_RE = re.compile(r"[;,.]|—|–|\s-\s")


def _split_recommended_action(text: str) -> tuple:
    """Returns (description, recommended_action). If a recommendation
    trigger word is found, splits at the nearest preceding clause
    delimiter (so the recommendation is its own clean sentence/clause);
    with no delimiter, splits right at the trigger word. With no trigger
    at all, the whole text is the description and recommended_action is
    blank - never fabricated.
    """
    match = _RECOMMEND_TRIGGER_RE.search(text)
    if not match:
        return text.strip(), ""
    before = text[: match.start()]
    delims = list(_CLAUSE_DELIM_RE.finditer(before))
    if delims:
        cut = delims[-1].end()
        description = before[: delims[-1].start()].strip()
        action = text[cut:].strip()
    else:
        description = before.strip()
        action = text[match.start():].strip()
    return description, action


def _parse_generic_item(content: str) -> dict:
    """Shared extraction for one free-text bullet (used by Issues, Tax,
    Pending, and Expense items): optional leading Entity, a Description /
    Recommended Action split, an Amount if a currency-marked figure is
    present anywhere, and a best-effort Priority/Status. Nothing here is
    invented - each field is only populated on an actual signal in the
    text; otherwise the full original text is preserved in Description.
    """
    entity, remaining = _extract_entity(content)
    description, recommended_action = _split_recommended_action(remaining)
    amount = _extract_amount(content)
    priority = _classify_priority(content)
    status = _classify_status(content)
    return {
        "entity": entity,
        "description": description,
        "amount": amount,
        "priority": priority,
        "status": status,
        "recommended_action": recommended_action,
    }


# --- Label-boundary slicing (Cash / Sales / Purchase) --------------------
def _slice_by_labels(block: str, label_patterns: list) -> dict:
    """Find each label's position in `block`, sort by position, and slice
    the text between consecutive labels as that label's value - the same
    boundary-slicing technique summary_parser.py uses to locate sections
    within the whole document, applied here at the finer grain of
    "labeled fields within one section". This is what lets e.g. Sales'
    two separate "Total value:" occurrences (one for invoices, one for
    sales orders) each resolve to the correct field, and keeps working
    even if newlines between the labels were stripped upstream.

    Returns {key: (label_text, value_text)} for whichever labels matched.
    """
    matches = []
    for key, pattern in label_patterns:
        match = pattern.search(block)
        if match:
            matches.append((match.start(), match.end(), key, match.group(0)))
    matches.sort(key=lambda m: m[0])

    result = {}
    for i, (_, end, key, label_text) in enumerate(matches):
        next_start = matches[i + 1][0] if i + 1 < len(matches) else len(block)
        raw_value = re.sub(r"[\s\-•\*|]+$", "", block[end:next_start]).strip()
        result[key] = (label_text, raw_value)
    return result


_CASH_LABELS = [
    ("opening_balance", re.compile(r"opening\s+balance[^:]*:\s*", re.IGNORECASE)),
    ("closing_balance", re.compile(r"closing\s+balance[^:]*:\s*", re.IGNORECASE)),
    ("total_receipts", re.compile(r"total\s+receipts[^:]*:\s*", re.IGNORECASE)),
    ("total_payments", re.compile(r"total\s+payments[^:]*:\s*", re.IGNORECASE)),
]


def _parse_cash(block: str) -> tuple:
    """Returns ({opening_balance, closing_balance, total_receipts,
    total_payments}, notes_str). Each field is populated only when a
    currency-marked amount is found for it; when a label's value instead
    says something like "not reliably computable", that field is left
    blank and the raw text is preserved in the returned notes string -
    per the explicit "leave numeric fields blank, preserve in Notes"
    rule (this is intentionally different from how Monitoring keeps
    "Unconfirmed" text directly in a metric field).
    """
    sliced = _slice_by_labels(block, _CASH_LABELS)
    values = {"opening_balance": "", "closing_balance": "", "total_receipts": "", "total_payments": ""}
    notes_parts = []
    for key in values:
        if key not in sliced:
            continue
        label_text, raw_value = sliced[key]
        amount = _extract_amount(raw_value)
        if amount:
            values[key] = amount
        elif raw_value:
            notes_parts.append(f"{label_text.rstrip(': ').strip()}: {raw_value}")
    return values, " | ".join(notes_parts)


_SALES_LABELS = [
    ("invoices", re.compile(r"invoices?\s+raised[^:]*:\s*", re.IGNORECASE)),
    ("sales_orders", re.compile(r"sales\s+orders?\s+raised[^:]*:\s*", re.IGNORECASE)),
    ("receivables", re.compile(r"outstanding\s+receivables[^:]*:\s*", re.IGNORECASE)),
]


def _parse_sales(block: str) -> dict:
    """Returns {"invoices": {...} | None, "sales_orders": {...} | None,
    "receivables": {...} | None} - each present dict has whatever of
    count/value/amount/notes was found; a section entirely absent from
    the report stays None so build_accounting_rows never fabricates a
    row for something that wasn't mentioned at all.
    """
    sliced = _slice_by_labels(block, _SALES_LABELS)
    result = {"invoices": None, "sales_orders": None, "receivables": None}

    for key in ("invoices", "sales_orders"):
        if key not in sliced:
            continue
        _, raw_value = sliced[key]
        count_match = re.search(r"\d+", raw_value)
        count = int(count_match.group(0)) if count_match else ""
        amount = _extract_amount(raw_value)
        notes = raw_value if not count and not amount else ""
        result[key] = {"count": count, "value": amount, "notes": notes}

    if "receivables" in sliced:
        _, raw_value = sliced["receivables"]
        amount = _extract_amount(raw_value)
        # Aging context ("if any >45 days") is kept regardless of whether
        # an amount was also found, per "preserve aging information".
        result["receivables"] = {"amount": amount, "notes": raw_value}

    return result


_PURCHASE_LABELS = [
    ("bills", re.compile(r"bills?\s+booked[^:]*:\s*", re.IGNORECASE)),
    ("mismatch", re.compile(r"(?:any\s+)?(?:vendor\s+)?GSTIN\s*/?\s*HSN\s+mismatch(?:es)?[^:]*:\s*", re.IGNORECASE)),
]


def _parse_purchase(block: str) -> dict:
    """Returns {bills_count, bills_value, mismatch_text, notes}.
    mismatch_text is stored verbatim (e.g. a bare "N" stays "N" - never
    expanded into a fabricated detailed claim; "Y — <details>" stays
    exactly as given). If the bills line has neither a count nor an
    amount (e.g. "Unconfirmed, vendor portal was down most of the day"),
    that text is preserved in notes rather than silently dropped.
    """
    sliced = _slice_by_labels(block, _PURCHASE_LABELS)
    bills_count, bills_value = "", ""
    notes_parts = []
    if "bills" in sliced:
        label_text, raw_value = sliced["bills"]
        count_match = re.search(r"\d+", raw_value)
        bills_count = int(count_match.group(0)) if count_match else ""
        bills_value = _extract_amount(raw_value)
        if not bills_count and not bills_value and raw_value:
            notes_parts.append(f"{label_text.rstrip(': ').strip()}: {raw_value}")
    mismatch_text = sliced["mismatch"][1] if "mismatch" in sliced else ""
    return {
        "bills_count": bills_count,
        "bills_value": bills_value,
        "mismatch_text": mismatch_text,
        "notes": " | ".join(notes_parts),
    }


_NONE_WORDS = {"none", "none.", "n/a", "nil", "nil.", "-"}


def _parse_expenses(block: str) -> list:
    """Returns a list of raw item strings (one per notable/unusual entry).
    A bare "none" (with or without the "Notable/unusual entries today:"
    label still attached) produces an empty list - no fabricated row.
    Multiple bulleted entries become multiple items; a single grouped
    line stays one item, per "routine expenses can be grouped".
    """
    items = []
    for line in _block_lines(block):
        content = _strip_bullet(line)
        content = re.sub(r"^notable/?\s*unusual\s+entries[^:]*:\s*", "", content, flags=re.IGNORECASE).strip()
        if not content or content.lower() in _NONE_WORDS:
            continue
        items.append(content)
    return items


def parse_accounting_summary(summary: str) -> dict:
    """Parse a full raw Day Book (Accounting) summary into a structured
    dict. See build_accounting_rows() for how this becomes Accounting
    sheet rows.

    Returns:
        {
            "date": "YYYY-MM-DD",
            "issues": [{"entity","description","amount","priority","status","recommended_action"}, ...],
            "cash": {"opening_balance","closing_balance","total_receipts","total_payments"},
            "cash_notes": str,
            "sales": {"invoices": {...}|None, "sales_orders": {...}|None, "receivables": {...}|None},
            "purchase": {"bills_count","bills_value","mismatch_text"},
            "expenses": [same shape as "issues" items, ...],
            "tax": [same shape as "issues" items, ...],
            "pending": [same shape as "issues" items, ...],
        }
    """
    text = _clean_text(summary)

    header_matches = []
    for key, anchored_pattern, loose_pattern in SECTION_DEFS:
        match = anchored_pattern.search(text) or loose_pattern.search(text)
        if match:
            header_matches.append((match.start(), match.end(), key))
    header_matches.sort(key=lambda m: m[0])

    blocks = {}
    for i, (_, end, key) in enumerate(header_matches):
        next_start = header_matches[i + 1][0] if i + 1 < len(header_matches) else len(text)
        blocks[key] = text[end:next_start]

    # Date: same "up to next newline or next header" technique as
    # summary_parser.py - see there for why (avoids swallowing the rest
    # of the summary when there's no newline after the title).
    report_date_raw = ""
    title_match = TITLE_RE.search(text)
    if title_match:
        after_title = text[title_match.end():]
        pipe_index = after_title.find("|")
        if pipe_index != -1:
            date_start = title_match.end() + pipe_index + 1
            boundaries = [len(text)]
            newline_index = text.find("\n", date_start)
            if newline_index != -1:
                boundaries.append(newline_index)
            boundaries.extend(start for start, _, _ in header_matches if start >= date_start)
            date_end = min(boundaries)
            report_date_raw = text[date_start:date_end].strip()

    def _items(block_key):
        return [
            _parse_generic_item(_strip_bullet(line))
            for line in _block_lines(blocks.get(block_key, ""))
        ]

    cash_values, cash_notes = _parse_cash(blocks.get("cash", ""))

    return {
        "date": _parse_date(report_date_raw) if report_date_raw else date.today().isoformat(),
        "issues": _items("issues"),
        "cash": cash_values,
        "cash_notes": cash_notes,
        "sales": _parse_sales(blocks.get("sales", "")),
        "purchase": _parse_purchase(blocks.get("purchase", "")),
        "expenses": [_parse_generic_item(item) for item in _parse_expenses(blocks.get("expenses", ""))],
        "tax": _items("tax"),
        "pending": _items("pending"),
    }


# -----------------------------------------------------------------------
# Unified Accounting sheet schema
# -----------------------------------------------------------------------
ACCOUNTING_HEADERS = [
    "Date",
    "Record Type",
    "Entity",
    "Description",
    "Amount",
    "Count",
    "Priority",
    "Status",
    "Recommended Action",
    "Opening Balance",
    "Closing Balance",
    "Total Receipts",
    "Total Payments",
    "Sales Value",
    "Outstanding Receivables",
    "Purchase Value",
    "GSTIN/HSN Mismatch",
    "GST/Tax Watch",
    "Pending From Yesterday",
    "Notes",
]

_ACOL = {name: index for index, name in enumerate(ACCOUNTING_HEADERS)}


def _empty_accounting_row(date_value: str, record_type: str) -> list:
    row = [""] * len(ACCOUNTING_HEADERS)
    row[_ACOL["Date"]] = date_value
    row[_ACOL["Record Type"]] = record_type
    return row


def _apply_generic_item(row: list, item: dict) -> None:
    """Fills the columns every _parse_generic_item-derived row can use,
    regardless of Record Type - Entity/Amount/Priority/Recommended Action
    are general-purpose columns, so populating them whenever the parser
    actually extracted a value keeps the row lossless without needing a
    per-Record-Type allowlist of which columns "are allowed" to be used.
    """
    row[_ACOL["Entity"]] = item["entity"]
    row[_ACOL["Amount"]] = item["amount"]
    row[_ACOL["Priority"]] = item["priority"]
    row[_ACOL["Recommended Action"]] = item["recommended_action"]


def build_accounting_rows(parsed: dict) -> dict:
    """Convert parse_accounting_summary()'s structured dict into row lists
    for the unified Accounting sheet (column order: ACCOUNTING_HEADERS).

    Returns one list per source section (grouping is only so a caller can
    write/dedupe each independently - the sheet itself has no Section
    column, just the Record Type each row carries):
        {
            "issues": [row, ...],      # Record Type = Exception
            "cash": [row] | [],        # Record Type = Cash, at most 1
            "sales": [row, ...],       # Record Type = Sale, 0-3
            "purchase": [row] | [],    # Record Type = Purchase, at most 1
            "expenses": [row, ...],    # Record Type = Expense
            "tax": [row, ...],         # Record Type = Tax
            "pending": [row, ...],     # Record Type = Pending
        }

    A section produces no rows when the report has nothing for it - no
    fake exception/purchase/etc. row is ever fabricated just to have
    something to write.
    """
    date_value = parsed["date"]
    rows = {"issues": [], "cash": [], "sales": [], "purchase": [], "expenses": [], "tax": [], "pending": []}

    for item in parsed["issues"]:
        row = _empty_accounting_row(date_value, "Exception")
        _apply_generic_item(row, item)
        row[_ACOL["Description"]] = item["description"]
        row[_ACOL["Status"]] = item["status"] or "Open"
        rows["issues"].append(row)

    cash = parsed["cash"]
    cash_notes = parsed.get("cash_notes", "")
    if any(cash.values()) or cash_notes:
        row = _empty_accounting_row(date_value, "Cash")
        row[_ACOL["Opening Balance"]] = cash["opening_balance"]
        row[_ACOL["Closing Balance"]] = cash["closing_balance"]
        row[_ACOL["Total Receipts"]] = cash["total_receipts"]
        row[_ACOL["Total Payments"]] = cash["total_payments"]
        row[_ACOL["Notes"]] = cash_notes
        rows["cash"].append(row)

    sales = parsed["sales"]
    receivables = sales.get("receivables")
    receivables_attached = False
    for key in ("invoices", "sales_orders"):
        entry = sales.get(key)
        if not entry:
            continue
        row = _empty_accounting_row(date_value, "Sale")
        row[_ACOL["Count"]] = entry["count"]
        row[_ACOL["Sales Value"]] = entry["value"]
        notes_parts = [entry["notes"]] if entry["notes"] else []
        # Receivables/aging info attaches to the first Sale row written
        # (invoices takes priority over sales orders) rather than being
        # duplicated onto every Sale row.
        if receivables and not receivables_attached:
            row[_ACOL["Outstanding Receivables"]] = receivables["amount"]
            if receivables["notes"]:
                notes_parts.append(receivables["notes"])
            receivables_attached = True
        row[_ACOL["Notes"]] = " | ".join(notes_parts)
        rows["sales"].append(row)

    if receivables and not receivables_attached:
        row = _empty_accounting_row(date_value, "Sale")
        row[_ACOL["Outstanding Receivables"]] = receivables["amount"]
        row[_ACOL["Notes"]] = receivables["notes"]
        rows["sales"].append(row)

    purchase = parsed["purchase"]
    if purchase["bills_count"] != "" or purchase["bills_value"] or purchase["mismatch_text"] or purchase.get("notes"):
        row = _empty_accounting_row(date_value, "Purchase")
        row[_ACOL["Count"]] = purchase["bills_count"]
        row[_ACOL["Purchase Value"]] = purchase["bills_value"]
        row[_ACOL["GSTIN/HSN Mismatch"]] = purchase["mismatch_text"]
        row[_ACOL["Notes"]] = purchase.get("notes", "")
        rows["purchase"].append(row)

    for item in parsed["expenses"]:
        row = _empty_accounting_row(date_value, "Expense")
        _apply_generic_item(row, item)
        row[_ACOL["Description"]] = item["description"]
        rows["expenses"].append(row)

    for item in parsed["tax"]:
        row = _empty_accounting_row(date_value, "Tax")
        _apply_generic_item(row, item)
        row[_ACOL["Description"]] = item["description"]
        row[_ACOL["GST/Tax Watch"]] = item["description"]
        row[_ACOL["Status"]] = item["status"]
        rows["tax"].append(row)

    for item in parsed["pending"]:
        row = _empty_accounting_row(date_value, "Pending")
        _apply_generic_item(row, item)
        row[_ACOL["Description"]] = item["description"]
        row[_ACOL["Pending From Yesterday"]] = item["description"]
        row[_ACOL["Status"]] = item["status"] or "Open"
        rows["pending"].append(row)

    return rows
