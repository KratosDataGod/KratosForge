"""Compare data on the SQL page (editor parity S34, Preview): the rows of A and B, side by side.

A and B are each a lakehouse table or a query. The page runs each one as an ordinary SQL page run that
only reads (sql_exec.py: its queue, Stop and time limit), then asks for ``compare`` with the two runs'
ids. This module reads the two results from their snapshots on this PC and compares them with verify.py,
the same row diff that Verify uses. It runs no query of its own and changes nothing, so it needs no confirm.

* **Keys** match a row of A with a row of B. A key repeated on either side stops the compare and names
  the key, since there's no single row to compare. With no key, rows compare as whole rows, and only
  "Only in A" and "Only in B" are counted.
* **Tolerances** apply to number columns: ``{"amount": 0.01}`` makes amounts equal within 0.01. Every
  other column must be equal. A NULL equals only another NULL.
* ``status`` lists one kind of differing row (``only_a``, ``only_b``, ``changed``), a page at a time.
* ``export`` writes every differing row to a new CSV file in Downloads, cells that start like a formula
  protected as the SQL page's own export protects them.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pyarrow as pa

from . import csv_cells, jsonfile, verify
from .filenames import file_stem
from .frames import csv_ready_batch, csv_ready_schema

PAGE_ROWS = 50
MAX_KEYS = 8
MAX_TOLERANCES = 50
_STATUS = {"only_a": "only_pc", "only_b": "only_fabric", "changed": "changed"}
_BACK = {v: k for k, v in _STATUS.items()}


def _rules(keys: Any, tolerance: Any) -> dict[str, Any]:
    if keys is None:
        keys = []
    if not isinstance(keys, list) or len(keys) > MAX_KEYS or not all(isinstance(k, str) and k.strip() for k in keys):
        raise ValueError(f"Match rows on up to {MAX_KEYS} columns, each named.")
    if len({k.strip().lower() for k in keys}) != len(keys):
        raise ValueError("A column is in the key twice.")
    if tolerance is None:
        tolerance = {}
    if not isinstance(tolerance, dict) or len(tolerance) > MAX_TOLERANCES:
        raise ValueError(f'A tolerance is a number column and how far apart its values may be, like {{"amount": 0.01}}, '
                         f"for up to {MAX_TOLERANCES} columns.")
    columns = {}
    for name, value in tolerance.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("A tolerance names its column.")
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"The tolerance for {name} is a number 0 or more, like 0.01.")
        columns[name.strip()] = {"abs": value}
    # "Other columns must be equal": no default tolerance, so numbers compare exactly.
    return {"keys": [k.strip() for k in keys], "abs": 0, "rel": 0, "columns": columns, "examples": PAGE_ROWS}


def _read(runs: Any, run_id: Any, side: str) -> tuple[dict[str, Any], pa.Table]:
    if not isinstance(run_id, str) or not run_id:
        raise ValueError(f"Run {side} before comparing.")
    status, table = runs.table(run_id)
    if status.get("change") or status.get("plan") or status.get("profile") or status.get("parts"):
        raise ValueError(f"Compare data reads the result of one query that only reads, and {side} isn't one.")
    return status, table


def _offset(offset: Any) -> int:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError(f"offset is a whole number of rows to skip, 0 or more, not {offset!r}.")
    return offset


def _numbers(table: pa.Table) -> set[str]:
    return {f.name.lower() for f in table.schema if pa.types.is_integer(f.type) or pa.types.is_floating(f.type)
            or pa.types.is_decimal(f.type)}


def _check_tolerances(rules: dict[str, Any], ta: pa.Table, tb: pa.Table) -> None:
    both = _numbers(ta) & _numbers(tb)
    for name in rules["columns"]:
        if name.lower() not in both:
            raise ValueError(f"A tolerance is for a number column in both A and B, and {name} isn't one.")
        if name.lower() in {k.lower() for k in rules["keys"]}:
            raise ValueError(f"{name} matches rows, so it can't have a tolerance.")


def compare(runs: Any, a: Any, b: Any, keys: Any = None, tolerance: Any = None, status: Any = None,
            offset: Any = 0) -> dict[str, Any]:
    """A beside B: the verdict, the counts and a page of differing rows. Reads two finished runs; runs nothing."""
    if status is not None and status not in _STATUS:
        raise ValueError(f"status is one of {', '.join(_STATUS)}.")
    rules = _rules(keys, tolerance)
    began = time.monotonic()
    (sa, ta), (sb, tb) = _read(runs, a, "A"), _read(runs, b, "B")
    _check_tolerances(rules, ta, tb)
    try:
        out = verify.compare(ta, tb, rules, _offset(offset), repeated_keys="stop",
                             status=_STATUS[status] if status else None, full=True)
    except verify.RepeatedKey as exc:
        return {"stopped": str(exc), **_sides(ta, tb, sa, sb)}
    except ValueError as exc:  # a key column that isn't in both: said as A and B, not "outputs"
        raise ValueError(str(exc).replace("both outputs", "both A and B")) from None
    return _shape(out, ta, tb, sa, sb, status, time.monotonic() - began)


def _sides(ta: pa.Table, tb: pa.Table, sa: dict[str, Any], sb: dict[str, Any]) -> dict[str, Any]:
    from .side_by_side import kind
    names_b = {f.name.lower(): f for f in tb.schema}
    return {"rows": {"a": ta.num_rows, "b": tb.num_rows},
            # The columns in both, for the key and tolerance pickers: A's names, and whether they're numbers.
            "common": [{"name": f.name, "kind": kind(f.type), "number": f.name.lower() in _numbers(ta) & _numbers(tb)}
                       for f in ta.schema if f.name.lower() in names_b],
            "runs": {"a": sa.get("id"), "b": sb.get("id")}}


def _shape(out: dict[str, Any], ta: pa.Table, tb: pa.Table, sa: dict[str, Any], sb: dict[str, Any],
           status: str | None, seconds: float) -> dict[str, Any]:
    counts = out.get("counts")
    shaped: dict[str, Any] = {
        "result": "same" if out["result"] == "match" else "differs", "level": out.get("level", "complete"),
        "method": out.get("method"), "keys": out.get("keys") or [], "seconds": round(seconds, 2),
        "columns": {"only_a": out["columns"]["only_pc"], "only_b": out["columns"]["only_fabric"],
                    "types": out["columns"]["types"]},
        "limitations": [t.replace("this PC", "A").replace("in Fabric", "in B") for t in out["limitations"]],
        "status": status, "offset": out.get("offset", 0), **_sides(ta, tb, sa, sb)}
    if counts is None:  # fingerprint: too many rows to say which differ
        shaped["counts"] = None
        shaped["examples"] = []
        return shaped
    changed = counts.get("changed", 0)
    shaped["counts"] = {"same": max(0, ta.num_rows - counts["only_pc"] - changed), "only_a": counts["only_pc"],
                        "only_b": counts["only_fabric"], "changed": changed if out.get("method") == "keyed" else None}
    shaped["changed_columns"] = [n for n, d in (out.get("column_diffs") or {}).items() if d.get("rows")]
    shaped["listed"] = (sum(v or 0 for k, v in shaped["counts"].items() if k != "same") if status is None
                        else shaped["counts"][status] or 0)
    if out.get("note"):
        shaped["note"] = out["note"].replace("Add key columns", "Match rows on key columns")
    shaped["examples"] = [_example(e) for e in out.get("examples") or []]
    return shaped


def _example(e: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {"status": _BACK[e["status"]]}
    if "key" in e:
        row["key"] = e["key"]
    if "row" in e:
        row |= {"row": e["row"], "times": e.get("times", 1)}
    for part in ("columns", "same"):
        if part in e:
            row[part] = {n: {"a": v["pc"], "b": v["fabric"]} for n, v in e[part].items()}
    return row


def export(runs: Any, a: Any, b: Any, keys: Any = None, tolerance: Any = None, name: str = "") -> dict[str, Any]:
    """Every row that differs, to a new CSV file in Downloads (never over a file). Reads two finished runs."""
    rules = _rules(keys, tolerance)
    (_sa, ta), (_sb, tb) = _read(runs, a, "A"), _read(runs, b, "B")
    _check_tolerances(rules, ta, tb)
    folder = runs._downloads()
    folder.mkdir(parents=True, exist_ok=True)
    from .sql_exec import _reserve
    path = _reserve(folder, f"{file_stem(str(name or '')) or 'Compare'} compare {time.strftime('%Y-%m-%d %H%M%S')}", ".csv")
    partial = path.with_name(path.name + ".partial")
    written = {"rows": 0, "protected": 0}

    def write(reader: Any) -> None:
        import pyarrow.csv as pa_csv
        schema = _export_schema(reader.schema, bool(rules["keys"]))
        header, written["protected"] = csv_cells.protect_header(csv_ready_schema(schema))
        with open(partial, "xb") as sink:
            writer = pa_csv.CSVWriter(sink, header)
            try:
                for batch in reader:
                    batch = pa.RecordBatch.from_arrays([_where(batch.column(0)), *batch.columns[1:]], schema=schema)
                    batch, protected = csv_cells.protect_batch(csv_ready_batch(batch))
                    written["protected"] += protected
                    writer.write_batch(batch)
                    written["rows"] += batch.num_rows
            finally:
                writer.close()

    try:
        out = verify.compare(ta, tb, rules, 0, repeated_keys="stop", export=write)
        if out.get("counts") is None:
            raise ValueError("A or B has too many rows to list the rows that differ. Only their checksums were compared.")
        if not written["rows"]:
            raise ValueError("No rows differ, so there are none to export.")
        jsonfile.replace(partial, path)
    except Exception as exc:
        _discard(partial, path)
        too_long = jsonfile.path_too_long(exc, path, "exported")
        if too_long:
            raise too_long from None
        raise
    notes = [csv_cells.note(written["protected"], can_keep_raw=False)] if written["protected"] else []
    return {"path": str(path), "name": path.name, "rows": written["rows"], "notes": notes}


def _export_schema(schema: pa.Schema, keyed: bool) -> pa.Schema:
    """The export's column names: Where (and, with no key, Times) first, then the key columns and each column's
    A: and B: values, or the row's columns. A name that clashes with one before it gets _2, as the SQL page's
    own export does, so a column of the table named Where or Times stays a column of its own."""
    from .sql_exec import _unique_names
    added = ["Where"] if keyed else ["Where", "Times"]
    names = added + schema.names[len(added):]
    return _unique_names(pa.schema([f.with_name(n) for f, n in zip(schema, names, strict=True)]))


def _where(column: Any) -> Any:
    """verify's names for where a row is (only_pc, only_fabric) as the page says them: only_a, only_b."""
    import pyarrow.compute as pc
    return pc.replace_substring_regex(pc.replace_substring_regex(column, "^only_pc$", "only_a"), "^only_fabric$", "only_b")


def _discard(*paths: Path) -> None:
    for p in paths:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
