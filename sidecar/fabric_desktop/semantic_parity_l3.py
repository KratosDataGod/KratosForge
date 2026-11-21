"""Behavioral semantic parity, SQL side (L3, OSSIE-006c): what does each side's measure return on the same data?

``parity_l3(left, right)`` is the L2 report (``semantic_parity.parity_l2``), every row of it, with each measure or
metric that L2 could read on a side also *run* on that side, when it is SQL, against the same small controlled
datasets (``SCENARIOS``): plain values, NULLs, a column of NULLs only, no rows, repeated values, zeros (a zero divisor,
empty text, DAX's zero date) and precision (decimal fractions, integers past 2^53). The datasets are generated here
from the tables and columns the measures name, typed from each side's own column types, so a type change between
the sides is part of what is run. They are never customer data, and nothing is read from or written to disk.

What runs and what doesn't (plan D.5):

- A SQL side runs on DuckDB, in memory, with external access off. The query is built from L2's canonical form of the
  expression (an aggregate of one column, COUNT(*), one division, measure references), never from the expression's
  own text, so only the subset L2 accepts can run and no text from the input reaches DuckDB except quoted names.
- A DAX side never runs: this PC has no DAX engine, and DuckDB is not one. It is recorded as not run, with the
  reason, and a DAX-side result never appears in a report (``_check_no_dax_result`` refuses one). OSSIE-006d runs
  the DAX side in ``kf_tenant_test``.
- Relationships, filter context and time intelligence aren't exercised: the subset aggregates whole tables, so
  nothing crosses a relationship (scenarios 8, 9 and 10 need the live DAX side).

Results, per measure row:

- both sides SQL and both ran: **Verified equivalent** when every dataset gives the same value on both sides,
  **Equivalent within tolerance** when some differ within the tolerances, **Different results** otherwise (the
  datasets and both values are in ``changes``). Verified means on these datasets, both run on DuckDB, which the
  row's notes say: each side's own engine can still differ (DuckDB gives 1/0 = inf where T-SQL raises an error).
- one side DAX: the row keeps its L2 result (at best **Structurally compatible but behavior unverified**); the SQL
  side's values are evidence for the 006d comparison, never a match.
- a row L1 or L2 already found worse (Different results, Unsupported, Test failed) keeps that result; L3 never
  improves on it, but different values still mark the row Different results (never a Test failed row). A SQL
  query that DuckDB refuses on a dataset never verifies. Refused on both sides, the row keeps its L2 result with a
  note. Refused on one side only, the row is Different results. SUM and AVERAGE over a text, Boolean, date or time
  column aren't run (DuckDB's type rules there aren't DAX's or T-SQL's).

Tolerances are configuration (``DEFAULT_TOLERANCES_L3``, stated in the report): ``absolute`` and ``relative``, a
pair of numbers is within tolerance when ``|a - b| <= max(absolute, relative * max(|a|, |b|))``. Exact equality is
always Verified; the defaults allow only floating-point noise.

Determinism: the same inputs give the same report (the datasets are fixed, DuckDB runs one thread, and values are
compared as text of exact decimals).

Security: as at L2, pass a report through ``redacted`` before it leaves the local pane; it also replaces each built
query with a length and hash.
"""

from __future__ import annotations

import contextlib
import datetime
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from . import model_ossie as mo, semantic_parity as sp

LEVEL_L3 = "L3"
DEFAULT_TOLERANCES_L3: dict[str, float] = {"absolute": 0.0, "relative": 1e-9}
MAX_MEASURE_DEPTH = 8  # measure references followed on one side before L3 gives up (a cycle, or a long chain)
MAX_MEASURES_RUN = 200  # measures run per side; the rest are noted, never reported as passing
# A referenced measure is computed once per dataset and read by every reference, so a chain of measures that each
# use the one before twice stays linear. Aggregates and row counts per measure and per side are capped as well, so one
# crafted model can't make L3 run for minutes: past a cap a measure is not run, with the reason.
MAX_TERMS_PER_MEASURE = 64
MAX_TERMS_PER_SIDE = 2000
NOT_CHECKED_L3 = (
    {"what": "the DAX side", "level": "L3", "story": "OSSIE-006d",
     "reason": "no DAX engine runs on this PC, and DuckDB runs SQL only, so a DAX side is recorded as not run"},
    {"what": "relationships, filter context and time intelligence", "level": "L3", "story": "OSSIE-006d",
     "reason": "the L2 subset aggregates whole tables, so nothing crosses a relationship or a filter"},
    {"what": "performance", "level": "L4", "story": "OSSIE-006", "reason": "not measured until a live baseline exists"},
)
_DAX_NOT_RUN = "not run: this PC has no DAX engine (OSSIE-006d runs the DAX side in kf_tenant_test)"
_ON_DATASETS = ("Verified on the controlled datasets only, both run on DuckDB (L3, SQL side). Other data, or each "
                "side's own engine (a zero divisor, integer division, NULL order), can still give different results")

# Value classes by a side's column type (TMSL dataType or Ossie data_type, casefolded) -> (class, DuckDB type).
_TYPES: dict[str, tuple[str, str]] = {
    "int64": ("int", "BIGINT"), "integer": ("int", "BIGINT"), "int": ("int", "BIGINT"), "bigint": ("int", "BIGINT"),
    "decimal": ("dec", "DECIMAL(19,4)"), "currency": ("dec", "DECIMAL(19,4)"),
    "double": ("num", "DOUBLE"), "float": ("num", "DOUBLE"), "number": ("num", "DOUBLE"), "real": ("num", "DOUBLE"),
    "string": ("text", "VARCHAR"), "text": ("text", "VARCHAR"), "varchar": ("text", "VARCHAR"),
    "boolean": ("bool", "BOOLEAN"), "bool": ("bool", "BOOLEAN"),
    "datetime": ("time", "TIMESTAMP"), "timestamp": ("time", "TIMESTAMP"), "date": ("date", "DATE"),
}
_N = None
# Each scenario: its values per class, the same length in every class, so the columns of one table line up.
SCENARIOS: dict[str, dict[str, list[str | None]]] = {
    "basic": {"int": ["1", "2", "3", "4", "5"], "dec": ["1.5", "2", "3.25", "4", "5"],
              "num": ["1.5", "2", "3.25", "4", "5"], "text": ["a", "b", "c", "d", "e"],
              "bool": ["true", "false", "true", "false", "true"],
              "time": ["2024-01-01 00:00:00", "2024-01-02 12:00:00", "2024-01-03 00:00:00", "2024-01-04 00:00:00",
                       "2024-01-05 23:59:59"],
              "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]},
    "nulls": {"int": ["1", _N, "3", _N, "5"], "dec": ["1.5", _N, "3.25", _N, "5"], "num": ["1.5", _N, "3.25", _N, "5"],
              "text": ["a", _N, "c", _N, "e"], "bool": ["true", _N, "false", _N, "true"],
              "time": ["2024-01-01 00:00:00", _N, "2024-01-03 00:00:00", _N, "2024-01-05 00:00:00"],
              "date": ["2024-01-01", _N, "2024-01-03", _N, "2024-01-05"]},
    "all_null": {k: [_N] * 4 for k in ("int", "dec", "num", "text", "bool", "time", "date")},
    "no_rows": {k: [] for k in ("int", "dec", "num", "text", "bool", "time", "date")},
    "repeats": {"int": ["2", "2", "2", "5", "5"], "dec": ["2.5", "2.5", "2.5", "5", "5"],
                "num": ["2.5", "2.5", "2.5", "5", "5"], "text": ["a", "a", "a", "b", "b"],
                "bool": ["true", "true", "true", "false", "false"],
                "time": ["2024-01-01 00:00:00", "2024-01-01 00:00:00", "2024-01-01 00:00:00", "2024-01-02 00:00:00",
                         "2024-01-02 00:00:00"],
                "date": ["2024-01-01", "2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"]},
    "zeros": {"int": ["0", "0", "0"], "dec": ["0", "0", "0"], "num": ["0", "0", "0"], "text": ["", "", ""],
              "bool": ["false", "false", "false"], "time": ["1899-12-30 00:00:00"] * 3, "date": ["1899-12-30"] * 3},
    "precision": {"int": ["9007199254740993", "1", "-1"], "dec": ["0.1", "0.2", "0.3"], "num": ["0.1", "0.2", "0.3"],
                  "text": ["a", "A", "a "], "bool": ["true", "false", "false"],
                  "time": ["2024-02-29 23:59:59.999", "1900-01-01 00:00:00", "9999-12-31 00:00:00"],
                  "date": ["2024-02-29", "1900-01-01", "9999-12-31"]},
}
_SQL_AGG = {"SUM": "SUM({})", "AVERAGE": "AVG({})", "MIN": "MIN({})", "MAX": "MAX({})", "COUNT": "COUNT({})",
            "DISTINCTCOUNTNOBLANK": "COUNT(DISTINCT {})"}
_NUMERIC_AGG = frozenset({"SUM", "AVERAGE"})  # DuckDB sums a Boolean; DAX and T-SQL refuse it, so it isn't run
_NUMERIC_CLASSES = frozenset({"int", "dec", "num"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class _NotRun(Exception):
    """A SQL side L3 can't run; the message is the reason (names only)."""


def _q(name: str) -> str:
    if _CONTROL.search(name):
        raise _NotRun(f"the name {mo._q(name)} holds a control character")
    return '"' + name.replace('"', '""') + '"'


def _tolerances(value: Any) -> dict[str, Any]:
    values = dict(DEFAULT_TOLERANCES_L3)
    ignored: list[str] = []
    if value is not None and not isinstance(value, dict):
        ignored.append(f"(not a mapping: {type(value).__name__})")
    elif value:
        for k, v in value.items():
            if k in DEFAULT_TOLERANCES_L3 and isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and math.isfinite(v) and v >= 0:
                values[k] = float(v)
            elif len(ignored) < 20:
                ignored.append(mo._short(k))
    return {"values": values, "defaults": dict(DEFAULT_TOLERANCES_L3), "used": False, "ignored": ignored,
            "note": "used to compare two SQL sides' values at L3; a DAX side never runs, so never compared"}


class _Run:
    """One side's SQL build: its measures by casefolded name, its column types, the columns its queries read, and the
    referenced measures, each computed once per dataset into a one-row table (``order``) that its references read."""

    def __init__(self, side: Any) -> None:
        self.side = side
        self.names = sp._measure_names(side)
        self.measures = {str(e["key"][1]).casefold(): e for e in side.entries if e["kind"] == "measure"}
        sp._is_boolean(side, "", "")  # builds side.column_types
        self.types: dict[tuple[str, str], Any] = side.column_types or {}
        self.columns: dict[tuple[str, str], tuple[str, str]] = {}  # (table, column) -> (class, DuckDB type)
        self.tables: set[str] = set()
        self.parsed: dict[str, dict[str, Any]] = {}
        self.made: dict[str, str] = {}  # referenced measure -> the one-row table that holds its value
        self.order: list[tuple[str, str]] = []  # (that table, its query), each after the measures it reads
        self.schema = _q(f"{side.which}_measures")
        self.terms = 0  # aggregates and row counts in every query kept so far on this side
        self.building = 0  # in the query being built

    def build(self, f: tuple[Any, ...]) -> str:
        """One measure's query, within the term caps; a measure past them isn't run."""
        self.building = 0
        sql = self.form(f)
        self.terms += self.building
        return sql

    def count(self) -> None:
        self.building += 1
        if self.building > MAX_TERMS_PER_MEASURE:
            raise _NotRun(f"more than {MAX_TERMS_PER_MEASURE} aggregates in one measure")
        if self.terms + self.building > MAX_TERMS_PER_SIDE:
            raise _NotRun(f"the {self.side.which} side's measures reach {MAX_TERMS_PER_SIDE} aggregates in all")

    def column(self, table: str, column: str) -> str:
        found = self.types.get((table, column))
        typed = _TYPES.get(str(found).casefold()) if found is not None else None
        if typed is None:
            raise _NotRun(f"the {self.side.which} side's column {mo._short(table)}[{mo._short(column)}] has "
                          + ("no known type" if found is None else f"the type {mo._short(found)}, which L3 has no "
                             "controlled values for"))
        self.columns[(table, column)] = typed
        self.tables.add(table)
        return f"{_q(self.side.which)}.{_q(table)}"

    def measure(self, name: str, seen: tuple[str, ...]) -> str:
        """A reference to measure ``name``: its value, computed once however often it is referenced."""
        if name in self.made:
            return f"(SELECT v FROM {self.made[name]})"
        if name in seen or len(seen) >= MAX_MEASURE_DEPTH:
            raise _NotRun(f"measure [{mo._short(name)}] refers back to itself or past {MAX_MEASURE_DEPTH} levels")
        entry = self.measures.get(name)
        if entry is None:
            raise _NotRun(f"measure [{mo._short(name)}] isn't on the {self.side.which} side")
        if name not in self.parsed:
            self.parsed[name] = sp._side_form(self.side, entry, self.names)
        parsed = self.parsed[name]
        if parsed["form"] is None or parsed["dialect"] == "DAX":
            raise _NotRun(f"measure [{mo._short(name)}] has no SQL form in the subset"
                          + (f": {parsed['reason']}" if parsed["reason"] else ""))
        outer, self.building = self.building, 0
        try:
            sql = self.form(parsed["form"], (*seen, name))
            self.terms += self.building
        finally:
            self.building = outer
        table = f"{self.schema}.{_q(f'm{len(self.order)}')}"
        self.order.append((table, sql))
        self.made[name] = table
        return f"(SELECT v FROM {table})"

    def term(self, t: tuple[Any, ...], seen: tuple[str, ...]) -> str:
        if t[0] == "measure":
            return self.measure(t[1], seen)
        self.count()
        if t[0] == "agg":
            if t[1] not in _SQL_AGG:
                raise _NotRun(f"{t[1]} has no SQL form")
            where = self.column(t[2], t[3])
            if t[1] in _NUMERIC_AGG and self.columns[(t[2], t[3])][0] not in _NUMERIC_CLASSES:
                raise _NotRun(f"{t[1]} over {self.columns[(t[2], t[3])][0]} column {mo._short(t[2])}"
                              f"[{mo._short(t[3])}]: DuckDB's type rules here aren't DAX's or T-SQL's")
            return f"(SELECT {_SQL_AGG[t[1]].format(_q(t[3]))} FROM {where})"
        if not any(table == t[1] for table, _ in self.types):
            raise _NotRun(f"the {self.side.which} side has no columns for table {mo._short(t[1])}")
        self.tables.add(t[1])
        return f"(SELECT COUNT(*) FROM {_q(self.side.which)}.{_q(t[1])})"

    def form(self, f: tuple[Any, ...], seen: tuple[str, ...] = ()) -> str:
        if f[0] != "div":
            return self.term(f, seen)
        numerator, divisor = self.term(f[1], seen), self.term(f[2], seen)
        if f[3] == "BLANK":
            return f"{numerator} / NULLIF({divisor}, 0)"
        if f[3] == "SQL /":
            return f"{numerator} / {divisor}"
        raise _NotRun(f"a zero divisor giving {f[3]} has no SQL form")


def _value(v: Any) -> str | None:
    """A query result as exact text: None for NULL, a plain decimal for any number, ISO for a time."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if not math.isfinite(v):
            return str(v)
        return format(Decimal(repr(v)).normalize(), "f")
    if isinstance(v, (int, Decimal)):
        d = Decimal(v)
        return format(d.normalize(), "f") if d else "0"
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    return str(v)


def _number(text: str | None) -> Decimal | None:
    if text is None or text.startswith("error:"):
        return None
    with contextlib.suppress(InvalidOperation):
        d = Decimal(text)
        return d if d.is_finite() else None
    return None


def _close(a: str | None, b: str | None, tol: dict[str, float]) -> str:
    """'same', 'tolerance' or 'different' for two values as text."""
    if a == b:
        return "same"
    x, y = _number(a), _number(b)
    if x is None or y is None:
        return "different"
    gap = abs(x - y)
    allowed = max(Decimal(repr(tol["absolute"])), Decimal(repr(tol["relative"])) * max(abs(x), abs(y)))
    return "tolerance" if gap <= allowed else "different"


def _connect() -> Any:
    import duckdb

    con = duckdb.connect(":memory:", config={"threads": 1, "enable_external_access": False})
    con.execute("SET autoload_known_extensions = false")
    con.execute("SET lock_configuration = true")  # nothing later on this connection can loosen the settings above
    return con


def _load(con: Any, which: str, run: _Run, scenario: dict[str, list[str | None]]) -> None:
    """The scenario's rows for one side's tables, each column typed as that side types it."""
    con.execute(f"CREATE SCHEMA {_q(which)}")
    for table in sorted(run.tables):
        cols = sorted((c, typed) for (t, c), typed in run.columns.items() if t == table)
        if not cols:  # COUNT(*) only: the rows of any class
            con.execute(f"CREATE TABLE {_q(which)}.{_q(table)} AS SELECT * FROM range(?)", [len(scenario["int"])])
            continue
        n = len(scenario["int"])
        select = ", ".join(f"CAST(v{i} AS {typed[1]}) AS {_q(c)}" for i, (c, typed) in enumerate(cols))
        raw = ", ".join(f"v{i} VARCHAR" for i in range(len(cols)))
        con.execute(f"CREATE TEMP TABLE kf_l3_staging ({raw})")
        if n:
            marks = ", ".join("?" for _ in cols)
            con.executemany(f"INSERT INTO kf_l3_staging VALUES ({marks})",
                            [[scenario[typed[0]][r] for _, typed in cols] for r in range(n)])
        con.execute(f"CREATE TABLE {_q(which)}.{_q(table)} AS SELECT {select} FROM kf_l3_staging")
        con.execute("DROP TABLE kf_l3_staging")


def _run_side(which: str, run: _Run, queries: dict[str, str]) -> dict[str, dict[str, str | None]]:
    """{measure row id: {scenario: value as text}}, every scenario in a fresh in-memory database."""
    import duckdb

    out: dict[str, dict[str, str | None]] = {k: {} for k in queries}
    for name, scenario in SCENARIOS.items():
        con = _connect()
        try:
            _load(con, which, run, scenario)
            failed: dict[str, str] = {}  # a referenced measure's table -> its error, which its readers take
            if run.order:
                con.execute(f"CREATE SCHEMA {run.schema}")
            for table, sql in run.order:
                read = next((failed[t] for t in failed if t in sql), None)
                if read is not None:
                    failed[table] = read
                    continue
                try:
                    con.execute(f"CREATE TABLE {table} AS SELECT {sql} AS v")
                except duckdb.Error as exc:  # narrow: the engine's refusal on this data is that measure's value
                    failed[table] = f"error: {type(exc).__name__}"
            for key, sql in queries.items():
                read = next((failed[t] for t in failed if t in sql), None)
                if read is not None:
                    out[key][name] = read
                    continue
                try:
                    rows = con.execute(f"SELECT {sql}").fetchall()
                    out[key][name] = _value(rows[0][0])
                except duckdb.Error as exc:  # narrow: what the engine refuses on this data is that value's result
                    out[key][name] = f"error: {type(exc).__name__}"
        finally:
            con.close()
    return out


def _check_no_dax_result(objects: list[dict[str, Any]]) -> None:
    """A DAX side never runs on this PC, so a DAX side with results is a programming error, never reported."""
    for o in objects:
        behavior = o.get("behavior") or {}
        for which in ("left", "right"):
            b = behavior.get(which) or {}
            if b.get("dialect") == "DAX" and (b.get("ran") or b.get("results")):
                raise AssertionError("L3 never reports a DAX-side result without an executed DAX query")


def parity_l3(left: Any, right: Any, *, tolerances: dict[str, Any] | None = None, left_kind: str | None = None,
              right_kind: str | None = None) -> dict[str, Any]:
    """The L3 behavioral parity report, SQL side (module docstring): the L2 report with each measure row's
    ``behavior``. Same inputs as ``parity_l2``; never raises for bad input."""
    tol = _tolerances(tolerances)
    report = sp.parity_l2(left, right, left_kind=left_kind, right_kind=right_kind)
    report["level"] = LEVEL_L3
    report["not_checked"] = [dict(n) for n in NOT_CHECKED_L3]
    report["tolerances"] = tol
    report["scenarios"] = sorted(SCENARIOS)
    if report["error"] or not any(o["kind"] == "measure" for o in report["objects"]):
        return report
    sides = [sp._Side("left"), sp._Side("right")]
    for side, value, kind in ((sides[0], left, left_kind), (sides[1], right, right_kind)):
        try:
            sp._read(side, value, kind)
        except sp._READ_ERRORS as exc:  # narrow: L2 read it, so this is a reader that isn't deterministic
            report["error"] = f"the {side.which} side couldn't be read again for L3: {type(exc).__name__}"
            return report
    runs = {s.which: _Run(s) for s in sides}
    queries: dict[str, dict[str, str]] = {"left": {}, "right": {}}
    behaviors: dict[str, dict[str, Any]] = {}
    for o in report["objects"]:
        if o["kind"] != "measure" or "expression_result" not in o:
            continue
        behavior: dict[str, Any] = {}
        for side in sides:
            entry = side.by_id.get(o["ids"][side.which] or "")
            b: dict[str, Any] = {"dialect": None, "ran": False, "sql": None, "results": {}, "reason": ""}
            behavior[side.which] = b
            if entry is None:
                b["reason"] = "not on this side"
                continue
            parsed = sp._side_form(side, entry, runs[side.which].names)
            b["dialect"] = parsed["dialect"]
            if parsed["dialect"] == "DAX":
                b["reason"] = _DAX_NOT_RUN
            elif parsed["form"] is None:
                b["reason"] = f"outside the L2 subset: {parsed['reason']}"
            elif len(queries[side.which]) >= MAX_MEASURES_RUN:
                b["reason"] = f"not run: past {MAX_MEASURES_RUN} measures on one side"
            else:
                try:
                    b["sql"] = "SELECT " + runs[side.which].build(parsed["form"])
                    queries[side.which][o["id"]] = b["sql"][len("SELECT "):]
                except _NotRun as exc:  # narrow: a form L3 can't build; the reason is the result
                    b["reason"] = f"not run: {exc}"
        behaviors[o["id"]] = behavior
    results = {which: _run_side(which, runs[which], queries[which]) if queries[which] else {} for which in queries}
    for o in report["objects"]:
        if o["id"] not in behaviors:
            continue
        behavior = behaviors[o["id"]]
        for which in ("left", "right"):
            if o["id"] in results[which]:
                behavior[which].update(ran=True, results=results[which][o["id"]])
        o["behavior"] = behavior
        _judge(o, behavior, tol)
    _check_no_dax_result(report["objects"])
    counts = dict.fromkeys(sp.RESULTS, 0)
    for o in report["objects"]:
        counts[o["result"]] += 1
    report["counts"] = counts
    order = (sp.TEST_FAILED, sp.DIFFERENT, sp.UNSUPPORTED, sp.COMPATIBLE, sp.WITHIN_TOLERANCE, sp.VERIFIED)
    report["summary"] = min((o["result"] for o in report["objects"]), key=order.index, default=sp.TEST_FAILED)
    return report


def _judge(o: dict[str, Any], behavior: dict[str, Any], tol: dict[str, Any]) -> None:
    left, right = behavior["left"], behavior["right"]
    if not (left["ran"] and right["ran"]):
        ran = [w for w in ("left", "right") if behavior[w]["ran"]]
        o["notes"] = o["notes"] + ([f"L3 ran the {ran[0]} side's SQL only; its values are evidence, not a match"]
                                   if ran else ["L3 ran neither side"])
        return
    refused = [n for n in SCENARIOS if any(str(b["results"].get(n)).startswith("error:") for b in (left, right))]
    if refused:  # a refusal never verifies: equal error texts aren't equal behavior
        one_sided = [n for n in refused if str(left["results"].get(n)).startswith("error:")
                     != str(right["results"].get(n)).startswith("error:")]
        both = len(refused) - len(one_sided)
        if both:
            o["notes"] = o["notes"] + [(f"DuckDB refused both sides' query on {both} of {len(SCENARIOS)} datasets, so "
                                        "L3 verifies nothing about this measure")]
        if one_sided:
            o["behavior_result"] = sp.DIFFERENT
            o["changes"] = o["changes"] + [f"on the {n} dataset only one side's query ran" for n in one_sided]
            if o["result"] != sp.TEST_FAILED:
                o["result"] = sp.DIFFERENT
        return
    verdicts = {name: _close(left["results"].get(name), right["results"].get(name), tol["values"])
                for name in SCENARIOS}
    tol["used"] = True
    differ = [n for n, v in verdicts.items() if v == "different"]
    o["changes"] = o["changes"] + [
        f"on the {n} dataset the left side gives {left['results'].get(n)!s} and the right side "
        f"{right['results'].get(n)!s}" for n in differ]
    if differ:
        found = sp.DIFFERENT
    elif any(v == "tolerance" for v in verdicts.values()):
        found = sp.WITHIN_TOLERANCE
    else:
        found = sp.VERIFIED
    o["behavior_result"] = found
    if o["result"] == sp.COMPATIBLE:  # L3 only replaces the ceiling; a worse L1 or L2 result stands
        o["result"] = found
    elif found == sp.DIFFERENT and o["result"] != sp.TEST_FAILED:
        o["result"] = sp.DIFFERENT
    if found in (sp.VERIFIED, sp.WITHIN_TOLERANCE):
        notes = [n for n in o["notes"] if n != sp._NO_QUERY] if o["result"] == found else o["notes"]
        o["notes"] = notes + [_ON_DATASETS]


def redacted(report: dict[str, Any]) -> dict[str, Any]:
    """``semantic_parity.redacted`` plus each built query as a length and hash (it names tables and columns)."""
    out = sp.redacted(report)
    for o in out.get("objects") or []:
        for b in (o.get("behavior") or {}).values():
            if isinstance(b, dict) and isinstance(b.get("sql"), str):
                b["sql"] = sp._fingerprint(b["sql"])
    return out


__all__ = ["DEFAULT_TOLERANCES_L3", "LEVEL_L3", "SCENARIOS", "parity_l3", "redacted"]
