"""Finds the SQL and Python examples in docs/ and runs them where this PC can, for test_docs_examples.py.

Two kinds of example are checked:

- A fenced ```sql, ```python, ```py or ```pyspark block. The line before it (blank lines skipped) is a marker,
  ``<!-- example: runs -->`` or ``<!-- example: illustrative -->``, so a reader's copy of the page shows
  nothing extra and every block says which it is. A block marked runs is run by a test in
  test_docs_examples.py, keyed by its page and first line. The two best-practice pages are the exception:
  their Before/After blocks are checked against their own rule (test_docs_practice_examples.py).
- An inline claim: `<expression>` gives / is / returns `<value>` (or NULL, NaN, true, false or a number
  written bare) in prose. Each one found on a checked page is either a Claim, run here and compared with the
  value the page shows, or in ILLUSTRATIVE with the reason it isn't run (it needs a table, a warehouse,
  the clock, and so on).

Not a test module: pytest collects test_*.py only.
"""

from __future__ import annotations

import datetime as dt
import decimal
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

DOCS = Path(__file__).resolve().parents[2] / "docs"

# Pages whose inline claims are checked: the user guide and the compatibility pages.
CLAIM_PAGES = ("user-guide.md", "sql-compatibility.md", "local-data.md", "notebooks.md", "engines.md")
# Pages not scanned for fenced blocks: plans and review records describe work, not the product.
SKIP_DIRS = ("product", "quality")
PRACTICE_PAGES = ("notebook-best-practices.md", "sql-best-practices.md")
FENCED_LANGS = ("sql", "python", "py", "pyspark")
MARKER = re.compile(r"^\s*<!--\s*example:\s*(runs|illustrative)\b[^>]*-->\s*$")


@dataclass(frozen=True)
class Block:
    page: str        # relative to docs/, with forward slashes
    line: int        # the fence's line, 1-based
    lang: str
    code: str
    marker: str | None   # "runs", "illustrative" or None

    @property
    def first(self) -> str:
        """The block's first non-empty line: the key a test uses, stable when lines are added above it."""
        return next((ln.strip() for ln in self.code.splitlines() if ln.strip()), "")


def fenced_blocks() -> Iterator[Block]:
    fence = re.compile(r"^([ \t]*)```([A-Za-z0-9_+-]*)[^\n]*\n(.*?)^[ \t]*```", re.M | re.S)
    for path in sorted(DOCS.rglob("*.md")):
        rel = path.relative_to(DOCS).as_posix()
        if rel.split("/", 1)[0] in SKIP_DIRS:
            continue
        text = path.read_text(encoding="utf-8")
        for m in fence.finditer(text):
            lang = m.group(2).lower()
            if lang not in FENCED_LANGS:
                continue
            before = [ln for ln in text[:m.start()].splitlines() if ln.strip()]
            mark = MARKER.match(before[-1]) if before else None
            indent = len(m.group(1))
            code = "\n".join(ln[indent:] for ln in m.group(3).splitlines())
            yield Block(rel, text[:m.start()].count("\n") + 1, lang, code, mark.group(1) if mark else None)


# ---- inline claims

_CLAIM = re.compile(
    r"`(?P<expr>[^`\n]+)`\s+(?:gives|is|returns|answers|becomes|comes back as|evaluates to)\s+(?:an?\s+|the\s+)?"
    r"(?:`(?P<want>[^`\n]+)`|(?P<bare>NULL|NaN|true|false|-?\d+(?:\.\d+)?)\b)")


def _expression_like(text: str) -> bool:
    """A call, an operation or a literal: not a bare name such as `typeof` or `mode`."""
    return bool(re.search(r"\w\s*\(|\s(?:/|\|\||\+|-|\*|%|div|=|<|>)\s|^'.*'$|^-?\d", text))


@dataclass(frozen=True)
class Found:
    page: str
    line: int
    expr: str
    want: str


def inline_claims(page: str) -> list[Found]:
    text = (DOCS / page).read_text(encoding="utf-8")
    out = []
    for m in _CLAIM.finditer(text):
        if _expression_like(m.group("expr")):
            out.append(Found(page, text[:m.start()].count("\n") + 1, m.group("expr"), m.group("want") or m.group("bare")))
    return out


@dataclass(frozen=True)
class Claim:
    """An inline example run here. ``path`` is how the page says it runs:

    - "notebook": a notebook's spark.sql on this PC (fabric_desktop.sql.run, Spark dialect)
    - "local-spark", "local-tsql", "local-duckdb": a local data project's DuckDB engine in that dialect
    - "databricks", "snowflake": a Databricks SQL or Snowflake cell, translated as the app does

    ``query`` is the whole statement when the expression isn't one (default: SELECT <expr>).
    ``typeof``: the page names the result's type, not its value. ``setup`` runs first (local paths only).
    ``want`` defaults to the value the scan found beside ``expr``.
    """
    page: str
    expr: str
    path: str
    want: str | None = None
    query: str | None = None
    typeof: bool = False
    setup: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""


def render(value: Any) -> str:
    """A value as the docs write it: NULL, true/false, a decimal with its digits, a date as yyyy-mm-dd."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if value.is_integer() and abs(value) < 1e15:
            return f"{value:.1f}"
        return repr(value)
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(render(v) for v in value) + "]"
    return str(value)


def same(got: str, want: str) -> bool:
    """The docs' value against the rendered one: quotes, case of NULL/true/false and a trailing .0 aside."""
    w = want.strip()
    if len(w) >= 2 and w[0] == w[-1] and w[0] in "'\"":
        w = w[1:-1]
    g = got.strip()
    if g == w or g.lower() == w.lower() and w.upper() in ("NULL", "TRUE", "FALSE", "NAN"):
        return True
    try:
        return decimal.Decimal(g) == decimal.Decimal(w)
    except (decimal.InvalidOperation, ValueError):
        return False


# ---- inline statements: a whole query written inline, such as `SELECT 7 div 0`

_STATEMENT = re.compile(r"`((?:SELECT\s|WITH\s+\w+\s+AS\s*\()[^`\n]+)`")


def _whole(sql: str) -> bool:
    """A statement someone could run as written: not a pattern with ``...`` or a ``<placeholder>``."""
    return "..." not in sql and "<" not in sql


def inline_statements(page: str) -> list[Found]:
    """Every inline `SELECT ...` or `WITH ...` on the page (``want`` is empty: a claim beside it is checked
    as a Claim)."""
    text = (DOCS / page).read_text(encoding="utf-8")
    return [Found(page, text[:m.start()].count("\n") + 1, m.group(1), "") for m in _STATEMENT.finditer(text)
            if _whole(m.group(1))]


@dataclass(frozen=True)
class Statement:
    """An inline statement run here on ``path`` (as for Claim). ``refused``: the page says it is refused, so
    it must stop with the app's own message (one that says it isn't run here), never DuckDB's error.
    ``match`` is a regex the error must contain when refused."""
    page: str
    sql: str
    path: str
    refused: bool = False
    match: str = ""
    setup: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""
