"""Learn checks (LX-03): what a lab or exercise asks for, checked on this PC.

Each check returns ``{kind, passed, detail}``: ``detail`` says in words why it passed or failed, so the
learner knows what to fix, never just a red cross. The learner's SQL runs inside a transaction that is
always rolled back, on a connection the lab runner opens without file or network access, so a check
can't change the lab's data or reach anything outside it. Nothing is guessed: a check that can't run
says so and doesn't pass.

Kinds (the names content.CHECK_KINDS lists):

    result_matches  the learner's query gives the reference answer (``ordered``: in the same order too)
    table_shape     a table the learner made has these columns and types, unique keys, no NULLs
    integrity       every row of a child table finds its parent (no orphans)
    no_findings     the learner's code has no finding of the named best-practice rules
    faster_than     the learner's query gives the reference answer at least ``factor`` times faster than
                    the baseline: fastest of ``runs`` runs each, taking turns after one to warm up, on this PC
    defects_found   a query listing the keys the learner flagged finds every seeded defect of the kinds
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from typing import Any

import duckdb

from .. import notebook_practices
from ..sqlcompat import practices as sql_practices
from ..duckdb_rows import one_row

MAX_ROWS = 1_000_000   # a result bigger than this isn't compared row by row


def guard(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    """Refuse an answer that starts or ends a transaction: a COMMIT inside it would keep its changes
    in the lab, past the rollback every check ends with. SQL that doesn't parse is left to fail when
    it runs, with DuckDB's own message."""
    try:
        statements = con.extract_statements(sql)
    except duckdb.Error:
        return
    if any(st.type == duckdb.StatementType.TRANSACTION for st in statements):
        raise ValueError("a lab answer can't use BEGIN, COMMIT or ROLLBACK; the lab undoes every answer itself")


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    guard(con, sql)
    con.begin()
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(MAX_ROWS + 1)
    finally:
        con.rollback()
    if len(rows) > MAX_ROWS:
        raise ValueError(f"the query returns more than {MAX_ROWS:,} rows; a lab answer is smaller")
    return cols, rows


def _ok(kind: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "passed": True, "detail": detail}


def _no(kind: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "passed": False, "detail": detail}


def _norm(row: tuple[Any, ...]) -> tuple[Any, ...]:
    """Decimals and floats compare by value, so 120.5 and Decimal('120.50') are the same answer."""
    return tuple(float(v) if hasattr(v, "as_tuple") or isinstance(v, float) else v for v in row)


def result_matches(con: duckdb.DuckDBPyConnection, sql: str, reference: str | None = None,
                   expected: dict[str, Any] | None = None, ordered: bool = False) -> dict[str, Any]:
    kind = "result_matches"
    try:
        cols, rows = _rows(con, sql)
    except (duckdb.Error, ValueError) as exc:
        return _no(kind, f"Your query didn't run: {exc}")
    if expected is not None:
        want_cols, want = list(expected["columns"]), [tuple(r) for r in expected["rows"]]
    elif reference is not None:
        want_cols, want = _rows(con, reference)
    else:
        return _no(kind, "This check has no reference answer, so it can't pass.")
    if [c.lower() for c in cols] != [c.lower() for c in want_cols]:
        return _no(kind, f"The columns are {', '.join(cols) or 'none'}; the question asks for {', '.join(want_cols)}.")
    got, exp = [_norm(r) for r in rows], [_norm(r) for r in want]
    if len(got) != len(exp):
        return _no(kind, f"{len(got):,} rows; the answer has {len(exp):,}.")
    if Counter(map(repr, got)) != Counter(map(repr, exp)):
        missing = sum((Counter(map(repr, exp)) - Counter(map(repr, got))).values())
        return _no(kind, f"{missing:,} of the {len(exp):,} rows differ from the answer.")
    if ordered and list(map(repr, got)) != list(map(repr, exp)):
        return _no(kind, "The right rows, in the wrong order.")
    return _ok(kind, f"{len(got):,} rows, the same as the answer{' and in the same order' if ordered else ''}.")


def table_shape(con: duckdb.DuckDBPyConnection, table: str, columns: dict[str, str] | None = None,
                unique: list[str] | None = None, not_null: list[str] | None = None) -> dict[str, Any]:
    kind = "table_shape"
    have = {r[0].lower(): r[1].upper() for r in con.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE lower(table_name) = lower(?)", [table]).fetchall()}
    if not have:
        return _no(kind, f"There's no table {table} yet.")
    for name, typ in (columns or {}).items():
        if name.lower() not in have:
            return _no(kind, f"{table} has no column {name}.")
        if typ and not have[name.lower()].startswith(typ.upper()):
            return _no(kind, f"{table}.{name} is {have[name.lower()]}; it should be {typ.upper()}.")
    ident = '"' + table.replace('"', '""') + '"'
    for col in not_null or []:
        n = one_row(con.execute(f'SELECT count(*) FROM {ident} WHERE "{col}" IS NULL'))[0]  # type: ignore[index]
        if n:
            return _no(kind, f"{table}.{col} has {n:,} empty (NULL) values.")
    if unique:
        key = ", ".join(f'"{c}"' for c in unique)
        n = one_row(con.execute(f"SELECT count(*) FROM (SELECT {key} FROM {ident} GROUP BY ALL HAVING count(*) > 1)"))[0]  # type: ignore[index]
        if n:
            return _no(kind, f"{n:,} values of {', '.join(unique)} appear more than once in {table}.")
    return _ok(kind, f"{table} has the columns, types and keys it needs.")


def integrity(con: duckdb.DuckDBPyConnection, child: str, column: str, parent: str,
              parent_column: str | None = None) -> dict[str, Any]:
    kind = "integrity"
    pc = parent_column or column
    n = one_row(con.execute(f'SELECT count(*) FROM "{child}" c WHERE c."{column}" IS NOT NULL AND NOT EXISTS '
                    f'(SELECT 1 FROM "{parent}" p WHERE p."{pc}" = c."{column}")'))[0]  # type: ignore[index]
    if n:
        return _no(kind, f"{n:,} rows of {child} have a {column} that {parent} doesn't have.")
    return _ok(kind, f"Every {child} row finds its {parent}.")


def no_findings(code: str, rules: list[str], language: str = "sql") -> dict[str, Any]:
    kind = "no_findings"
    if language == "sql":
        found = sql_practices.check(code)["findings"]
    else:
        found = notebook_practices.check([code])["findings"]
    hits = [f for f in found if f["rule_id"] in set(rules)]
    if hits:
        f = hits[0]
        return _no(kind, f"{f['rule_id']} {f['rule']}: {f['description']}")
    return _ok(kind, f"No {', '.join(rules)} findings.")


def faster_than(con: duckdb.DuckDBPyConnection, sql: str, baseline: str, factor: float = 2.0, runs: int = 3,
                clock: Callable[[], float] = time.perf_counter) -> dict[str, Any]:
    kind = "faster_than"
    same = result_matches(con, sql, reference=baseline)
    if not same["passed"]:
        return _no(kind, f"Speed counts only with the same answer. {same['detail']}")

    # Turn about, and keep each query's fastest run: on a busy PC (another program, a shared CI
    # runner) a pause only ever adds time, and taking turns spreads a slow spell over both queries,
    # so a correct answer isn't failed by what else the PC was doing.
    _rows(con, baseline)   # once each to warm up: the first run also reads the data from disk
    _rows(con, sql)
    fastest = [float("inf"), float("inf")]
    for _ in range(max(1, runs)):
        for i, q in enumerate((baseline, sql)):
            start = clock()
            _rows(con, q)
            fastest[i] = min(fastest[i], clock() - start)
    base, mine = fastest
    speedup = base / mine if mine > 0 else float("inf")
    detail = (f"{speedup:.1f}× faster: {mine:.2f} s against {base:.2f} s (fastest of {runs} runs each, taking turns, on this PC). "
              "Fabric timings differ; this is measured here only.")
    measured = {"seconds": round(mine, 4), "baseline_seconds": round(base, 4), "speedup": round(speedup, 2) if mine > 0 else None}
    out = _ok(kind, detail) if speedup >= factor else _no(kind, f"{detail} The goal is {factor:g}×.")
    return {**out, "measured": measured}


def defects_found(con: duckdb.DuckDBPyConnection, sql: str, defects: list[dict[str, Any]],
                  kinds: list[str] | None = None) -> dict[str, Any]:
    """``sql`` returns one row per problem the learner found: (table, key). Each seeded defect of
    ``kinds`` (all when None) must be among them."""
    kind = "defects_found"
    try:
        _, rows = _rows(con, sql)
    except (duckdb.Error, ValueError) as exc:
        return _no(kind, f"Your query didn't run: {exc}")
    flagged = {(str(r[0]).lower(), str(r[1])) for r in rows if len(r) >= 2}
    want = [d for d in defects if kinds is None or d["kind"] in kinds]
    missed = [d for d in want if (d["table"], str(d["key"])) not in flagged]
    if missed:
        return _no(kind, f"{len(want) - len(missed)} of the {len(want)} problems found. Still hidden: "
                         f"{len(missed)} in {', '.join(sorted({d['table'] for d in missed}))}.")
    return _ok(kind, f"All {len(want)} problems found.")
