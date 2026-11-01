"""Reading a usage file a person exported: CSV text, its numbers and its times.

Exports come from report tools in the exporter's own locale, so a number like ``1,234`` or a date
like ``03/04/2026`` can be read two ways. Nothing here guesses: the format is worked out from the
whole column, and when the file itself can't settle it the caller is told to choose.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from typing import Any

MAX_BYTES = 64 * 1024 * 1024
MAX_ROWS = 250_000


class FileProblem(ValueError):
    """The file can't be read as it is; the message says what to do."""


class NeedsChoice(FileProblem):
    """The file reads two ways; the person picks ``option`` from ``choices`` ({value: label})."""

    def __init__(self, message: str, option: str, choices: dict[str, str]):
        super().__init__(message)
        self.option = option
        self.choices = choices


def table(text: str) -> tuple[list[str], list[list[str]]]:
    """Headers and rows of a CSV export. Comma, semicolon and tab separated files are read."""
    if len(text.encode("utf-8", errors="ignore")) > MAX_BYTES:
        raise FileProblem(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB. Export a shorter period.")
    text = text.lstrip("﻿")
    if not text.strip():
        raise FileProblem("The file is empty.")
    if text.startswith("PK\x03\x04"):
        raise FileProblem("This looks like an Excel workbook. Export the data as CSV instead.")
    first = text.split("\n", 1)[0]
    delimiter = max((",", ";", "\t"), key=first.count)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        rows = [r for r in reader if any(c.strip() for c in r)]
    except csv.Error as exc:
        raise FileProblem(f"The file isn't valid CSV: {exc}") from exc
    if not rows:
        raise FileProblem("The file has no rows.")
    headers = [h.strip() for h in rows[0]]
    if len(rows) - 1 > MAX_ROWS:
        raise FileProblem(f"The file has more than {MAX_ROWS:,} rows. Export a shorter period.")
    return headers, rows[1:]


def normal(name: str) -> str:
    """A column name compared without case, spaces or punctuation: "Total CU (s)" is "totalcus"."""
    return re.sub(r"[^a-z0-9%]", "", name.lower())


# ---- numbers

_NUM = re.compile(r"^[+-]?[\d.,\s  ']+$")


def decimal_mark(values: list[str]) -> str | None:
    """"." or "," when the column settles which is the decimal mark, None when nothing in it needs
    one, and FileProblem when it can't be told (only values like 1,234)."""
    seen = {"dot": False, "comma": False}
    ambiguous = False
    for raw in values:
        v = raw.strip().replace(" ", "").replace(" ", "").replace(" ", "").replace("'", "")
        if not v or not _NUM.match(v):
            continue
        has_dot, has_comma = "." in v, "," in v
        if has_dot and has_comma:
            seen["dot" if v.rfind(".") > v.rfind(",") else "comma"] = True
        elif has_comma:
            tail = v.rsplit(",", 1)[1]
            if v.count(",") > 1 or len(tail) == 3:
                ambiguous = ambiguous or v.count(",") == 1
                if v.count(",") > 1:
                    seen["dot"] = True  # 1,234,567: commas group thousands
            else:
                seen["comma"] = True
        elif has_dot:
            tail = v.rsplit(".", 1)[1]
            if v.count(".") > 1:
                seen["comma"] = True  # 1.234.567: dots group thousands
            elif len(tail) != 3:
                seen["dot"] = True
            else:
                ambiguous = True
    if seen["dot"] and seen["comma"]:
        raise FileProblem("The CU column mixes number formats (both 1,5 and 1.5 styles). Export it again from one place.")
    if seen["dot"]:
        return "."
    if seen["comma"]:
        return ","
    if ambiguous:
        raise NeedsChoice("The numbers can be read two ways (is 1,234 one thousand or one point two three four?). "
                          "Choose the decimal mark the export uses.", "decimal",
                          {".": "Point: 1,234.5", ",": "Comma: 1.234,5"})
    return None


def number(raw: str, mark: str | None) -> float | None:
    """A number written with ``mark`` as its decimal mark; None when the cell is empty or not a number."""
    v = (raw or "").strip().replace(" ", "").replace(" ", "").replace(" ", "").replace("'", "")
    if not v or not _NUM.match(v):
        return None
    if mark == ",":
        v = v.replace(".", "").replace(",", ".")
    else:
        v = v.replace(",", "")
    try:
        return float(v)
    except ValueError:
        return None


# ---- times

_SLASH = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?)?\s*$")


def date_order(values: list[str]) -> str | None:
    """"mdy" or "dmy" when the column's day-first or month-first dates settle it, None when it has
    none, and FileProblem when every date reads both ways (such as 03/04/2026)."""
    mdy = dmy = False
    slashed = False
    for raw in values:
        m = _SLASH.match(raw or "")
        if not m:
            continue
        slashed = True
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12:
            dmy = True
        elif b > 12:
            mdy = True
    if mdy and dmy:
        raise FileProblem("The dates mix day-first and month-first order. Export the file again from one place.")
    if mdy:
        return "mdy"
    if dmy:
        return "dmy"
    if slashed:
        raise NeedsChoice("The dates can be read two ways (is 03/04 the 3rd of April or March 4th?). "
                          "Choose the date order the export uses.", "date_order",
                          {"mdy": "Month first: 04/30/2026", "dmy": "Day first: 30/04/2026"})
    return None


def zone(offset: str | None) -> dt.timezone:
    """A fixed offset such as "UTC", "+02:00" or "-05:00"."""
    text = (offset or "UTC").strip().upper()
    if text in ("UTC", "Z", "GMT", "+00:00", "00:00"):
        return dt.timezone.utc
    m = re.fullmatch(r"([+-])(\d{1,2}):?(\d{2})?", text)
    if not m or int(m.group(2)) > 14 or int(m.group(3) or 0) > 59:
        raise FileProblem(f"{offset!r} isn't a time zone offset. Use UTC or a form like +02:00.")
    delta = dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
    return dt.timezone(-delta if m.group(1) == "-" else delta)


# Usage is from this era: a year outside it is a cell that can't be read, never a time a later step
# (a day added to a date, a reconcile tolerance) could push off the calendar.
YEARS = range(1970, 3000)


def when(raw: str, order: str | None, tz: dt.timezone) -> dt.datetime | None:
    """A time in UTC. Times without an offset are read in ``tz``; None when the cell isn't a time,
    including one outside YEARS."""
    t = _when(raw, order, tz)
    return t if t is None or t.year in YEARS else None


def _when(raw: str, order: str | None, tz: dt.timezone) -> dt.datetime | None:
    v = (raw or "").strip()
    if not v:
        return None
    m = _SLASH.match(v)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        month, day = (a, b) if order != "dmy" else (b, a)
        hour, minute, second = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
        ampm = (m.group(7) or "").lower()
        if ampm:
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if ampm == "pm" else 0)
        try:
            # OverflowError: a time at the very edge of the calendar that leaves it once moved to UTC.
            return dt.datetime(year, month, day, hour, minute, second, tzinfo=tz).astimezone(dt.timezone.utc)
        except (ValueError, OverflowError):
            return None
    try:
        t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        return (t if t.tzinfo else t.replace(tzinfo=tz)).astimezone(dt.timezone.utc)
    except (ValueError, OverflowError):
        return None


def iso(t: dt.datetime | None) -> str | None:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds") if t else None


def cells(headers: list[str], rows: list[list[str]], index: dict[str, int]) -> list[dict[str, Any]]:
    """Each row as {field: cell}, for the columns a provider uses."""
    out = []
    for r in rows:
        out.append({f: (r[i].strip() if i < len(r) else "") for f, i in index.items()})
    return out
