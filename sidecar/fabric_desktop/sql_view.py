"""Sort, filter and column statistics over a SQL run's result snapshot (editor parity S19, S20, S26).

The SQL page's result grid sorts and filters every row of a run's result, not just the page on
screen, and profiles a column, all from the run's Arrow snapshot on this PC: nothing runs again and
nothing is sent anywhere. As the notebook's result viewer (frames.py), the snapshot is read by a
private in-memory DuckDB connection with its columns renamed ``c0..cN``, so any column name
(duplicates, quotes) is safe, and every typed value reaches DuckDB as a parameter, never as SQL text.

A filter is what a person types in a column's filter box:

- ``West``: the text contains it, ignoring case (a number column: equals it, when it is a number)
- ``= West``, ``!= Central``, ``<> Central``: equal or not, ignoring case for text
- ``> 3000``, ``>= 3000``, ``< 3000``, ``<= 3000``: compared as numbers, dates or text
- ``null``, ``not null`` (also ``= null``, ``!= null``, ``is null``, ``is not null``): empty or not
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pyarrow as pa

from .duckdb_rows import for_arrow, one_row
from .frames import Frames, _kind, _rows

# Sorting or filtering reads every row of the snapshot, and sql_page must answer within the watchdog's
# quick-call limit (60 s, app/src-tauri/src/watchdog.rs); 5 million rows take a few seconds on a laptop.
VIEW_MAX_ROWS = 5_000_000
MAX_SORT_KEYS = 3
MAX_FILTER_TEXT = 200
OPS = ("!=", "<>", ">=", "<=", "=", ">", "<")
_OP = re.compile(r"^\s*(!=|<>|>=|<=|=|>|<)\s*(.*?)\s*$", re.S)
_NULL = re.compile(r"^\s*(?:=\s*|is\s+)?null\s*$", re.I)
_NOT_NULL = re.compile(r"^\s*(?:(?:!=|<>)\s*|is\s+not\s+|not\s+)null\s*$", re.I)
_NUMBER = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")


class FilterError(ValueError):
    """A filter box's text can't be read for its column; ``column`` is the column's position."""

    def __init__(self, column: int, message: str) -> None:
        super().__init__(message)
        self.column = column


def _number(text: str) -> float | None:
    t = text.replace(",", "").strip()
    return float(t) if _NUMBER.fullmatch(t) else None


def _date(text: str) -> str | None:
    try:
        return dt.datetime.fromisoformat(text.strip()).isoformat(sep=" ")
    except ValueError:
        return None


def filter_sql(c: str, field: pa.Field, column: int, text: str) -> tuple[str, list[Any]]:
    """The WHERE condition (and its parameters) for one filter box on column ``c``."""
    name = field.name
    text = (text or "").strip()
    if len(text) > MAX_FILTER_TEXT:
        raise FilterError(column, f"The filter on {name} is too long (at most {MAX_FILTER_TEXT} characters).")
    if _NULL.fullmatch(text):
        return f"{c} IS NULL", []
    if _NOT_NULL.fullmatch(text):
        return f"{c} IS NOT NULL", []
    kind = _kind(field.type)
    if pa.types.is_time(field.type):
        kind = "time"
    elif pa.types.is_duration(field.type) or pa.types.is_interval(field.type):
        kind = "text"  # compared as their text
    m = _OP.fullmatch(text)
    if not m:  # no operator: contains (a number column: equals, when the text is a number)
        n = _number(text) if kind == "number" else None
        if n is not None:
            return f"CAST({c} AS DOUBLE) = ?", [n]
        like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return f"CAST({c} AS VARCHAR) ILIKE ? ESCAPE '\\'", [like]
    op, value = m.group(1), m.group(2)
    op = "!=" if op == "<>" else op
    if not value:
        raise FilterError(column, f"Write a value after {op} in the filter on {name}.")
    if kind == "number":
        n = _number(value)
        if n is None:
            raise FilterError(column, f"Write a number after {op} in the filter on {name}, as 3000 or 12.5.")
        return f"CAST({c} AS DOUBLE) {op} ?", [n]
    if kind == "date":
        d = _date(value)
        if d is None:
            raise FilterError(column, f"Write a date after {op} in the filter on {name}, as 2026-10-01.")
        return f"CAST({c} AS TIMESTAMP) {op} CAST(? AS TIMESTAMP)", [d]
    if kind == "time":
        try:
            t = dt.time.fromisoformat(value.strip()).isoformat()
        except ValueError:
            raise FilterError(column, f"Write a time after {op} in the filter on {name}, as 14:30 or 14:30:15.") from None
        return f"CAST({c} AS TIME) {op} CAST(? AS TIME)", [t]
    if kind == "boolean":
        b = value.lower()
        if b not in ("true", "false") or op not in ("=", "!="):
            raise FilterError(column, f"The filter on {name} takes = true, = false, != true or != false.")
        return f"{c} {op} ?", [b == "true"]
    if op in ("=", "!="):
        return f"lower(CAST({c} AS VARCHAR)) {op} lower(?)", [value]
    return f"CAST({c} AS VARCHAR) {op} ?", [value]


def _index(column: Any) -> int:
    """A column's position as sent, or -1 (refused as a column the result doesn't have) when it isn't a whole number."""
    if isinstance(column, bool) or (isinstance(column, float) and not column.is_integer()):
        return -1
    try:
        return int(column)
    except (TypeError, ValueError):
        return -1


def entries(given: Any, what: str) -> list[dict[str, Any]]:
    """The grid's list of sorts or filters, each a dict; anything else is refused by name, not with a bare error."""
    if given is None:
        return []
    if not isinstance(given, list) or not all(isinstance(e, dict) for e in given):
        raise ValueError(f"The {what} isn't in the form the grid sends, which is a list of {{column, ...}} entries.")
    return given


def _where(table: pa.Table, cols: list[str], filters: list[dict[str, Any]] | None) -> tuple[str, list[Any]]:
    parts, params = where_parts(table, cols, filters)
    return (" WHERE " + " AND ".join(parts)) if parts else "", params


def where_parts(table: pa.Table, cols: list[str], filters: list[dict[str, Any]] | None) -> tuple[list[str], list[Any]]:
    """Each filled filter box's condition, in parentheses, and their parameters in order."""
    parts: list[str] = []
    params: list[Any] = []
    for f in entries(filters, "filter"):
        i = _index(f.get("column"))
        if not 0 <= i < len(cols):
            raise ValueError("A filter names a column this result doesn't have.")
        if not str(f.get("text") or "").strip():
            continue
        sql, p = filter_sql(cols[i], table.schema.field(i), i, str(f["text"]))
        parts.append(f"({sql})")
        params.extend(p)
    return parts, params


def _order(table: pa.Table, cols: list[str], sort: list[dict[str, Any]] | None) -> str:
    keys = []
    for s in entries(sort, "sort")[:MAX_SORT_KEYS]:
        i = _index(s.get("column"))
        if not 0 <= i < len(cols):
            raise ValueError("The sort names a column this result doesn't have.")
        t = table.schema.field(i).type
        if pa.types.is_nested(t):
            raise ValueError(f"{table.schema.field(i).name} holds lists or structs, so it can't be sorted.")
        keys.append(f"{cols[i]} {'DESC' if s.get('desc') else 'ASC'} NULLS LAST")
    # The snapshot's own order breaks ties, so a sorted page is the same each time it is read.
    return " ORDER BY " + ", ".join([*keys, "__row"]) if keys else " ORDER BY __row"


def check_size(rows: int, what: str) -> None:
    if rows > VIEW_MAX_ROWS:
        raise ValueError(f"{what} works on results of up to {VIEW_MAX_ROWS:,} rows, and this one has {rows:,}. "
                         "Narrow the query, or export the result and open it elsewhere.")


def view_page(table: pa.Table, offset: int, limit: int, sort: list[dict[str, Any]] | None = None,
              filters: list[dict[str, Any]] | None = None) -> tuple[list[list[Any]], int]:
    """Rows ``offset`` to ``offset + limit`` of ``table`` filtered and sorted, and how many rows match."""
    import duckdb
    cols = [f"c{i}" for i in range(table.num_columns)]
    where, params = _where(table, cols, filters)
    order = _order(table, cols, sort)
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone = 'UTC'")
        # The snapshot's row order as a column: a scan of an Arrow table may run in parallel.
        con.register("t", table.rename_columns(cols).append_column("__row", pa.array(range(table.num_rows), pa.int64())))
        total = one_row(con.execute(f"SELECT count(*) FROM t{where}", params))[0] if where else table.num_rows
        select = ", ".join(cols)
        rows = _rows(for_arrow(con, f"SELECT {select} FROM t{where}{order} LIMIT {int(limit)} OFFSET {int(offset)}", params))
    finally:
        con.close()
    return rows, int(total)


def column_stats(table: pa.Table, column: int) -> dict[str, Any]:
    """One column's statistics over every row of ``table``: as the notebook's Inspect profile
    (Frames._profile_column): missing, distinct (approximate), and for numbers min, max, mean,
    quartiles and a histogram; for dates their range; for anything else the most common values."""
    import duckdb
    if not 0 <= int(column) < table.num_columns:
        raise ValueError("That column isn't in this result.")
    i = int(column)
    con = duckdb.connect()
    try:
        con.register("t", pa.table({"c": table.column(i)}))
        out = Frames._profile_column(con, "c", table.schema.field(i), table.num_rows)
    finally:
        con.close()
    out["column"] = i
    out["rows"] = table.num_rows
    # Distinct is counted approximately (HyperLogLog), so it can come out above the values there are.
    out["distinct"] = min(int(out.get("distinct") or 0), table.num_rows - int(out.get("missing") or 0))
    return out
