"""Academy exercises in Databricks SQL, run on this PC (the ``dbsql_lab`` adapter, DBA-004).

The learner writes Databricks SQL. It is translated to DuckDB SQL by the same path the DuckDB
engine's Databricks SQL cells take (engines/local_sql._databricks: sqlglot's Databricks dialect plus
KratosForge's Spark fixes, so substring, casts, dates and column names answer as Databricks does) and
then checked exactly as a ``duckdb_lab`` answer is: a fresh in-memory database built from the
exercise's setup, no file or network access, a memory limit, locked settings, a transaction that is
always rolled back and a time limit (academy/lab.py). Translated SQL runs in UTC, as a warehouse does
unless a session changes it.

Two checks read a table rather than the answer's last statement, because a Databricks answer often
ends with a write: ``result_matches`` with ``table`` compares the table the answer leaves with the
one the reference solution leaves, and ``idempotent`` runs the answer twice and compares again, so
INSERT INTO where CREATE OR REPLACE TABLE was needed fails. A check with ``"data": "hidden"`` runs on
``grading/<id>/hidden.sql`` instead of the setup, so an answer that hard-codes the practice rows fails.

A pass is LOCAL VERIFIED on DuckDB: it says nothing about a SQL warehouse, Photon or Unity Catalog.
``unproven()`` lists the functions an exercise's reference solution uses that the Databricks SQL
parity table (engines/databricks_sql_parity.py) hasn't shown to answer the same here, and
exercises.problems() refuses such an exercise, so a pass never rests on a function that may differ.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .. import sql
from ..engines import databricks_sql_parity
from ..engines.local_sql import _databricks
from ..learn import checks
from . import exercises, registry

ZONE = "UTC"
# sqlglot reads an answer before the lab's time limit starts, so a longer one isn't read at all.
MAX_ANSWER = 100_000
# A failure on hidden data never echoes DuckDB's error: a crafted cast could put the hidden rows in it.
HIDDEN_FAILED = "Your SQL failed on the check's own data; run it on the practice data to see why."
_ERROR_DETAILS = ("Your SQL failed", "Your SQL didn't run", "Your query didn't run")
# What the parity table doesn't list but every exercise needs: casts and the standard aggregates,
# which the SQL page's own tests cover (tests/test_sql_*.py) and which take no Spark-only arguments.
BASE = frozenset({"cast", "try_cast", "count", "sum", "avg", "min", "max"})
_CASE_NAME = re.compile(r"\s*\(.*\)$")
# A call as Databricks writes it: name( ... ). CASE, LIKE and the operators are syntax, not functions.
_CALL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(")


def _proven() -> frozenset[str]:
    return frozenset(_CASE_NAME.sub("", c.function).strip().lower()
                     for c in databricks_sql_parity.CASES if c.outcome == "same")


PROVEN = _proven() | BASE


def translate(text: str) -> str:
    """The Databricks SQL ``text`` as DuckDB SQL, statement by statement. A ValueError in words for a
    learner when it can't be read or has no DuckDB form (nothing then runs)."""
    if len(text) > MAX_ANSWER:
        raise ValueError(f"This SQL can't run on this PC: it is longer than {MAX_ANSWER:,} characters.")
    try:
        statements = _databricks(text)
        return ";\n".join(sql.duckdb_sql(s) for s in statements)
    except ValueError as exc:
        raise ValueError(f"This SQL can't run on this PC: {exc}") from None
    except RecursionError:
        raise ValueError("This SQL can't run on this PC: it is nested too deeply to read.") from None


def functions(text: str) -> set[str]:
    """The functions ``text`` calls, by the names Databricks SQL writes them with."""
    try:
        statements = [s for s in sqlglot.parse(text, read="databricks") if s is not None]
    except ParseError:
        return set()
    names = (_CALL.match(f.sql("databricks")) for s in statements for f in s.find_all(exp.Func)
             if not isinstance(f, (exp.Case, exp.If)))   # CASE (and if(), which it may print as) is proven
    return {m.group(1).lower() for m in names if m}


def unproven(text: str) -> list[str]:
    """The functions in ``text`` the parity table hasn't shown to answer the same on this PC."""
    return sorted(functions(text) - PROVEN)


def connect(setup: str | None, memory_limit: str | None = None) -> duckdb.DuckDBPyConnection:
    """A new in-memory database built from ``setup`` (DuckDB SQL, the exercise's own), in UTC, with no
    file or network access and locked settings."""
    con = duckdb.connect(":memory:")
    if setup:
        con.execute(setup)
    con.execute(f"SET TimeZone = '{ZONE}'")
    con.execute("SET enable_external_access = false")
    if memory_limit:
        con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute("SET lock_configuration = true")
    return con


def _ident(table: str) -> str:
    return ".".join('"' + part.replace('"', '""') + '"' for part in table.split("."))


def _table_rows(con: duckdb.DuckDBPyConnection, statements: list[str], table: str) -> tuple[list[str], list[Any]]:
    """The table ``table`` after running each of ``statements`` in turn, in one transaction that is
    rolled back."""
    con.begin()
    try:
        for s in statements:
            con.execute(s)
        cur = con.execute(f"SELECT * FROM {_ident(table)}")
        cols = [d[0] for d in cur.description or []]
        return cols, cur.fetchmany(checks.MAX_ROWS + 1)
    finally:
        con.rollback()


def _no(kind: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "passed": False, "detail": detail}


def _compare(kind: str, table: str, got: tuple[list[str], list[Any]], want: tuple[list[str], list[Any]],
             again: bool) -> dict[str, Any]:
    (cols, rows), (want_cols, want_rows) = got, want
    after = "After running your SQL twice, " if again else ""
    if len(rows) > checks.MAX_ROWS:
        return _no(kind, f"{after}{table} has more than {checks.MAX_ROWS:,} rows; the answer has {len(want_rows):,}.")
    if [c.lower() for c in cols] != [c.lower() for c in want_cols]:
        return _no(kind, f"{after}{table} has columns {', '.join(cols) or 'none'}; it should have {', '.join(want_cols)}.")
    have, need = sorted(map(repr, map(checks._norm, rows))), sorted(map(repr, map(checks._norm, want_rows)))
    if len(have) != len(need):
        return _no(kind, f"{after}{table} has {len(have):,} rows; it should have {len(need):,}.")
    if have != need:
        missing = sum((Counter(need) - Counter(have)).values())
        return _no(kind, f"{after}{missing:,} of the {len(need):,} rows in {table} differ from the answer's.")
    if again:
        return {"kind": kind, "passed": True, "detail": f"Run twice, {table} still has the same {len(need):,} rows."}
    return {"kind": kind, "passed": True, "detail": f"{table} has the same {len(need):,} rows as the answer's."}


def run_check(con: duckdb.DuckDBPyConnection, c: dict[str, Any], answer: str, solution: str) -> dict[str, Any]:
    """One check of the translated ``answer`` against the translated ``solution`` (both DuckDB SQL)."""
    kind, table = c["kind"], c.get("table")
    if not (table and kind in ("result_matches", "idempotent")):
        return exercises.run_check(con, c, answer, solution)
    try:
        checks.guard(con, answer)
    except ValueError as exc:
        return _no(kind, f"Your SQL wasn't run: {exc}.")
    want = _table_rows(con, [solution], table)
    try:
        got = _table_rows(con, [answer] * (2 if kind == "idempotent" else 1), table)
    except duckdb.Error as exc:
        first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return _no(kind, f"Your SQL failed {'the second time it ran' if kind == 'idempotent' else 'to run'}: {first}")
    return _compare(kind, table, got, want, again=kind == "idempotent")


def reference(pack_id: str, exercise_id: str, root: Path = registry.ROOT) -> dict[str, Any]:
    """exercises.reference() plus the hidden data (``hidden``: DuckDB SQL or None)."""
    ref = exercises.reference(pack_id, exercise_id, root)
    hidden = root / pack_id / "grading" / exercise_id / "hidden.sql"
    return {**ref, "hidden": hidden.read_text(encoding="utf-8") if hidden.is_file() else None}


class _NoLimit:
    fired = False


def _no_limit(_con: duckdb.DuckDBPyConnection) -> AbstractContextManager[Any]:
    return nullcontext(_NoLimit())


def check_all(ex: dict[str, Any], ref: dict[str, Any], answer: str,
              connect_to: Callable[[str | None], duckdb.DuckDBPyConnection] = connect,
              limit: Callable[[duckdb.DuckDBPyConnection], AbstractContextManager[Any]] = _no_limit,
              stopped: str = "") -> list[dict[str, Any]]:
    """Every check of ``ex`` on the learner's Databricks SQL ``answer``. ``connect_to(setup)`` opens a
    locked database (academy/lab.py passes its own, with the lab memory limit) and ``limit(con)`` is
    the time limit around each check; a check it stops fails with ``stopped``."""
    solution = translate(ref["solution"])   # the pack's own SQL: a failure here is a broken exercise
    try:
        duck = translate(answer)
    except ValueError as exc:
        return [_message(c, _no(c["kind"], str(exc))) for c in ex["validation"]]
    results = []
    cons: dict[str, duckdb.DuckDBPyConnection] = {}
    try:
        for c in ex["validation"]:
            data = c.get("data") or "practice"
            if data not in cons:
                cons[data] = connect_to(ref["hidden"] if data == "hidden" else ref["setup"])
            with limit(cons[data]) as stop:
                r = run_check(cons[data], c, duck, solution)
            if stop.fired:
                r = _no(c["kind"], stopped)
            elif data == "hidden" and not r["passed"] and str(r.get("detail", "")).startswith(_ERROR_DETAILS):
                r = _no(c["kind"], HIDDEN_FAILED)
            results.append(_message(c, r))
    finally:
        for con in cons.values():
            con.close()
    return results


def _message(c: dict[str, Any], r: dict[str, Any]) -> dict[str, Any]:
    message = c.get("pass") if r["passed"] else c.get("fail")
    return {**r, "message": message} if message else r


def grade(ex: dict[str, Any], ref: dict[str, Any], answer: str) -> dict[str, Any]:
    results = check_all(ex, ref, answer)
    return {"passed": all(r["passed"] for r in results), "results": results}


def self_test(pack_id: str, exercise_id: str, root: Path = registry.ROOT) -> dict[str, Any]:
    """The reference solution must pass and every planted wrong answer must fail."""
    ex = exercises.get(pack_id, exercise_id, root)
    ref = reference(pack_id, exercise_id, root)
    return {"solution": grade(ex, ref, ref["solution"]),
            "wrong": {name: grade(ex, ref, text) for name, text in ref["wrong"].items()}}
