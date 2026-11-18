"""CSV exports protect text cells a spreadsheet would run as a formula (WR-228).

Excel, LibreOffice and Google Sheets read a CSV cell that starts with ``=``, ``+``, ``-`` or ``@`` as a formula
(and Excel also one that starts with a tab or a carriage return before one), so a value such as ``=HYPERLINK(...)`` in a query's result would run when the file is opened. Every CSV file the
app writes for someone to open puts an apostrophe in front of such a text cell (``'=1+2``), which a spreadsheet
shows as the text. Number, date and other non-text cells are never changed: ``-5`` in a number column stays
``-5``. The export says how many cells it protected. ``raw=True`` writes the values as they are, for that one
export only.

tests/test_csv_cells.py checks that every CSV writer in the engine goes through here, or is listed there with
the reason it writes data files rather than exports.
"""

from __future__ import annotations

from typing import Any, Iterable

import pyarrow as pa

STARTS = ("=", "+", "-", "@", "\t", "\r")
_PATTERN = r"^[=+\-@\t\r]"


def protect(value: str) -> str:
    """``value`` with an apostrophe in front when it starts like a formula."""
    return "'" + value if value.startswith(STARTS) else value


class Cells:
    """Protects the cells of a listing export one at a time (``cells(value)``), counting the ones it changed."""

    def __init__(self) -> None:
        self.protected = 0

    def __call__(self, value: Any) -> str:
        text = "" if value is None else str(value)
        if text.startswith(STARTS):
            self.protected += 1
            return "'" + text
        return text


def protect_row(values: Iterable[Any], raw: bool = False) -> tuple[list[Any], int]:
    """A row for ``csv.writer``: its text cells protected, and how many were."""
    row = list(values)
    if raw:
        return row, 0
    n = 0
    for i, v in enumerate(row):
        if isinstance(v, str) and v.startswith(STARTS):
            row[i], n = "'" + v, n + 1
    return row, n


def _plain_text(t: pa.DataType) -> bool:
    return pa.types.is_string(t) or pa.types.is_large_string(t) or pa.types.is_string_view(t)


def _text(t: pa.DataType) -> bool:
    """A text column, or a dictionary-encoded one (a DuckDB ENUM comes back as one)."""
    return _plain_text(t) or (pa.types.is_dictionary(t) and _plain_text(t.value_type))


def _column(column: Any) -> tuple[Any, int]:
    import pyarrow.compute as pc  # here, not at the top: the engine starts without it (test_startup_imports)

    if isinstance(column, pa.ChunkedArray):
        column = column.combine_chunks()
    if pa.types.is_string_view(column.type):
        column = column.cast(pa.string())
    if pa.types.is_dictionary(column.type):  # protect the dictionary's values, so the column keeps its type
        values, n_values = _column(column.dictionary)
        if not n_values:
            return column, 0
        risky = pc.take(pc.match_substring_regex(column.dictionary, _PATTERN), column.indices)
        n = pc.sum(pc.cast(risky, pa.int64())).as_py() or 0
        return pa.DictionaryArray.from_arrays(column.indices, values, ordered=column.type.ordered), n
    risky = pc.match_substring_regex(column, _PATTERN)
    n = pc.sum(pc.cast(risky, pa.int64())).as_py() or 0
    if not n:
        return column, 0
    marked = pc.binary_join_element_wise(pa.scalar("'", column.type), column, pa.scalar("", column.type))
    return pc.if_else(risky, marked, column), n


def protect_header(schema: pa.Schema, raw: bool = False) -> tuple[pa.Schema, int]:
    """The schema with its column names protected, for the header row Arrow's CSV writer takes from it, and how
    many names were. Count it once per file; protect_batch gives every batch the same names."""
    if raw:
        return schema, 0
    names = [protect(f.name) for f in schema]
    n = sum(1 for f, name in zip(schema, names, strict=True) if f.name != name)
    views = any(pa.types.is_string_view(f.type) for f in schema)
    if not n and not views:
        return schema, 0
    return pa.schema([f.with_name(name).with_type(_written(f.type)) for f, name in zip(schema, names, strict=True)],
                     schema.metadata), n


def _written(t: pa.DataType) -> pa.DataType:
    """The type a column is written as: Arrow's compute functions don't take string_view, so it goes as string."""
    return pa.string() if pa.types.is_string_view(t) else t


def protect_batch(batch: pa.RecordBatch, raw: bool = False) -> tuple[pa.RecordBatch, int]:
    """A batch for Arrow's CSV writer: its text columns protected, and how many cells were. Its column names are
    protected too, as protect_header protects the writer's (and counts them, once)."""
    if raw:
        return batch, 0
    schema, renamed = protect_header(batch.schema)
    changed = renamed or schema != batch.schema
    total, columns = 0, []
    for column, f in zip(batch.columns, batch.schema, strict=True):
        if _text(f.type):
            column, n = _column(column)
            total += n
        columns.append(column)
    return (pa.RecordBatch.from_arrays(columns, schema=schema), total) if total or changed else (batch, 0)


def protect_table(table: pa.Table, raw: bool = False) -> tuple[pa.Table, int]:
    """A table for Arrow's CSV writer: its column names and text columns protected, and how many were."""
    if raw:
        return table, 0
    total = 0
    for i, f in enumerate(table.schema):
        if _text(f.type):
            column, n = _column(table.column(i))
            if n or column.type != f.type:
                table, total = table.set_column(i, f.with_type(column.type), column), total + n
    schema, renamed = protect_header(table.schema)
    return (table.rename_columns(schema.names), total + renamed) if renamed else (table, total)


def unprotect(value: str) -> str:
    """The value a protected cell came from, for a CSV the app reads back (a data dictionary's Import)."""
    return value[1:] if value.startswith("'") and value[1:].startswith(STARTS) else value


def note(protected: int, can_keep_raw: bool = True) -> str:
    """What the export says when it protected cells; empty when it didn't. ``can_keep_raw`` when the export
    offers Keep raw values."""
    if not protected:
        return ""
    cells, got = ("1 text cell", "it got") if protected == 1 else (f"{protected:,} text cells", "each got")
    said = (f"{cells} started with =, +, -, @, a tab or a carriage return, so {got} an apostrophe in front to keep a "
            "spreadsheet from running it as a formula.")
    return said + (" To keep the values exactly as they are, pick CSV, Keep raw values." if can_keep_raw
                   else "")
