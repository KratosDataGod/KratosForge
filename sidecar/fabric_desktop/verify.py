"""Verifying two outputs of the same table: explicit rules, explicit levels, the rows that differ.

A comparison says what it proved. Each table's result has a **level**:

* ``complete``: every row on both sides was compared. With **key columns**, rows are matched by
  key, and each column is compared under the rules (numbers within a tolerance, timestamps at a
  precision). Without keys, rows are matched as whole rows (a multiset: order doesn't matter,
  duplicates count), numbers rounded to the significant digits the tolerance allows.
* ``fingerprint``: the table was larger than the complete-comparison limit, so only row counts
  and an order-independent checksum were compared. Equal checksums make a difference very
  unlikely, not impossible; the result says so.

Rules (all optional):

``keys``        columns that identify a row (matched case-insensitively)
``ignore``      columns left out of the comparison
``abs`` ``rel`` the default numeric tolerance: equal when |a-b| <= abs or |a-b| <= rel*max(|a|,|b|)
                (both 0: integers and decimals are compared exactly, digit for digit, not as DOUBLEs)
``columns``     per-column overrides: {"amount": {"abs": 0.01}}
``timestamps``  compare timestamps to "us" (default), "ms" or "s"
``limit_rows``  above this many rows on either side, compare fingerprints only (default 5,000,000)

Delta tables have no row order, so order is never compared.
"""

from __future__ import annotations

import math
from typing import Any

import pyarrow as pa
from .duckdb_rows import one_row

DEFAULTS: dict[str, Any] = {"keys": [], "ignore": [], "abs": 0.0, "rel": 1e-9, "columns": {}, "timestamps": "us",
                            "limit_rows": 5_000_000, "examples": 50}
_TS_UNITS = {"us": "microseconds", "ms": "milliseconds", "s": "seconds"}
_NUMERIC = {"integer", "number"}


def rules(given: dict[str, Any] | None) -> dict[str, Any]:
    if given is not None and not isinstance(given, dict):
        raise ValueError('Comparison rules are a set of named rules, like {"keys": ["id"], "rel": 1e-9}.')
    r = dict(DEFAULTS) | {k: v for k, v in (given or {}).items() if v is not None}
    r["columns"] = dict(r["columns"]) if isinstance(r["columns"], dict) else r["columns"]  # never DEFAULTS' own dict
    unknown = set(r) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown comparison rule {', '.join(sorted(unknown))}. Rules: {', '.join(DEFAULTS)}.")
    if r["timestamps"] not in _TS_UNITS:
        raise ValueError("timestamps is us, ms or s")
    for k in ("abs", "rel"):
        r[k] = _tolerance_value(k, r[k])
    for k in ("limit_rows", "examples"):
        v = int(r[k]) if isinstance(r[k], float) and r[k].is_integer() else r[k]
        if isinstance(v, bool) or not (isinstance(v, int) or (isinstance(v, str) and v.strip().isdigit())) or int(v) < 0:
            raise ValueError(f"{k} is a whole number of rows, 0 or more, not {r[k]!r}.")
        r[k] = int(v)
    for k in ("keys", "ignore"):  # one column name, or a list of them; "id" is not ["i", "d"]
        names = [r[k]] if isinstance(r[k], str) else r[k]
        if not isinstance(names, (list, tuple)) or not all(isinstance(n, str) and n.strip() for n in names):
            raise ValueError(f"{k} is a list of column names.")
        r[k] = [n.strip() for n in names]
    if not isinstance(r["columns"], dict):
        raise ValueError('columns gives tolerances per column, like {"amount": {"abs": 0.01}}.')
    for column, over in r["columns"].items():
        if not isinstance(over, dict) or set(over) - {"abs", "rel"}:
            raise ValueError(f'The tolerance for {column} is abs and/or rel, like {{"abs": 0.01}}.')
        r["columns"][column] = {k: _tolerance_value(f"{k} for {column}", v) for k, v in over.items() if v is not None}
    return r


def _tolerance_value(name: str, value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} is a number, like 0.01 or 1e-9, not {value!r}.") from None
    if isinstance(value, bool) or not math.isfinite(v) or v < 0:
        raise ValueError(f"{name} is a number 0 or more, like 0.01 or 1e-9, not {value!r}.")
    return v


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _kind(t: pa.DataType) -> str:
    from .side_by_side import kind
    return kind(t)


def _sig_digits(rel: float) -> int:
    """Significant digits a relative tolerance leaves, for matching whole rows (at least 1, at most 15)."""
    if rel <= 0:
        return 17
    return max(1, min(15, int(math.floor(-math.log10(rel)))))


def _exact(expr: str) -> str:
    """An integer or decimal as text with no trailing zeros, so 1.50 (decimal(10,2)), 1.5000 and 15
    vs 15.0 compare exactly at any precision, without DOUBLE's 15-17 digits or a common decimal type."""
    s = f"CAST({expr} AS VARCHAR)"
    return f"CASE WHEN contains({s}, '.') THEN rtrim(rtrim({s}, '0'), '.') ELSE {s} END"


def _norm(expr: str, kind: str, r: dict[str, Any], digits: int | None = None, exact: bool = False) -> str:
    """A column expression both engines' values normalize to before whole-row matching. ``exact``:
    both sides are integers or decimals, compared exactly when no tolerance is allowed."""
    if kind == "integer":
        return f"CAST({expr} AS HUGEINT)"
    if kind == "number":
        x = f"CAST({expr} AS DOUBLE)"
        d = digits if digits is not None else _sig_digits(float(r["rel"]))
        if d >= 17:
            return _exact(expr) if exact else x
        return (f"CASE WHEN {x} IS NULL OR {x} = 0 OR NOT isfinite({x}) THEN {x} "
                f"ELSE round({x}, {d - 1} - CAST(floor(log10(abs({x}))) AS INTEGER)) END")
    if kind == "timestamp":
        return f"date_trunc('{_TS_UNITS[r['timestamps']][:-1]}', CAST({expr} AS TIMESTAMP))"
    if kind == "string":
        return expr
    return f"CAST({expr} AS VARCHAR)"


def _columns(schema_a: pa.Schema, schema_b: pa.Schema, r: dict[str, Any]) -> dict[str, Any]:
    """Columns matched by name (case-insensitive) less the ignored ones, and what doesn't line up.
    A column of whole numbers on one side and decimals on the other is ``widened``, not a type
    difference: engines differ there (an average, a cast), and its values are compared as numbers."""
    ignore = {c.lower() for c in r["ignore"]}
    a = {f.name.lower(): f for f in schema_a if f.name.lower() not in ignore}
    b = {f.name.lower(): f for f in schema_b if f.name.lower() not in ignore}
    common = [n for n in a if n in b]
    kinds = {n: (_kind(a[n].type), _kind(b[n].type)) for n in common}
    widened = [n for n in common if kinds[n][0] != kinds[n][1] and set(kinds[n]) == _NUMERIC]
    return {"common": [(a[n].name, b[n].name, *(("number", "number") if n in widened else kinds[n])) for n in common],
            "only_pc": sorted(a[n].name for n in a if n not in b),
            "only_fabric": sorted(b[n].name for n in b if n not in a),
            "types": sorted(f"{a[n].name} ({kinds[n][0]} vs {kinds[n][1]})" for n in common
                            if kinds[n][0] != kinds[n][1] and n not in widened),
            "widened": sorted(f"{a[n].name} ({kinds[n][0]} vs {kinds[n][1]})" for n in widened),
            "numbers": frozenset(widened),
            # Both sides integers or decimals: no tolerance means exactly equal, not equal as DOUBLEs.
            "exact": frozenset(n for n in common if all(pa.types.is_integer(t) or pa.types.is_decimal(t)
                                                        for t in (a[n].type, b[n].type)))}


def _tolerance(r: dict[str, Any], column: str) -> tuple[float, float]:
    over = {k.lower(): v for k, v in (r["columns"] or {}).items()}.get(column.lower()) or {}
    return float(over.get("abs", r["abs"])), float(over.get("rel", r["rel"]))


def _count(con: Any, sql: str) -> int:
    return int(one_row(con.execute(sql))[0] or 0)


def compare(pc: Any, fabric: Any, given_rules: dict[str, Any] | None = None, offset: int = 0) -> dict[str, Any]:
    """Compare two pyarrow tables or datasets (this PC's output and Fabric's) under ``given_rules``."""
    import duckdb

    from .side_by_side import fingerprint
    r = rules(given_rules)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError(f"offset is a whole number of rows to skip, 0 or more, not {offset!r}.")
    cols = _columns(pc.schema, fabric.schema, r)
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone = 'UTC'")
        con.register("a", pc)
        con.register("b", fabric)
        rows_a = _count(con, "SELECT count(*) FROM a")
        rows_b = _count(con, "SELECT count(*) FROM b")
        out: dict[str, Any] = {"rules": r, "pc": {"rows": rows_a}, "fabric": {"rows": rows_b},
                               "columns": {k: cols[k] for k in ("only_pc", "only_fabric", "types", "widened")},
                               "limitations": []}
        if cols["widened"]:
            out["limitations"].append(f"Whole numbers on one side and decimals on the other: {', '.join(cols['widened'])}. "
                                      "Their values were compared as numbers.")
        schema_same = not (cols["only_pc"] or cols["only_fabric"] or cols["types"])
        if max(rows_a, rows_b) > int(r["limit_rows"]):
            fa, fb = fingerprint(pc, cols["numbers"]), fingerprint(fabric, cols["numbers"])
            same = schema_same and fa["rows"] == fb["rows"] and fa["checksum"] == fb["checksum"]
            out |= {"level": "fingerprint", "method": "checksum", "result": "match" if same else "differs"}
            out["limitations"].append(
                f"More than {int(r['limit_rows']):,} rows, so only row counts and a checksum of every row were compared "
                "(numbers to 10 significant digits). Matching checksums make a difference very unlikely, not impossible; "
                "differing ones don't say which rows differ.")
            return out
        keys = r["keys"]
        if keys:
            lower = {c[0].lower(): c for c in cols["common"]}
            missing = [k for k in keys if k.lower() not in lower]
            if missing:
                raise ValueError(f"Key column {', '.join(missing)} isn't in both outputs (or is ignored).")
            kq = [(_q(lower[k.lower()][0]), _q(lower[k.lower()][1])) for k in keys]
            dup_a = _count(con, f"SELECT count(*) FROM (SELECT {', '.join(x for x, _ in kq)} FROM a GROUP BY ALL HAVING count(*) > 1)")
            dup_b = _count(con, f"SELECT count(*) FROM (SELECT {', '.join(y for _, y in kq)} FROM b GROUP BY ALL HAVING count(*) > 1)")
            if dup_a or dup_b:
                out["duplicates"] = {"pc": dup_a, "fabric": dup_b}
                out["limitations"].append(
                    f"The key ({', '.join(keys)}) isn't unique: {dup_a:,} repeated keys on this PC, {dup_b:,} in Fabric. "
                    "Rows were matched as whole rows instead.")
            else:
                # Keys of different types (text on one side, numbers on the other) are matched as text;
                # the type difference already makes the table differ.
                text = [lower[k.lower()][2] != lower[k.lower()][3] for k in keys]
                out |= _keyed(con, cols, kq, r, offset, text)
                out["result"] = "match" if schema_same and not (out["counts"]["only_pc"] or out["counts"]["only_fabric"]
                                                               or out["counts"]["changed"]) else "differs"
                return out
        out |= _whole_rows(con, cols, r, offset)
        out["result"] = "match" if schema_same and not (out["counts"]["only_pc"] or out["counts"]["only_fabric"]) else "differs"
        return out
    finally:
        con.close()


def _keyed(con: Any, cols: dict[str, Any], kq: list[tuple[str, str]], r: dict[str, Any], offset: int,
           text: list[bool]) -> dict[str, Any]:
    key_names = {x for x, _ in kq}
    keys = [(f"CAST(a.{x} AS VARCHAR)", f"CAST(b.{y} AS VARCHAR)") if t else (f"a.{x}", f"b.{y}")
            for (x, y), t in zip(kq, text)]
    on = " AND ".join(f"{x} IS NOT DISTINCT FROM {y}" for x, y in keys)
    checks, metrics = [], []
    for name_a, name_b, ka, kb in cols["common"]:
        qa, qb = _q(name_a), _q(name_b)
        if qa in key_names:
            continue
        if ka == kb == "number":  # widened integer/decimal pairs arrive as number
            ab, rl = _tolerance(r, name_a)
            x, y = f"CAST(a.{qa} AS DOUBLE)", f"CAST(b.{qb} AS DOUBLE)"
            if ab == rl == 0 and name_a.lower() in cols["exact"]:
                same = f"({_exact('a.' + qa)} IS NOT DISTINCT FROM {_exact('b.' + qb)})"
            else:
                same = (f"(({x} IS NULL AND {y} IS NULL) OR ({x} IS NOT NULL AND {y} IS NOT NULL AND "
                        f"((isfinite({x}) AND isfinite({y}) AND (abs({x} - {y}) <= {ab!r} OR "
                        f"abs({x} - {y}) <= {rl!r} * greatest(abs({x}), abs({y})))) "
                        f"OR (isnan({x}) AND isnan({y})) OR {x} = {y})))")  # the tolerance holds between finite numbers
            metrics.append((name_a, f"max(abs({x} - {y})) FILTER (WHERE NOT {same})",
                            f"max(abs({x} - {y}) / nullif(greatest(abs({x}), abs({y})), 0)) FILTER (WHERE NOT {same})"))
        elif ka == kb == "timestamp":
            same = f"({_norm('a.' + qa, ka, r)} IS NOT DISTINCT FROM {_norm('b.' + qb, kb, r)})"
            metrics.append((name_a, None, None))
        elif ka == kb:
            same = f"(a.{qa} IS NOT DISTINCT FROM b.{qb})"
            metrics.append((name_a, None, None))
        else:
            same = f"(CAST(a.{qa} AS VARCHAR) IS NOT DISTINCT FROM CAST(b.{qb} AS VARCHAR))"
            metrics.append((name_a, None, None))
        checks.append((name_a, qa, qb, same))
    con.execute(f"""CREATE TEMP TABLE j AS SELECT
        a."__fd_a_row" IS NULL AS no_a, b."__fd_b_row" IS NULL AS no_b,
        {', '.join(f'{x} AS "k_a{i}", {y} AS "k_b{i}"' for i, (x, y) in enumerate(keys))}
        {''.join(f', {same} AS "s{i}", a.{qa} AS "a{i}", b.{qb} AS "b{i}"' for i, (_n, qa, qb, same) in enumerate(checks))}
        FROM (SELECT *, 1 AS "__fd_a_row" FROM a) a FULL OUTER JOIN (SELECT *, 1 AS "__fd_b_row" FROM b) b ON {on}""")
    only_pc = _count(con, "SELECT count(*) FROM j WHERE no_b")
    only_fabric = _count(con, "SELECT count(*) FROM j WHERE no_a")
    any_diff = " OR ".join(f'NOT "s{i}"' for i in range(len(checks))) or "false"
    changed = _count(con, f"SELECT count(*) FROM j WHERE NOT no_a AND NOT no_b AND ({any_diff})")
    per_column = {}
    for i, (name, _qa, _qb, _same) in enumerate(checks):
        n = _count(con, f'SELECT count(*) FROM j WHERE NOT no_a AND NOT no_b AND NOT "s{i}"')
        entry: dict[str, Any] = {"rows": n}
        _m_name, max_abs, max_rel = metrics[i]
        if n and max_abs:
            mx = one_row(con.execute(f'SELECT max(abs(CAST("a{i}" AS DOUBLE) - CAST("b{i}" AS DOUBLE))), '
                             f'max(abs(CAST("a{i}" AS DOUBLE) - CAST("b{i}" AS DOUBLE)) / '
                             f'nullif(greatest(abs(CAST("a{i}" AS DOUBLE)), abs(CAST("b{i}" AS DOUBLE))), 0)) '
                             f'FROM j WHERE NOT no_a AND NOT no_b AND NOT "s{i}"'))
            if not (mx[0] == 0 and name.lower() in cols["exact"]):  # a decimal difference DOUBLE can't show
                entry |= {"max_abs": _finite(mx[0]), "max_rel": _finite(mx[1])}
        per_column[name] = entry
    limit = int(r["examples"])
    key_cols = ", ".join(f'coalesce("k_a{i}", "k_b{i}") AS "key{i}"' for i in range(len(kq)))
    value_cols = "".join(f', "s{i}", "a{i}", "b{i}"' for i in range(len(checks)))
    rows = con.execute(f"""SELECT no_a, no_b, {key_cols}{value_cols} FROM j
        WHERE no_a OR no_b OR ({any_diff}) ORDER BY {', '.join(f'"key{i}"' for i in range(len(kq)))} NULLS FIRST
        LIMIT {limit} OFFSET {int(offset)}""").fetchall()
    examples = []
    key_names_out = [k for k in r["keys"]]
    for row in rows:
        no_a, no_b = row[0], row[1]
        key = {key_names_out[i]: _json(row[2 + i]) for i in range(len(kq))}
        base = 2 + len(kq)
        if no_a or no_b:
            examples.append({"key": key, "status": "only_fabric" if no_a else "only_pc"})
            continue
        diffs = {}
        for i, (name, *_rest) in enumerate(checks):
            same, va, vb = row[base + 3 * i], row[base + 3 * i + 1], row[base + 3 * i + 2]
            if not same:
                diffs[name] = {"pc": _json(va), "fabric": _json(vb)}
        examples.append({"key": key, "status": "changed", "columns": diffs})
    return {"level": "complete", "method": "keyed", "keys": r["keys"],
            "counts": {"only_pc": only_pc, "only_fabric": only_fabric, "changed": changed},
            "column_diffs": per_column, "examples": examples, "offset": int(offset)}


def _whole_rows(con: Any, cols: dict[str, Any], r: dict[str, Any], offset: int) -> dict[str, Any]:
    """Rows matched as whole rows: each distinct normalized row and how many times each side has it."""
    pairs = cols["common"]
    if not pairs:
        na, nb = _count(con, "SELECT count(*) FROM a"), _count(con, "SELECT count(*) FROM b")
        return {"level": "complete", "method": "whole rows", "counts": {"only_pc": max(na - nb, 0), "only_fabric": max(nb - na, 0)},
                "examples": [], "offset": int(offset)}
    # A tolerance per column can't apply to whole rows: the default relative tolerance sets the digits.
    ea = ", ".join(f"{_norm(_q(x), ka if ka == kb else 'other', r, exact=x.lower() in cols['exact'])} AS c{i}"
                   for i, (x, _y, ka, kb) in enumerate(pairs))
    eb = ", ".join(f"{_norm(_q(y), kb if ka == kb else 'other', r, exact=x.lower() in cols['exact'])} AS c{i}"
                   for i, (x, y, ka, kb) in enumerate(pairs))
    names = ", ".join(f"c{i}" for i in range(len(pairs)))
    on = " AND ".join(f"ga.c{i} IS NOT DISTINCT FROM gb.c{i}" for i in range(len(pairs)))
    con.execute(f"""CREATE TEMP TABLE w AS
        WITH ga AS (SELECT {names}, count(*) AS n FROM (SELECT {ea} FROM a) GROUP BY ALL),
             gb AS (SELECT {names}, count(*) AS n FROM (SELECT {eb} FROM b) GROUP BY ALL)
        SELECT {', '.join(f'coalesce(ga.c{i}, gb.c{i}) AS c{i}' for i in range(len(pairs)))},
               coalesce(ga.n, 0) AS na, coalesce(gb.n, 0) AS nb
        FROM ga FULL OUTER JOIN gb ON {on}""")
    only_pc = _count(con, "SELECT coalesce(sum(na - nb), 0) FROM w WHERE na > nb")
    only_fabric = _count(con, "SELECT coalesce(sum(nb - na), 0) FROM w WHERE nb > na")
    rows = con.execute(f"SELECT {names}, na, nb FROM w WHERE na <> nb ORDER BY {names} NULLS FIRST "
                       f"LIMIT {int(r['examples'])} OFFSET {int(offset)}").fetchall()
    examples = [{"row": {pairs[i][0]: _json(v) for i, v in enumerate(row[:len(pairs)])},
                 "status": "only_pc" if row[-2] > row[-1] else "only_fabric", "times": abs(row[-2] - row[-1])}
                for row in rows]
    out = {"level": "complete", "method": "whole rows", "counts": {"only_pc": only_pc, "only_fabric": only_fabric},
           "examples": examples, "offset": int(offset)}
    if any(k == "number" for _x, _y, k, _k2 in pairs):
        out["note"] = (f"Rows matched as whole rows, numbers rounded to {_sig_digits(float(r['rel']))} significant digits. "
                       "Add key columns to see which values changed.")
    return out


def _finite(v: Any) -> float | None:
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else float(v)


def _json(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    return str(v)
