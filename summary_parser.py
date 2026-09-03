"""Deterministic parser for the SUNTROP SOLAR Plant Monitoring Summary.

No LLM calls happen here on purpose (see project notes) - this is a plain
text parser tied to the fixed template used by the admin. If the template
changes shape, this file is where to update the parsing rules.

Expected template:

    SUNTROP SOLAR — PLANT MONITORING SUMMARY | [Date]

    ISSUES TODAY
    - New issues detected: [count]
    - Issues resolved today: [count]
    - Total issues currently open: [count]

    ⚠️ NEEDS ATTENTION
    - [Site] — [Issue] — open [X] days — [specific reason for delay]
    (If none: "No overdue or escalated issues today.")

    ACTIONS TAKEN TODAY
    - [Site] — [Issue] — [Action]

    WHAT'S NEEDED NEXT
    - [Site] — [required next action]

    SERVICE PATTERN WATCH
    - [Recurring issue/site/vendor pattern]
    (Only when relevant.)

Sections are located with regexes over the whole text (not line-by-line),
so a header is recognized wherever it appears - regardless of leading
emoji/punctuation, case, extra whitespace, or whether the line before it
ended with a real newline. That last point matters in practice: some MCP
clients render a plain string tool argument as a single-line input, which
silently strips embedded newlines from pasted multi-line text. A purely
line-based parser breaks completely in that case (the title-line check
below used to swallow the whole message); this one degrades gracefully
instead.
"""

import re
from datetime import date, datetime

TITLE_RE = re.compile(r"PLANT\s+MONITORING\s+SUMMARY", re.IGNORECASE)

# Each entry: (internal key, regex matching that section's heading anywhere
# in the text). Order doesn't matter - matches are sorted by position.
SECTION_PATTERNS = [
    ("issues_today", re.compile(r"ISSUES\s+TODAY", re.IGNORECASE)),
    ("needs_attention", re.compile(r"NEEDS\s+ATTENTION", re.IGNORECASE)),
    ("actions_taken", re.compile(r"ACTIONS\s+TAKEN\s+TODAY", re.IGNORECASE)),
    # ".?" between WHAT and S tolerates a straight/curly apostrophe or none.
    ("whats_needed_next", re.compile(r"WHAT.?S\s+NEEDED\s+NEXT", re.IGNORECASE)),
    ("service_pattern_watch", re.compile(r"SERVICE\s+PATTERN\s+WATCH", re.IGNORECASE)),
]

# Template notes like "(Only when relevant.)" - not real content.
NOTE_LINE_RE = re.compile(r"^\(.*\)$")

# Leading bullet marker: hyphen, bullet, or asterisk, plus any following space.
BULLET_PREFIX_RE = re.compile(r"^[\-•\*]+\s*")

# Fallback split point for a single physical line that turned out to hold
# multiple requirements glued together (collapsed-newline recovery) -
# only applied when a block resolved to exactly one line to begin with.
BULLET_RESPLIT_RE = re.compile(r"\s+-\s+(?=[A-Z0-9])")

# Common date formats an admin might paste after the "|" in the title line.
DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
]


def _clean_text(text: str) -> str:
    """Normalize characters that commonly sneak in from copy/paste."""
    text = text.replace("\xa0", " ")  # non-breaking space
    text = re.sub(r"[​‌‍﻿]", "", text)  # zero-width chars/BOM
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _parse_date(raw: str) -> str:
    """Best-effort parse of the title-line date into ISO format (YYYY-MM-DD).

    Falls back to today's date if the text doesn't match a known format,
    since every tab needs a usable Date value to key off of.
    """
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return date.today().isoformat()


def _split_dash(text: str) -> list:
    """Split a bullet's content on its field separator.

    Accepts an em dash ("—", the template's separator), an en dash ("–",
    a common autocorrect substitution for it), or a plain hyphen with
    surrounding spaces - whichever appears - without splitting on hyphens
    that are just part of a word (e.g. "20-day").
    """
    if "—" in text:
        return [part.strip() for part in text.split("—")]
    if "–" in text:
        return [part.strip() for part in text.split("–")]
    return [part.strip() for part in re.split(r"\s+-\s+", text)]


def _strip_bullet(line: str) -> str:
    return BULLET_PREFIX_RE.sub("", line).strip()


def _block_lines(block: str) -> list:
    """Split a section's raw text block into individual content lines,
    dropping blank lines and parenthetical template notes."""
    lines = []
    for raw_line in block.split("\n"):
        line = raw_line.strip()
        if not line or NOTE_LINE_RE.match(line):
            continue
        lines.append(line)
    return lines


def _parse_issues_today(block: str) -> dict:
    """Pull the three counts straight out of the block's raw text.

    Uses direct regex search over the whole block instead of per-bullet
    parsing, so it doesn't matter whether the three bullets ended up on
    separate lines or (if newlines were stripped upstream) glued together.
    """
    counts = {"new_issues": 0, "resolved_issues": 0, "total_open_issues": 0}

    new_match = re.search(r"new\s+issues[^0-9]*(\d+)", block, re.IGNORECASE)
    resolved_match = re.search(r"resolved[^0-9]*(\d+)", block, re.IGNORECASE)
    total_match = re.search(r"total[^0-9]*open[^0-9]*(\d+)", block, re.IGNORECASE)

    if new_match:
        counts["new_issues"] = int(new_match.group(1))
    if resolved_match:
        counts["resolved_issues"] = int(resolved_match.group(1))
    if total_match:
        counts["total_open_issues"] = int(total_match.group(1))

    return counts


def _parse_needs_attention(block: str) -> list:
    items = []
    for line in _block_lines(block):
        content = _strip_bullet(line)
        lower = content.lower()
        # The template's explicit "nothing to report" sentence - not a real row.
        if "no overdue" in lower or "no escalated" in lower:
            continue

        parts = _split_dash(content)
        if len(parts) < 3:
            # Doesn't match the expected "Site — Issue — open X days — reason"
            # shape; skip rather than fabricate a row from a stray line.
            continue

        site, issue, days_part = parts[0], parts[1], parts[2]
        days_match = re.search(r"(\d+)", days_part)
        days_open = int(days_match.group(1)) if days_match else ""
        reason = parts[3] if len(parts) > 3 else ""

        items.append({"site": site, "issue": issue, "days_open": days_open, "reason": reason})
    return items


def _parse_actions_taken(block: str) -> list:
    items = []
    for line in _block_lines(block):
        content = _strip_bullet(line)
        parts = _split_dash(content)
        if len(parts) < 3:
            continue
        items.append({"site": parts[0], "issue": parts[1], "action": parts[2]})
    return items


def _split_dash_once(text: str) -> list:
    """Split on the FIRST field separator only (em dash, en dash, or a
    spaced hyphen) - used for a bullet with exactly two logical fields
    (site, then free text) where the free text itself might legitimately
    contain further dashes that shouldn't be treated as more fields.
    """
    for sep in ("—", "–"):
        if sep in text:
            site, _, rest = text.partition(sep)
            return [site.strip(), rest.strip()]
    match = re.search(r"\s-\s", text)
    if match:
        return [text[: match.start()].strip(), text[match.end():].strip()]
    return [text.strip()]


def _parse_whats_needed_next(block: str) -> list:
    """Parse "- [Site] — [required next action]" bullets.

    If a bullet has no site separator at all (just free text), site is
    left blank rather than guessed - this also keeps older-style
    single-field requirement bullets working.
    """
    lines = _block_lines(block)
    raw_items = []
    for line in lines:
        content = _strip_bullet(line)
        if not content:
            continue
        # Recovery path: if the whole block came through as one physical
        # line (newlines stripped upstream) it may still hold multiple
        # requirements glued together. Only attempted when there's a
        # single line to begin with, so a normal, already-separated
        # requirement is never mis-split.
        if len(lines) == 1 and BULLET_RESPLIT_RE.search(content):
            raw_items.extend(part.strip() for part in BULLET_RESPLIT_RE.split(content) if part.strip())
        else:
            raw_items.append(content)

    items = []
    for content in raw_items:
        parts = _split_dash_once(content)
        if len(parts) == 2:
            site, requirement = parts
        else:
            site, requirement = "", parts[0]
        items.append({"site": site, "requirement": requirement})
    return items


def _parse_service_pattern_watch(block: str) -> list:
    """Parse Service Pattern Watch bullets.

    The template only guarantees free-text ("[Recurring issue/site/vendor
    pattern]"), so the full line always becomes the Pattern value. If the
    admin optionally writes it as "Pattern — Site — Vendor — Notes" the
    extra fields are captured too; otherwise Site/Vendor/Notes stay blank
    rather than guessing.
    """
    items = []
    for line in _block_lines(block):
        content = _strip_bullet(line)
        if not content:
            continue
        parts = _split_dash(content)
        pattern = parts[0] if parts else content
        site = parts[1] if len(parts) > 1 else ""
        vendor = parts[2] if len(parts) > 2 else ""
        notes = parts[3] if len(parts) > 3 else ""
        items.append({"pattern": pattern, "site": site, "vendor": vendor, "notes": notes})
    return items


def parse_monitoring_summary(summary: str) -> dict:
    """Parse a full raw monitoring summary into a structured dict.

    Returns:
        {
            "date": "YYYY-MM-DD",
            "new_issues": int,
            "resolved_issues": int,
            "total_open_issues": int,
            "needs_attention": [{"site", "issue", "days_open", "reason"}, ...],
            "actions_taken": [{"site", "issue", "action"}, ...],
            "whats_needed_next": [{"site", "requirement"}, ...],
            "service_pattern_watch": [{"pattern", "site", "vendor", "notes"}, ...],
        }

    Sections with no real data produce empty lists - callers should not
    write placeholder rows for those.
    """
    text = _clean_text(summary)

    # Find where each of the 5 sections starts (first occurrence), sorted
    # by position - this is what makes the parser resilient to headers
    # not being cleanly alone on their own line.
    header_matches = []
    for key, pattern in SECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            header_matches.append((match.start(), match.end(), key))
    header_matches.sort(key=lambda m: m[0])

    # Slice the text between consecutive headers into per-section blocks.
    blocks = {}
    for i, (_, end, key) in enumerate(header_matches):
        next_start = header_matches[i + 1][0] if i + 1 < len(header_matches) else len(text)
        blocks[key] = text[end:next_start]

    # Date: whatever follows "|" after the title marker, up to the next
    # newline or the first section header - whichever comes first. This
    # avoids swallowing the rest of the summary if there's no newline
    # after the title (e.g. embedded newlines were stripped upstream).
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

    issue_counts = _parse_issues_today(blocks.get("issues_today", ""))

    return {
        "date": _parse_date(report_date_raw) if report_date_raw else date.today().isoformat(),
        "new_issues": issue_counts["new_issues"],
        "resolved_issues": issue_counts["resolved_issues"],
        "total_open_issues": issue_counts["total_open_issues"],
        "needs_attention": _parse_needs_attention(blocks.get("needs_attention", "")),
        "actions_taken": _parse_actions_taken(blocks.get("actions_taken", "")),
        "whats_needed_next": _parse_whats_needed_next(blocks.get("whats_needed_next", "")),
        "service_pattern_watch": _parse_service_pattern_watch(blocks.get("service_pattern_watch", "")),
    }


# -----------------------------------------------------------------------
# Unified Monitoring sheet schema
# -----------------------------------------------------------------------
# All five sections now write into ONE "Monitoring" tab (rather than one
# tab per section), distinguished by the Section column. This is a
# separate transformation step on top of parse_monitoring_summary()'s
# output above - the section-detection/tolerance logic itself is
# untouched, this just reshapes the already-parsed data into flat rows.
MONITORING_HEADERS = [
    "Date",
    "Section",
    "Site",
    "Description",
    "Days Open",
    "Action Taken",
    "What's Needed Next",
    "New Issues",
    "Issues Resolved",
    "Total Open Issues",
    "Vendor",
    "Notes",
]

# Column name -> position, so row-building reads as named slots instead
# of magic indexes.
_COL = {name: index for index, name in enumerate(MONITORING_HEADERS)}


def _empty_row(date_value: str, section: str) -> list:
    row = [""] * len(MONITORING_HEADERS)
    row[_COL["Date"]] = date_value
    row[_COL["Section"]] = section
    return row


def build_monitoring_rows(parsed: dict) -> dict:
    """Convert parse_monitoring_summary()'s structured dict into row lists
    for the unified Monitoring sheet (column order: MONITORING_HEADERS).

    Returns one list per section:
        {
            "daily_summary": [row],                 # always exactly 1
            "needs_attention": [row, ...],
            "actions_taken": [row, ...],
            "whats_needed_next": [row, ...],
            "service_pattern_watch": [row, ...],
        }
    kept separate (rather than one flat list) so a caller can write/dedupe
    each section independently while they all share one tab and one
    column layout.

    Fields a section's bullet format doesn't supply (e.g. Vendor for a
    Needs Attention row - the template only gives one combined "reason"
    field there, not a distinct vendor) are left blank rather than
    guessed, per the "don't invent values" rule.
    """
    date_value = parsed["date"]

    daily_row = _empty_row(date_value, "Daily Summary")
    daily_row[_COL["New Issues"]] = parsed["new_issues"]
    daily_row[_COL["Issues Resolved"]] = parsed["resolved_issues"]
    daily_row[_COL["Total Open Issues"]] = parsed["total_open_issues"]

    needs_attention_rows = []
    for item in parsed["needs_attention"]:
        row = _empty_row(date_value, "Needs Attention")
        row[_COL["Site"]] = item["site"]
        row[_COL["Description"]] = item["issue"]
        row[_COL["Days Open"]] = item["days_open"]
        row[_COL["Notes"]] = item["reason"]
        needs_attention_rows.append(row)

    actions_taken_rows = []
    for item in parsed["actions_taken"]:
        row = _empty_row(date_value, "Actions Taken")
        row[_COL["Site"]] = item["site"]
        row[_COL["Description"]] = item["issue"]
        row[_COL["Action Taken"]] = item["action"]
        actions_taken_rows.append(row)

    whats_needed_next_rows = []
    for item in parsed["whats_needed_next"]:
        row = _empty_row(date_value, "What's Needed Next")
        row[_COL["Site"]] = item["site"]
        row[_COL["What's Needed Next"]] = item["requirement"]
        whats_needed_next_rows.append(row)

    service_pattern_watch_rows = []
    for item in parsed["service_pattern_watch"]:
        row = _empty_row(date_value, "Service Pattern Watch")
        row[_COL["Site"]] = item["site"]
        row[_COL["Description"]] = item["pattern"]
        row[_COL["Vendor"]] = item["vendor"]
        row[_COL["Notes"]] = item["notes"]
        service_pattern_watch_rows.append(row)

    return {
        "daily_summary": [daily_row],
        "needs_attention": needs_attention_rows,
        "actions_taken": actions_taken_rows,
        "whats_needed_next": whats_needed_next_rows,
        "service_pattern_watch": service_pattern_watch_rows,
    }
