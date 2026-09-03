"""Deterministic, loss-minimizing parser for the SUNTROP SOLAR Plant
Monitoring Summary.

No LLM calls happen here on purpose (see project notes) - this is a plain
text parser. It supports two shapes of input:

  A. The clean template:

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

  B. Real-world messy reports where values aren't clean/simple: metrics
     can be "Unconfirmed" instead of a number, a single bullet can cover
     multiple sites (comma-separated, sometimes with per-site values in
     the same order), What's Needed Next can be a numbered list, and
     free text can run for a sentence or two with qualifiers ("tomorrow",
     "not yet confirmed", "not appearing in today's report") that matter.

Guiding rule for (B): LOSS-MINIMIZING. When a value can't be confidently
mapped to a specific column, the original text is preserved in Description
/ Pattern/Notes / the metric field itself (as "Unconfirmed - ...") rather
than being dropped or forced to a default like 0.

Sections are located with regexes over the whole text (not line-by-line),
so a header is recognized wherever it appears - regardless of leading
emoji/punctuation, case, extra whitespace, or whether the line before it
ended with a real newline. That last point matters in practice: some MCP
clients render a plain string tool argument as a single-line input, which
silently strips embedded newlines from pasted multi-line text. A purely
line-based parser breaks completely in that case; this one degrades
gracefully instead (see _block_lines' collapsed-newline recovery).
"""

import re
from datetime import date, datetime

TITLE_RE = re.compile(r"PLANT\s+MONITORING\s+SUMMARY", re.IGNORECASE)

# Optional decoration (emoji/symbols, e.g. "⚠️") immediately before a
# header, plus the whitespace after it. Included as part of each header
# match (not just skipped) so it counts as belonging to THIS header, not
# as trailing content of the PREVIOUS section's block - otherwise e.g.
# "⚠️" from "⚠️ NEEDS ATTENTION" would leak into the end of the ISSUES
# TODAY block and get mistaken for real content there.
#
# Restricted to non-ASCII characters only (\x00-\x7F is the ASCII range)
# so this never swallows legitimate trailing ASCII punctuation from the
# previous section's real content - e.g. a sentence ending in "..." right
# before the next header must NOT be treated as decoration of that header;
# only genuine symbol/emoji decoration (which is virtually always outside
# the ASCII range) is matched here.
_HEADER_DECORATION = r"(?:[^\x00-\x7F\s]{1,4}\s+)?"

# Each entry: (internal key, regex matching that section's heading anywhere
# in the text). Order doesn't matter - matches are sorted by position.
SECTION_PATTERNS = [
    ("issues_today", re.compile(_HEADER_DECORATION + r"ISSUES\s+TODAY", re.IGNORECASE)),
    ("needs_attention", re.compile(_HEADER_DECORATION + r"NEEDS\s+ATTENTION", re.IGNORECASE)),
    ("actions_taken", re.compile(_HEADER_DECORATION + r"ACTIONS\s+TAKEN\s+TODAY", re.IGNORECASE)),
    # ".?" between WHAT and S tolerates a straight/curly apostrophe or none.
    ("whats_needed_next", re.compile(_HEADER_DECORATION + r"WHAT.?S\s+NEEDED\s+NEXT", re.IGNORECASE)),
    ("service_pattern_watch", re.compile(_HEADER_DECORATION + r"SERVICE\s+PATTERN\s+WATCH", re.IGNORECASE)),
]

# Template notes like "(Only when relevant.)" - not real content.
NOTE_LINE_RE = re.compile(r"^\(.*\)$")

# Leading item marker: "-"/"•"/"*" bullets, or a numbered-list marker
# ("1.", "2)", "3:"). Used both to detect where a new item starts and to
# strip the marker off before extracting the item's content.
BULLET_PREFIX_RE = re.compile(r"^(?:\d+[.\):]\s+|[\-•\*]+\s*)")

# Collapsed-newline recovery: if a section resolved to a single physical
# line/item that still holds multiple items glued together with spaces
# instead of newlines, split right before each embedded marker. Only
# plain "-" is treated as an item boundary here (not em/en dash - those
# are reserved as the *internal* field separator within one item, e.g.
# "Site — Issue — Action", so treating them as boundaries would wrongly
# cut a single item in half). Only "N." (not "N)" or "N:") is treated as
# a numbered-list boundary here, unlike BULLET_PREFIX_RE below - a
# closing paren or colon after a number is far too common as ordinary
# mid-sentence content (e.g. "...072) logged..." is a site code followed
# by a closing paren, not a "72)"-style list marker) to safely treat as
# a boundary when it's not anchored at a real line start.
_MID_ITEM_SPLIT_RE = re.compile(r"\s+(?=\d+\.\s|-\s+[A-Z0-9])")

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

# A site reference in the messy report format: a 2-5 digit code, e.g.
# "079" in "079 Oaza Global Krishnagiri". Used only to decide whether a
# comma-separated list is a list of distinct sites (see _split_sites).
SITE_CODE_RE = re.compile(r"^\d{2,5}\b")


def _clean_text(text: str) -> str:
    """Normalize characters that commonly sneak in from copy/paste."""
    text = text.replace("\xa0", " ")  # non-breaking space
    text = re.sub(r"[​‌‍﻿]", "", text)  # zero-width chars/BOM
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _parse_date(raw: str) -> str:
    """Best-effort parse of the title-line date into ISO format (YYYY-MM-DD).

    Falls back to today's date if the text doesn't match a known format
    (or there's no title line at all), since every row needs a usable
    Date value.
    """
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return date.today().isoformat()


def _split_dash(text: str) -> list:
    """Split an item's content on its field separator.

    Accepts an em dash ("—", the template's separator), an en dash ("–",
    a common autocorrect substitution for it), or a plain hyphen with
    surrounding spaces - whichever appears - without splitting on hyphens
    that are just part of a word (e.g. "20-day"). Returns [text] unchanged
    if none of those appear.
    """
    if "—" in text:
        return [part.strip() for part in text.split("—")]
    if "–" in text:
        return [part.strip() for part in text.split("–")]
    return [part.strip() for part in re.split(r"\s+-\s+", text)]


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


def _partition_first_dash(text: str) -> tuple:
    """Split on the FIRST dash of any kind (em/en/spaced-hyphen) only.

    Returns (head, separator_found, rest). Used to separate a leading
    site-reference from the rest of an item without assuming anything
    about how many more fields follow.
    """
    for sep in ("—", "–"):
        if sep in text:
            head, _, rest = text.partition(sep)
            return head.strip(), True, rest.strip()
    match = re.search(r"\s-\s", text)
    if match:
        return text[: match.start()].strip(), True, text[match.end():].strip()
    return text.strip(), False, ""


def _split_sites(head: str) -> list:
    """Identify one or more site names from the text before an item's
    first dash separator.

    A single segment (no comma) is always treated as one site name,
    whatever it looks like - this matches the simple template (e.g.
    "Bengaluru", "Kolar Site 1"). Multiple comma-separated segments are
    only split into separate sites when EVERY segment looks like a
    distinct site reference (starts with a numeric site code, as used in
    the real-world report format, e.g. "034 IIM Bangalore-2023") -
    otherwise a comma might just be part of one site's name or of the
    description, so the whole head is kept as a single (unsplit) site
    name rather than guessing.
    """
    head = head.strip()
    if not head:
        return []
    segments = [s.strip() for s in head.split(",") if s.strip()]
    if len(segments) > 1 and all(SITE_CODE_RE.match(seg) for seg in segments):
        return segments
    return [head]


_TRAILING_PAREN_LIST_RE = re.compile(r"\(([^()]*)\)[.\s]*$")
_TRAILING_WORD_RE = re.compile(r"\s*\b(?:respectively|resp\.?)\b\.?\s*$", re.IGNORECASE)


def _extract_per_site_values(text: str, site_count: int) -> tuple:
    """If `text` ends with a parenthetical comma/semicolon-separated list
    whose item count matches `site_count` (e.g. "...tripping today (10x/
    350min, 14x/135min, 7x/205min respectively)"), return
    (base_text_without_the_list, [value_for_site_0, value_for_site_1, ...]).

    Otherwise returns (text, None) unchanged - callers should fall back
    to giving every site the same, undivided text rather than guessing.
    """
    if site_count < 2:
        return text.strip(), None
    match = _TRAILING_PAREN_LIST_RE.search(text)
    if not match:
        return text.strip(), None
    inner = match.group(1)
    parts = [p.strip() for p in re.split(r"[,;]", inner) if p.strip()]
    if parts:
        parts[-1] = _TRAILING_WORD_RE.sub("", parts[-1]).strip()
        parts = [p for p in parts if p]
    if len(parts) != site_count:
        return text.strip(), None
    base = text[: match.start()].strip().rstrip(".,")
    return base, parts


def _extract_days_open(text: str):
    """Only ever returns a value when an explicit "X day(s)" count is
    present - never inferred from an unrelated number (outage minutes,
    percentages, site counts, etc.)."""
    match = re.search(r"\bopen\s+(\d+)\s+days?\b", text, re.IGNORECASE)
    if not match:
        match = re.search(r"\b(\d+)\s+days?\b", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return ""


def _strip_bullet(line: str) -> str:
    return BULLET_PREFIX_RE.sub("", line).strip()


def _block_lines(block: str) -> list:
    """Split a section's raw text block into individual items.

    A new item starts at each physical line beginning with a bullet
    marker ("-", "•", "*") or a numbered-list marker ("1.", "2)", etc.).
    A line that doesn't start with either is treated as a continuation of
    the previous item (an explanatory sentence/paragraph wrapped onto the
    next physical line) rather than a new, separate item - unless there's
    no previous item yet, in which case it starts one anyway so nothing
    is lost. Blank lines and parenthetical template notes ("(Only when
    relevant.)") are dropped.

    Collapsed-newline recovery: if everything above still collapsed into
    exactly one item (e.g. an MCP client stripped every embedded newline
    from a multi-line paste), and that one item still contains multiple
    markers glued together with spaces, it's split back apart. Only
    attempted when there's exactly one item, so normal, already
    newline-separated input is never touched.
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


# ISSUES TODAY: matched by label text up to (but not including) its
# trailing colon, so the boundary-slicing approach below can capture
# everything between one label and the next - including free text like
# "Unconfirmed - can't distinguish..." - rather than requiring a bare
# number right after the colon.
_ISSUES_LABEL_PATTERNS = [
    ("new_issues", re.compile(r"new\s+issues[^:]*:\s*", re.IGNORECASE)),
    ("resolved_issues", re.compile(r"(?:issues\s+)?resolved[^:]*:\s*", re.IGNORECASE)),
    ("total_open_issues", re.compile(r"total[^:]*open[^:]*:\s*", re.IGNORECASE)),
]


def _parse_issues_today(block: str) -> dict:
    """Extract the three ISSUES TODAY metrics from the block's raw text.

    Uses direct regex search + boundary-slicing over the whole block
    (mirroring how sections themselves are located) instead of per-line
    parsing, so it doesn't matter whether the three items ended up on
    separate lines or (if newlines were stripped upstream) glued
    together.

    Each metric is parsed as: a leading number (kept as an int - any
    trailing context after it is preserved in "notes" rather than
    discarded), OR, if there's no leading number at all (e.g.
    "Unconfirmed - can't distinguish new vs. recurring without tracker"),
    the ENTIRE value text is kept as-is rather than being forced to 0.
    """
    values = {"new_issues": "", "resolved_issues": "", "total_open_issues": ""}
    notes_parts = []

    label_matches = []
    for key, pattern in _ISSUES_LABEL_PATTERNS:
        match = pattern.search(block)
        if match:
            label_matches.append((match.start(), match.end(), key, match.group(0)))
    label_matches.sort(key=lambda m: m[0])

    if not label_matches:
        leftover = block.strip()
        if leftover:
            notes_parts.append(leftover)
        return {**values, "notes": " | ".join(notes_parts)}

    # Preserve any text before the first recognized label too, rather
    # than silently dropping it (e.g. a stray line that isn't one of the
    # three known metrics).
    leading = re.sub(r"[\s\-•\*]+$", "", block[: label_matches[0][0]]).strip()
    leading = _strip_bullet(leading)
    if leading and not NOTE_LINE_RE.match(leading):
        notes_parts.append(leading)

    for i, (_, end, key, label_text) in enumerate(label_matches):
        next_start = label_matches[i + 1][0] if i + 1 < len(label_matches) else len(block)
        raw_value = re.sub(r"[\s\-•\*]+$", "", block[end:next_start]).strip()

        number_match = re.match(r"^(\d+)\b\s*(.*)$", raw_value)
        if number_match:
            values[key] = int(number_match.group(1))
            extra = number_match.group(2).strip(" -—–,.")
            if extra:
                notes_parts.append(f"{label_text.rstrip(': ').strip()}: {extra}")
        elif raw_value:
            # No leading number (e.g. "Unconfirmed - ...") - preserve the
            # whole value rather than forcing it to 0.
            values[key] = raw_value

    return {**values, "notes": " | ".join(notes_parts)}


def _parse_needs_attention_item(rest: str) -> dict:
    """Given the text after a single site's dash, extract an explicit day
    count (if any) and a description, preferring the original clean
    "Issue — open X days — Reason" shape when it cleanly applies (pulled
    out as its own dash-separated part), and otherwise keeping the whole
    text together - with the day count still pulled out via regex,
    wherever it appears - rather than guessing at a split that might cut
    a sentence in a nonsensical place.
    """
    parts = _split_dash(rest)
    days_re = re.compile(r"^open\s+(\d+)\s+days?$", re.IGNORECASE)

    for i, part in enumerate(parts):
        match = days_re.match(part.strip())
        if match:
            days_open = int(match.group(1))
            remaining = [p for j, p in enumerate(parts) if j != i and p]
            return {"description": " — ".join(remaining), "days_open": days_open}

    return {"description": rest, "days_open": _extract_days_open(rest)}


def _parse_needs_attention(block: str) -> list:
    """Parse NEEDS ATTENTION items.

    Does NOT require the strict "Site — Issue — Days Open — Reason"
    shape - only a leading site (or comma-separated list of sites) is
    required; everything else is kept as free text in Description rather
    than dropped when it doesn't cleanly decompose further. A bullet
    naming several sites is split into one row per site: if the text ends
    with a parenthetical list of per-site values matching the site count,
    they're mapped by position; otherwise every site gets the same shared
    description (nothing invented).
    """
    items = []
    for line in _block_lines(block):
        content = _strip_bullet(line)
        lower = content.lower()
        # The template's explicit "nothing to report" sentence - not a real row.
        if "no overdue" in lower or "no escalated" in lower:
            continue

        head, has_sep, rest = _partition_first_dash(content)
        sites = _split_sites(head) if has_sep else []

        if not sites:
            # No confidently-identifiable site - preserve the whole item
            # rather than dropping it.
            items.append({"site": "", "description": content, "days_open": ""})
            continue

        if len(sites) > 1:
            base_description, per_site_values = _extract_per_site_values(rest, len(sites))
            days_open = _extract_days_open(rest)
            for i, site in enumerate(sites):
                if per_site_values:
                    value = per_site_values[i]
                    description = f"{base_description} ({value})" if base_description else value
                else:
                    description = rest
                items.append({"site": site, "description": description, "days_open": days_open})
        else:
            extracted = _parse_needs_attention_item(rest)
            items.append({"site": sites[0], **extracted})
    return items


def _parse_actions_taken(block: str) -> list:
    """Parse ACTIONS TAKEN items.

    Does not require a clean "Site — Issue — Action" 3-way split: if the
    text after the site has a further separator it's treated as
    Description + Action Taken (matching the simple template); otherwise
    the whole thing is kept as Action Taken (still under the site), which
    naturally preserves qualifiers like "tomorrow" or "not yet confirmed"
    instead of forcing them into a field they don't fit. Multi-site
    bullets (comma-separated site codes) become one row per site, each
    carrying the same text.
    """
    items = []
    for line in _block_lines(block):
        content = _strip_bullet(line)
        if not content:
            continue

        head, has_sep, rest = _partition_first_dash(content)
        if not has_sep:
            items.append({"site": "", "description": "", "action": content})
            continue

        sites = _split_sites(head)
        if not sites:
            items.append({"site": "", "description": "", "action": content})
            continue

        parts = _split_dash(rest)
        if len(parts) >= 2:
            description = parts[0]
            action = " — ".join(parts[1:])
        else:
            description = ""
            action = rest

        for site in sites:
            items.append({"site": site, "description": description, "action": action})
    return items


def _parse_whats_needed_next(block: str) -> list:
    """Parse "- [Site] — [required next action]" bullets, OR numbered
    items ("1.", "2.", ...) which may just be free text with no site at
    all - each becomes its own row (Section = "What's Needed Next").

    If an item has no site separator (just free text, however long),
    site is left blank rather than guessed - this also keeps older-style
    single-field requirement bullets working.
    """
    items = []
    for content in _block_lines(block):
        stripped = _strip_bullet(content)
        if not stripped:
            continue
        parts = _split_dash_once(stripped)
        if len(parts) == 2:
            site, requirement = parts
        else:
            site, requirement = "", parts[0]
        items.append({"site": site, "requirement": requirement})
    return items


def _parse_service_pattern_watch(block: str) -> list:
    """Parse Service Pattern Watch items.

    The template only guarantees free-text ("[Recurring issue/site/vendor
    pattern]"), so the full text always becomes the Pattern value - this
    already means a Site/Vendor aren't required (they're simply left
    blank when the item has no further dash-separated fields). If the
    admin optionally writes it as "Pattern — Site — Vendor — Notes" the
    extra fields are captured too.
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
            "new_issues": int | str,       # str only for unparseable values
            "resolved_issues": int | str,  # e.g. "Unconfirmed - ..."
            "total_open_issues": int | str,
            "issues_today_notes": str,     # extra context that didn't fit
                                            # into the three fields above
            "needs_attention": [{"site", "description", "days_open"}, ...],
            "actions_taken": [{"site", "description", "action"}, ...],
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
    # If there's no title line at all, falls back to today's date.
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

    issues_today = _parse_issues_today(blocks.get("issues_today", ""))

    return {
        "date": _parse_date(report_date_raw) if report_date_raw else date.today().isoformat(),
        "new_issues": issues_today["new_issues"],
        "resolved_issues": issues_today["resolved_issues"],
        "total_open_issues": issues_today["total_open_issues"],
        "issues_today_notes": issues_today["notes"],
        "needs_attention": _parse_needs_attention(blocks.get("needs_attention", "")),
        "actions_taken": _parse_actions_taken(blocks.get("actions_taken", "")),
        "whats_needed_next": _parse_whats_needed_next(blocks.get("whats_needed_next", "")),
        "service_pattern_watch": _parse_service_pattern_watch(blocks.get("service_pattern_watch", "")),
    }


# -----------------------------------------------------------------------
# Unified Monitoring sheet schema
# -----------------------------------------------------------------------
# All five sections write into ONE "Monitoring" tab (rather than one tab
# per section), distinguished by the Section column. This is a separate
# transformation step on top of parse_monitoring_summary()'s output above
# - the section-detection/tolerance logic itself is untouched by it, this
# just reshapes the already-parsed data into flat rows.
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
    "Pattern/Notes",
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

    Fields a section's bullet doesn't cleanly supply are left blank
    rather than guessed; any leftover context that doesn't map to a
    dedicated column lands in Description or Pattern/Notes instead of
    being discarded (see parse_monitoring_summary's "issues_today_notes"
    and each section's own free-text handling).
    """
    date_value = parsed["date"]

    daily_row = _empty_row(date_value, "Daily Summary")
    daily_row[_COL["New Issues"]] = parsed["new_issues"]
    daily_row[_COL["Issues Resolved"]] = parsed["resolved_issues"]
    daily_row[_COL["Total Open Issues"]] = parsed["total_open_issues"]
    daily_row[_COL["Pattern/Notes"]] = parsed.get("issues_today_notes", "")

    needs_attention_rows = []
    for item in parsed["needs_attention"]:
        row = _empty_row(date_value, "Needs Attention")
        row[_COL["Site"]] = item["site"]
        row[_COL["Description"]] = item["description"]
        row[_COL["Days Open"]] = item["days_open"]
        needs_attention_rows.append(row)

    actions_taken_rows = []
    for item in parsed["actions_taken"]:
        row = _empty_row(date_value, "Actions Taken")
        row[_COL["Site"]] = item["site"]
        row[_COL["Description"]] = item["description"]
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
        row[_COL["Pattern/Notes"]] = item["notes"]
        service_pattern_watch_rows.append(row)

    return {
        "daily_summary": [daily_row],
        "needs_attention": needs_attention_rows,
        "actions_taken": actions_taken_rows,
        "whats_needed_next": whats_needed_next_rows,
        "service_pattern_watch": service_pattern_watch_rows,
    }
