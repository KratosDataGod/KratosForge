"""Compare one table in two places: Dev against Test or Prod, or now against an earlier Delta version.

The answer is what a data engineer checks before a promote: row counts, keys that are only on
one side, which columns changed value for the same key, and how the numeric totals moved.
Columns each environment issues for itself (surrogate keys, run stamps, Type 2 dates) are
left out, since they always differ and say nothing about the data.

A table with more rows than ``LIMIT_ROWS`` on either side isn't loaded into memory: ``hashed``
streams both sides through DuckDB for row counts, totals and an order-independent checksum of
every row (the same approach as verify.py), so it finishes on a table larger than memory but
can't say which keys differ.
"""

from __future__ import annotations

import json
from typing import Any

import polars as pl
import pyarrow as pa
import pyarrow.dataset as pads

from .. import side_by_side
from ..verify import DEFAULTS
from .project import SCD2_COLUMNS, SYSTEM_COLUMNS, Entity
from ..duckdb_rows import one_row

SAMPLE = 20
# Above this many rows on either side, compare counts, totals and a checksum instead of every row.
LIMIT_ROWS = int(DEFAULTS["limit_rows"])


def _rows(df: pl.DataFrame) -> list[dict[str, Any]]:
    return json.loads(df.head(SAMPLE).write_json())


def ignored_columns(e: Entity) -> set[str]:
    """Columns that differ between environments by design."""
    out = set(SYSTEM_COLUMNS) | set(SCD2_COLUMNS) | set(e.key_columns)
    if e.surrogate_key:
        out.add(e.surrogate_key)
    for lk in e.spec.get("lookup", []):
        out.add(lk["as"])  # a surrogate key looked up from a dimension
    return out


def _current(df: pl.DataFrame | None) -> pl.DataFrame | None:
    if df is not None and "is_current" in df.columns:
        return df.filter(pl.col("is_current"))
    return df


def missing(e: Entity, left_rows: int | None, right_rows: int | None) -> dict[str, Any]:
    """The compare when the table is missing on one side or both (``None`` rows): nothing to load."""
    return _empty(e, left_rows, right_rows) | {
        "note": "The table doesn't exist on " + ("either side" if left_rows is None and right_rows is None
                                                  else "the left" if left_rows is None else "the right") + " yet."}


def _empty(e: Entity, left_rows: int | None, right_rows: int | None) -> dict[str, Any]:
    return {
        "entity": e.name, "keys": e.keys, "rows": {"left": left_rows, "right": right_rows},
        "columns": {"only_left": [], "only_right": [], "type_changed": []},
        "only_left": {"count": 0, "rows": []}, "only_right": {"count": 0, "rows": []},
        "changed": {"count": 0, "by_column": {}, "rows": []}, "totals": [], "compared_on_keys": False,
        "method": "full",
    }


def compare(e: Entity, left: pl.DataFrame | None, right: pl.DataFrame | None) -> dict[str, Any]:
    left, right = _current(left), _current(right)
    if left is None or right is None:
        return missing(e, None if left is None else left.height, None if right is None else right.height)
    out = _empty(e, left.height, right.height)
    skip = ignored_columns(e)
    lcols = [c for c in left.columns if c not in skip]
    rcols = [c for c in right.columns if c not in skip]
    out["columns"]["only_left"] = [c for c in lcols if c not in rcols]
    out["columns"]["only_right"] = [c for c in rcols if c not in lcols]
    common = [c for c in lcols if c in rcols]
    out["columns"]["type_changed"] = [{"column": c, "left": str(left.schema[c]), "right": str(right.schema[c])}
                                      for c in common if left.schema[c] != right.schema[c]]

    # How each number column's total moved: the quickest sign that a change changes the numbers.
    # Keys and ids are numbers too, but their totals mean nothing.
    for c in common:
        if c in e.keys or c.lower().endswith("id"):
            continue
        if left.schema[c].is_numeric() and right.schema[c].is_numeric():
            a, b = left[c].sum(), right[c].sum()
            out["totals"].append({"column": c, "left": a, "right": b, "diff": (b or 0) - (a or 0)})

    keys = [k for k in e.keys if k in common]
    if not keys or len(keys) != len(e.keys):
        out["note"] = "This table has no contract keys, so rows can't be matched one to one; counts and totals only."
        return out
    out["compared_on_keys"] = True
    # Compare as text so an int on one side and a float on the other still match on equal values.
    as_text = lambda df: df.select([pl.col(c).cast(pl.String) for c in common]).unique(subset=keys, keep="first")
    l, r = as_text(left), as_text(right)
    only_l = l.join(r.select(keys), on=keys, how="anti")
    only_r = r.join(l.select(keys), on=keys, how="anti")
    out["only_left"] = {"count": only_l.height, "rows": _rows(only_l)}
    out["only_right"] = {"count": only_r.height, "rows": _rows(only_r)}

    values = [c for c in common if c not in keys]
    both = l.join(r, on=keys, how="inner", suffix="__r")
    if not values or not both.height:
        return out
    diffs = both.select(keys + [pl.col(c).ne_missing(pl.col(f"{c}__r")).alias(c) for c in values])
    by_column = {c: int(diffs[c].sum()) for c in values}
    out["changed"]["by_column"] = {c: n for c, n in by_column.items() if n}
    changed_mask = pl.any_horizontal([pl.col(c) for c in values])
    changed_keys = diffs.filter(changed_mask).select(keys)
    out["changed"]["count"] = changed_keys.height
    sample = both.join(changed_keys.head(SAMPLE), on=keys, how="semi")
    rows = []
    for row in sample.iter_rows(named=True):
        rows.append({"key": {k: row[k] for k in keys},
                     "changes": [{"column": c, "left": row[c], "right": row[f"{c}__r"]}
                                 for c in values if row[c] != row[f"{c}__r"]]})
    out["changed"]["rows"] = rows
    return out


# ---- tables larger than memory


def _projected(data: pads.Dataset, columns: list[str]) -> pa.RecordBatchReader:
    """``columns`` of the current rows of ``data``, as a stream: nothing is held in memory."""
    return data.scanner(columns=columns, filter=_current_rows(data)).to_reader()


def _current_rows(data: pads.Dataset) -> Any:
    """The dataset filter for current rows (Type 2 tables keep old versions), or None for all rows."""
    return pads.field("is_current") == True if "is_current" in data.schema.names else None  # noqa: E712


def _summary(data: pads.Dataset, columns: list[str], numbers: set[str], totals: list[str]) -> dict[str, Any]:
    """Rows, an order-independent checksum of ``columns`` and the sums of ``totals``, in one pass."""
    import duckdb
    if not columns:  # nothing shared to checksum: only the row count says anything
        return {"rows": data.count_rows(filter=_current_rows(data)), "checksum": None, "totals": []}
    reader = _projected(data, columns)
    try:
        fields = sorted(reader.schema, key=lambda f: f.name.lower())
        row_hash = f"coalesce(sum(CAST(hash({', '.join(side_by_side._normalized(f.name, f.type, f.name in numbers) for f in fields)}) AS HUGEINT)), 0)"
        integers = {f.name for f in reader.schema if pa.types.is_integer(f.type)}
        sums = [f"sum({side_by_side._quote(c)})" if c in integers else f"sum(CAST({side_by_side._quote(c)} AS DOUBLE))"
                for c in totals]
        con = duckdb.connect()
        try:
            con.execute("SET TimeZone = 'UTC'")
            con.register("t", reader)
            got = one_row(con.execute(f"SELECT count(*), {', '.join([row_hash] + sums)} FROM t"))
        finally:
            con.close()
    finally:
        reader.close()
    return {"rows": int(got[0]), "checksum": f"{int(got[1]) % (1 << 64):016x}", "totals": list(got[2:])}


# Sums of floating-point columns depend on the order rows are added in, which differs between two
# scans of the same rows; a difference this small, relative to the totals, is the same total.
FLOAT_TOLERANCE = 1e-9
FLOAT_FLOOR = 1e-9  # and an absolute one, for sums near zero


def _diff(left: Any, right: Any) -> Any:
    """How a total moved; 0 for two float sums equal within FLOAT_TOLERANCE."""
    d = (right or 0) - (left or 0)
    if isinstance(d, float) and abs(d) <= max(FLOAT_TOLERANCE * max(abs(left or 0), abs(right or 0)), FLOAT_FLOOR):
        return 0.0
    return d


def _is_number(t: pa.DataType) -> bool:
    return pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t)


def hashed(e: Entity, left: pads.Dataset, right: pads.Dataset) -> dict[str, Any]:
    """The compare of two tables too large to load: row counts, column differences, totals and
    whether a checksum of every row matches. Both sides are streamed, never held in memory."""
    skip = ignored_columns(e)
    ls, rs = left.schema, right.schema
    lcols = [c for c in ls.names if c not in skip]
    rcols = [c for c in rs.names if c not in skip]
    common = [c for c in lcols if c in rcols]
    kinds = {c: (side_by_side.kind(ls.field(c).type), side_by_side.kind(rs.field(c).type)) for c in common}
    # An integer on one side and a decimal on the other hash as numbers, so equal values still match.
    numbers = {c for c, (a, b) in kinds.items() if a != b and {a, b} <= {"integer", "number"}}
    totals = [c for c in common if c not in e.keys and not c.lower().endswith("id")
              and _is_number(ls.field(c).type) and _is_number(rs.field(c).type)]
    a, b = _summary(left, common, numbers, totals), _summary(right, common, numbers, totals)
    # With no shared columns there's nothing to checksum: say so rather than call it a match.
    match = None if not common else a["checksum"] == b["checksum"]
    return {
        "entity": e.name, "keys": e.keys, "rows": {"left": a["rows"], "right": b["rows"]},
        "columns": {"only_left": [c for c in lcols if c not in rcols], "only_right": [c for c in rcols if c not in lcols],
                    "type_changed": [{"column": c, "left": str(ls.field(c).type), "right": str(rs.field(c).type)}
                                     for c in common if ls.field(c).type != rs.field(c).type]},
        "only_left": {"count": 0, "rows": []}, "only_right": {"count": 0, "rows": []},
        "changed": {"count": 0, "by_column": {}, "rows": []},
        "totals": [{"column": c, "left": x, "right": y, "diff": _diff(x, y)}
                   for c, x, y in zip(totals, a["totals"], b["totals"])],
        "compared_on_keys": False, "method": "hashed", "limit_rows": LIMIT_ROWS,
        "checksum": {"left": a["checksum"], "right": b["checksum"], "match": match},
        "note": (f"More than {LIMIT_ROWS:,} rows, so row counts, totals and a checksum of every row in the shared columns "
                 f"were compared instead of each row by key. " + (
                     "The tables share no columns, so no rows could be compared." if match is None
                     else "The checksums match: the rows are the same." if match
                     else "The checksums differ: some rows differ, but which keys can't be told without loading the table.")),
    }
