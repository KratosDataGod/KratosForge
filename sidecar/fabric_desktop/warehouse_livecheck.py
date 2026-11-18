"""The warehouse live check (WR-218): local T-SQL's answers next to a Fabric Warehouse's, case by case.

docs/sql-compatibility.md says, section by section, where local T-SQL gives a warehouse's answer and
where it still differs. Most of it was checked against T-SQL's reference, not against a warehouse.
This check runs each listed case three ways and records all three answers:

- **local**: what the local engine gives, worked out on this PC in a throwaway project;
- **documented**: what the docs (and T-SQL's reference behind them) say a warehouse gives, or nothing
  when the reference doesn't say (``basis="unverified"``). A case a live run settled has
  ``basis="live run"``: its documented answer is the one a test warehouse gave;
- **warehouse**: what the test warehouse gives.

It is flow 17 of the smoke test (``smoke-test --warehouse <id>``), so it runs only with Run 11's
go, on the marked test workspace. Safety, in order:

1. The workspace must pass the live test's guards (``livetest.refusals``): marked
   ``fabric-desktop-live-test``, not named like production, not a Platform workspace. The warehouse
   must be a Warehouse item in that workspace, and its name mustn't look like production either.
2. Every case's SQL is read with sqlglot before anything connects (``statement_problem``). A
   ``SELECT`` may read anything; any other statement may only name tables in the scratch schema
   ``fdlt_<run id>``, and only CREATE TABLE or VIEW, INSERT, UPDATE, DELETE, ALTER TABLE and DROP
   are allowed. One case that breaks this stops the whole check before it connects.
3. The scratch schema is the only thing it creates. Each case runs in a transaction that is rolled
   back (a case about a batch outside a transaction, ``autocommit``, runs without one and leaves its
   scratch tables for the end), and at the end whatever is left in the schema, and the schema itself, is dropped and
   checked gone.

``dry_run`` works out the local answers and lists the SQL each case would send, connecting nowhere.
A run's results go to ``<out>/warehouse-check.json`` and ``warehouse-check.md``; neither holds a token.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from . import livetest

_log = logging.getLogger(__name__)
SCHEMA = "warehouse-check-result@1"
_SCRATCH = re.compile(r"fdlt_[0-9a-f]{6,32}")


@dataclass(frozen=True)
class Case:
    """One case. ``setup`` and ``statements`` run first (scratch tables only); ``query`` gives the answer: the
    first column of its first row. ``{s}`` is the scratch schema. ``documented`` is the warehouse's answer as the
    docs give it, ``error: <words>`` for an error whose message has those words, or None when the reference
    doesn't say."""

    id: str
    wr: str
    section: str
    title: str
    query: str
    documented: str | None
    basis: str = "reference"  # reference: T-SQL's reference; unverified: it doesn't say; live run: a warehouse's
    setup: tuple[str, ...] = ()
    statements: tuple[str, ...] = ()
    autocommit: bool = False  # run outside a transaction: an error in one dooms a warehouse's whole transaction


CASES: tuple[Case, ...] = (
    # WR-206 decimals
    Case("DEC-01", "WR-206", "decimals", "1.0 / 3 is decimal with scale 6", "SELECT 1.0 / 3", "0.333333"),
    Case("DEC-02", "WR-206", "decimals", "decimal(38,10) * decimal(38,10) gives scale 6",
         "SELECT CAST(1.5 AS DECIMAL(38,10)) * CAST(2 AS DECIMAL(38,10))", "3.000000"),
    Case("DEC-03", "WR-206", "decimals", "AVG of a decimal is decimal(38, max(s, 6))",
         "SELECT AVG(v) FROM (VALUES (CAST(1 AS DECIMAL(10,2))), (CAST(2 AS DECIMAL(10,2)))) AS x(v)", "1.500000"),
    Case("DEC-04", "WR-206", "decimals", "A result too big for its type stops",
         "SELECT CAST(99999999999999999999999999999999.999999 AS DECIMAL(38,6)) * 10", "error: overflow"),
    # WR-207 dates and times
    Case("DT-01", "WR-207", "dates", "DATEADD month ends on the shorter month's last day",
         "SELECT DATEADD(month, 1, CAST('2024-01-31' AS DATE))", "2024-02-29"),
    Case("DT-02", "WR-207", "dates", "DATEDIFF week counts Sunday boundaries",
         "SELECT DATEDIFF(week, CAST('2024-03-02' AS DATE), CAST('2024-03-03' AS DATE))", "1"),
    Case("DT-03", "WR-207", "dates", "CONVERT style 121 of a datetime2(6)",
         "SELECT CONVERT(VARCHAR(30), CAST('2024-02-29 13:04:05.123' AS DATETIME2(6)), 121)",
         "2024-02-29 13:04:05.123000"),
    Case("DT-04", "WR-207", "dates", "CONVERT style 104 (not checked locally)",
         "SELECT CONVERT(VARCHAR(10), CAST('2024-02-29' AS DATE), 104)", "29.02.2024"),
    Case("DT-05", "WR-207", "dates", "CAST of a datetime2(6) to text keeps its 6 digits of a second",
         "SELECT CAST(CAST('2024-02-29 13:04:05' AS DATETIME2(6)) AS VARCHAR(30))", "2024-02-29 13:04:05.000000",
         "live run"),
    # WR-209 ordering and window functions
    Case("ORD-01", "WR-209", "ordering", "PERCENTILE_CONT of a decimal is a float",
         "SELECT TOP 1 PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY v) OVER () "
         "FROM (VALUES (1.25), (2.50), (4.00)) AS x(v)", "1.875"),
    Case("ORD-02", "WR-209", "ordering", "PERCENTILE_DISC with ORDER BY DESC on a date (refused locally)",
         "SELECT TOP 1 PERCENTILE_DISC(0.25) WITHIN GROUP (ORDER BY d DESC) OVER () FROM (VALUES "
         "(CAST('2024-01-01' AS DATE)), (CAST('2024-01-02' AS DATE)), (CAST('2024-01-03' AS DATE)), "
         "(CAST('2024-01-04' AS DATE))) AS x(d)", "2024-01-04"),
    Case("ORD-03", "WR-209", "ordering", "NULL sorts first ascending",
         "SELECT TOP 1 v FROM (VALUES (2), (NULL), (1)) AS x(v) ORDER BY v", "NULL"),
    # WR-210 text
    Case("TXT-01", "WR-210", "text", "Only spaces are trailing blanks: a no-break space counts",
         "SELECT CASE WHEN 'a' = 'a' + NCHAR(160) THEN 1 ELSE 0 END", "0"),
    Case("TXT-02", "WR-210", "text", "LEN leaves out trailing spaces, DATALENGTH doesn't",
         "SELECT CONCAT(LEN('ab  '), '/', DATALENGTH('ab  '))", "2/4"),
    Case("TXT-03", "WR-210", "text", "LIKE with _ on text with trailing spaces",
         "SELECT CASE WHEN 'abc  ' LIKE 'abc_' THEN 1 ELSE 0 END", "1", "live run"),
    Case("TXT-04", "WR-210", "text", "LIKE with __ on text with trailing spaces",
         "SELECT CASE WHEN 'abc  ' LIKE 'abc__' THEN 1 ELSE 0 END", "1", "live run"),
    Case("TXT-05", "WR-210", "text", "CHARINDEX with a NULL start",
         "SELECT CHARINDEX('b', 'abc', NULL)", "NULL", "live run"),
    Case("TXT-06", "WR-210", "text", "GROUP BY counts 'a' and 'a  ' as one value (differs locally)",
         "SELECT COUNT(*) FROM (SELECT v FROM {s}.g GROUP BY v) AS x", "1",
         setup=("CREATE TABLE {s}.g (v VARCHAR(5))",), statements=("INSERT INTO {s}.g VALUES ('a'), ('a  ')",)),
    # WR-211 arithmetic and conversion
    Case("AR-01", "WR-211", "arithmetic", "Integer % takes the dividend's sign", "SELECT -7 % 2", "-1"),
    Case("AR-02", "WR-211", "arithmetic", "An int past its range is an overflow error (message differs locally)",
         "SELECT 2147483647 + 1", "error: overflow"),
    Case("AR-03", "WR-211", "arithmetic", "ISNULL takes its first argument's type",
         "SELECT ISNULL(CAST(NULL AS VARCHAR(2)), 'abcd')", "ab"),
    Case("AR-04", "WR-211", "arithmetic", "ROUND that no longer fits the type is an overflow",
         "SELECT ROUND(748.58, -3)", "error: overflow"),
    # WR-212 and WR-215 tables
    Case("TBL-01", "WR-215", "tables", "CREATE TABLE AS SELECT keeps NOT NULL (differs locally)",
         "SELECT IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = '{s}' AND TABLE_NAME = 'c2'", "NO",
         setup=("CREATE TABLE {s}.c1 (id INT NOT NULL)",), statements=("CREATE TABLE {s}.c2 AS SELECT id FROM {s}.c1",)),
    Case("TBL-02", "WR-215", "tables", "CHAR(n) is padded to n (differs locally)",
         "SELECT DATALENGTH(v) FROM {s}.ch", "5",
         setup=("CREATE TABLE {s}.ch (v CHAR(5))",), statements=("INSERT INTO {s}.ch VALUES ('ab')",)),
    Case("TBL-03", "WR-215", "tables", "Trailing blanks past a column's length are dropped (differs locally)",
         "SELECT DATALENGTH(v) FROM {s}.vc", "3",
         setup=("CREATE TABLE {s}.vc (v VARCHAR(3))",), statements=("INSERT INTO {s}.vc VALUES ('ab   ')",)),
    # WR-213 changes
    Case("CHG-01", "WR-213", "changes", "UPDATE ... FROM where a row joins two rows (refused locally)",
         "SELECT COUNT(*) FROM {s}.ua WHERE v IN (10, 20)", "1",
         setup=("CREATE TABLE {s}.ua (id INT, v INT)", "CREATE TABLE {s}.ub (id INT, v INT)",
                "INSERT INTO {s}.ua VALUES (1, 0)", "INSERT INTO {s}.ub VALUES (1, 10), (1, 20)"),
         statements=("UPDATE t SET v = b.v FROM {s}.ua AS t JOIN {s}.ub AS b ON t.id = b.id",)),
    Case("CHG-02", "WR-213", "changes", "UPDATE ... FROM with a LEFT JOIN (refused locally)",
         "SELECT SUM(v) FROM {s}.la", "11",
         setup=("CREATE TABLE {s}.la (id INT, v INT)", "CREATE TABLE {s}.lb (id INT, v INT)",
                "INSERT INTO {s}.la VALUES (1, 0), (2, 0)", "INSERT INTO {s}.lb VALUES (1, 10)"),
         statements=("UPDATE t SET v = COALESCE(b.v, 1) FROM {s}.la AS t LEFT JOIN {s}.lb AS b ON t.id = b.id",)),
    # WR-214 scripts
    Case("SCR-01", "WR-214", "scripts", "A divide by zero ends its statement, and the batch goes on (differs locally)",
         "SELECT COUNT(*) FROM {s}.sc", "2",
         setup=("CREATE TABLE {s}.sc (id INT)",),
         statements=("INSERT INTO {s}.sc VALUES (1); SELECT 1 / 0; INSERT INTO {s}.sc VALUES (2)",),
         autocommit=True),
    # WR-216 views
    Case("VW-01", "WR-216", "views", "A SELECT * view keeps its columns after one is added (differs locally)",
         "SELECT b FROM {s}.vv", "error: Invalid column name",
         setup=("CREATE TABLE {s}.vt (a INT)", "CREATE VIEW {s}.vv AS SELECT * FROM {s}.vt"),
         statements=("ALTER TABLE {s}.vt ADD b INT", "INSERT INTO {s}.vt VALUES (1, 2)")),
    # WR-217 session settings
    Case("SET-01", "WR-217", "settings", "A warehouse's DATEFIRST is 7", "SELECT @@DATEFIRST", "7"),
    Case("SET-02", "WR-217", "settings", "ANSI_NULLS is on: NULL = NULL isn't true",
         "SELECT CASE WHEN NULL = NULL THEN 1 ELSE 0 END", "0"),
    # WR-229 formatting: .NET's answers; the unverified ones are refused locally until a live run gives them
    Case("FMT-01", "WR-229", "formatting", "FORMAT with a custom number format in de-DE",
         "SELECT FORMAT(1234567.891, '#,##0.00', 'de-DE')", "1.234.567,89"),
    Case("FMT-02", "WR-229", "formatting", "FORMAT with digits between text, and a section for below zero",
         "SELECT CONCAT(FORMAT(123456789, '000-00-0000'), '/', FORMAT(-5.5, '$#,##0.00;($#,##0.00)'))",
         "123-45-6789/($5.50)"),
    # PARSE's "AS type(n) USING culture" can't pass the guard's reader, so this one has neither
    Case("FMT-03", "WR-229", "formatting", "PARSE reads thousands separators", "SELECT PARSE(' -1,234 ' AS INT)",
         "-1234"),
    Case("FMT-04", "WR-229", "formatting", "FORMAT 'P' (refused locally)", "SELECT FORMAT(0.1234, 'P')", None,
         "unverified"),
    Case("FMT-05", "WR-229", "formatting", "FORMAT 'C' below zero (refused locally)", "SELECT FORMAT(-1234.5, 'C')",
         None, "unverified"),
    Case("FMT-06", "WR-229", "formatting", "FORMAT of a negative number that rounds to zero (refused locally)",
         "SELECT FORMAT(-0.001, '0.00')", None, "unverified"),
    Case("FMT-07", "WR-229", "formatting", "TRY_PARSE of '12.0' as an int (refused locally)",
         "SELECT TRY_PARSE('12.0' AS INT)", None, "unverified"),
    Case("FMT-08", "WR-229", "formatting", "FORMAT 'N2' of a negative number that rounds to zero (refused locally)",
         "SELECT FORMAT(-0.001, 'N2')", None, "unverified"),
)


# ---- the SQL guard

_ALLOWED = (exp.Create, exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter)


def statement_problem(sql: str, scratch: str) -> str | None:
    """Why ``sql`` may not be sent, or None. A SELECT (with no INTO) may read anything. Any other statement must be
    CREATE TABLE or VIEW, INSERT, UPDATE, DELETE, ALTER TABLE or DROP TABLE or VIEW, and every table it names (aliases
    aside) must be in ``scratch``."""
    if not _SCRATCH.fullmatch(scratch):
        return f"{scratch!r} isn't a scratch schema name"
    try:
        trees = [t for t in sqlglot.parse(sql, read="tsql") if t is not None]
    except SqlglotError as exc:
        return f"it can't be read: {exc}"
    if not trees:
        return "there's nothing to run"
    for tree in trees:
        if isinstance(tree, (exp.Select, exp.Union)) and not tree.find(exp.Into):
            continue
        if not isinstance(tree, _ALLOWED):
            return f"{type(tree).__name__} isn't allowed"
        kind = str(tree.args.get("kind") or "").upper()
        if isinstance(tree, (exp.Create, exp.Drop)) and kind not in ("TABLE", "VIEW"):
            return f"{type(tree).__name__.upper()} {kind or '?'} isn't allowed"
        if isinstance(tree, exp.Alter) and kind != "TABLE":
            return f"ALTER {kind or '?'} isn't allowed"
        targets = _alias_targets(tree, scratch)
        for table in tree.find_all(exp.Table):
            if not table.name:
                continue
            if any(table is t for t in targets):
                continue  # UPDATE t ... FROM scratch.x AS t: t is that scratch table
            if table.db.lower() != scratch.lower() or table.catalog:
                return f"it names {table.sql('tsql')}, outside the scratch schema {scratch}"
    return None


def _alias_targets(tree: exp.Expression, scratch: str) -> list[exp.Table]:
    """The bare target of an UPDATE or DELETE that names an alias of a scratch table in the same statement. Only
    those targets bind to a FROM alias in T-SQL; an INSERT, CREATE or DROP target, or any other bare name, means
    dbo."""
    if isinstance(tree, exp.Update):
        candidates = [tree.this]
    elif isinstance(tree, exp.Delete):
        candidates = [*(tree.args.get("tables") or []), tree.this]
    else:
        return []
    # only the statement's own FROM and JOIN tables: an alias inside a subquery isn't visible to the target
    bound = {t.alias.lower() for t in tree.find_all(exp.Table)
             if t.alias and t.db.lower() == scratch.lower() and not t.catalog
             and t.find_ancestor(exp.Select, exp.Subquery, exp.Exists) is None}
    return [t for t in candidates if isinstance(t, exp.Table) and not t.db and not t.catalog and not t.alias
            and t.name.lower() in bound]


def sql_for(case: Case, scratch: str) -> list[str]:
    """Every statement the case sends, in order, with the scratch schema filled in."""
    return [s.replace("{s}", scratch) for s in (*case.setup, *case.statements, case.query)]


def problems(cases: tuple[Case, ...], scratch: str) -> list[str]:
    if not _SCRATCH.fullmatch(scratch):
        return [f"{scratch!r} isn't a scratch schema name"]
    found = []
    for case in cases:
        for sql in sql_for(case, scratch):
            why = statement_problem(sql, scratch)
            if why:
                found.append(f"{case.id}: {why}")
    return found


# ---- answers

def answer(value: Any) -> str:
    """One value as text that compares across engines: NULL, a number without float noise, an ISO date."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return format(value, ".15g")
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex().upper()
    return str(value)


def _error(exc: BaseException) -> str:
    return "error: " + livetest.scrub(str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__)


def same(a: str | None, b: str | None) -> bool:
    """Whether two answers agree: an error matches ``error: <words>`` when its message has the words, ignoring case;
    numbers compare by value, so ``1.50`` is ``1.5``."""
    if a is None or b is None:
        return False
    if a.startswith("error:") or b.startswith("error:"):
        if not (a.startswith("error:") and b.startswith("error:")):
            return False
        want, got = sorted((a[6:].strip().lower(), b[6:].strip().lower()), key=len)
        return want in got
    try:
        return Decimal(a) == Decimal(b)
    except (InvalidOperation, ValueError):
        return a == b


def local_answers(cases: tuple[Case, ...], scratch: str, root: Path | None = None) -> dict[str, str]:
    """Each case's answer from the local engine, in a throwaway project."""
    from .localdata import LocalData

    with tempfile.TemporaryDirectory() as tmp:
        store = LocalData(Path(root or tmp) / "local-data")
        try:
            out: dict[str, str] = {}
            for case in cases:
                engine = store.engine(store.create(f"Check {case.id}")["id"])
                try:
                    engine.execute(f"CREATE SCHEMA {scratch}", dialect="tsql")
                    *before, query = sql_for(case, scratch)
                    for sql in before:
                        engine.execute(sql, dialect="tsql")
                    rows = engine.execute(query, dialect="tsql")["rows"]
                    out[case.id] = answer(rows[0][0]) if rows else "no rows"
                except Exception as exc:  # noqa: BLE001 - an error is the local answer
                    out[case.id] = _error(exc)
            return out
        finally:
            store.close_all()


# ---- the run

def _drain(cur: Any, errors: list[str], limit: int = 20) -> None:
    """Read the rest of a batch's results. An error between them is added to ``errors`` and reading goes on, up to
    ``limit`` errors."""
    while len(errors) < limit:
        try:
            if not cur.nextset():
                return
        except Exception as exc:  # noqa: BLE001 - noted, and the next result is read
            errors.append(_error(exc))


@dataclass
class Result:
    id: str
    wr: str
    section: str
    title: str
    basis: str
    documented: str | None
    local: str
    warehouse: str | None = None
    sql: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # errors the warehouse gave in the case's statements

    @property
    def verdict(self) -> str:
        if self.warehouse is None:
            return "not run"
        if self.documented is None:
            return "local matches the warehouse" if same(self.local, self.warehouse) else "local differs"
        if not same(self.documented, self.warehouse):
            return "docs are wrong"
        return "as documented" if same(self.local, self.warehouse) else "documented difference"


class WarehouseCheck:
    """One run of the cases against one test warehouse."""

    def __init__(self, workspace_id: str, warehouse_id: str, fabric: Any, connect: Callable[[], Any] | None,
                 out_dir: Path, protected_ids: set[str] | None = None, run_id: str = "",
                 cases: tuple[Case, ...] = CASES):
        self.workspace_id, self.warehouse_id = workspace_id, warehouse_id
        self.fabric, self.connect = fabric, connect
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.scratch = f"{livetest.PREFIX}{self.run_id}"
        self.dir = Path(out_dir)
        self.protected_ids = {i.lower() for i in (protected_ids or set())}
        self.cases = cases
        self.refused: list[str] = []
        self.cleanup: str = ""

    def plan(self) -> list[str]:
        return [
            (f"Read workspace {self.workspace_id} and check it is marked for tests and isn't production; check "
            f"{self.warehouse_id} is a Warehouse in it."),
            f"Work out each of the {len(self.cases)} cases' answers on this PC, in a throwaway local project.",
            f"Create the scratch schema {self.scratch} in the warehouse. It's the only thing the check creates.",
            "Run each case in a transaction that is rolled back: reads, plus tables made in the scratch schema.",
            f"Drop what's left in {self.scratch}, drop the schema, and check it's gone.",
            f"Write the local, documented and warehouse answers to {self.dir}.",
        ]

    def _guard(self) -> list[str]:
        workspace = self.fabric.get_workspace(self.workspace_id)
        reasons = livetest.refusals(workspace, self.protected_ids)
        item = self.fabric.get_warehouse(self.workspace_id, self.warehouse_id)
        if str(item.get("type") or "Warehouse") != "Warehouse":
            reasons.append(f"Item {self.warehouse_id} is a {item.get('type')}, not a Warehouse.")
        if str(item.get("workspaceId") or self.workspace_id).lower() != self.workspace_id.lower():
            reasons.append("The warehouse isn't in the test workspace.")
        if livetest._PROD_WORD.search(str(item.get("displayName") or "")):
            reasons.append(f"The warehouse name {item.get('displayName')!r} looks like production.")
        return reasons

    def dry_run(self) -> dict[str, Any]:
        """The local answers and the SQL each case would send. Connects nowhere and writes no results file."""
        self.refused = problems(self.cases, self.scratch)
        local = {} if self.refused else local_answers(self.cases, self.scratch)
        return self._finish([self._result(c, local) for c in self.cases], dry=True)

    def run(self) -> dict[str, Any]:
        self.refused = problems(self.cases, self.scratch)
        if not self.refused:
            self.refused = self._guard()
        if self.refused or self.connect is None:
            return self._finish([], dry=False)
        local = local_answers(self.cases, self.scratch)
        results = [self._result(c, local) for c in self.cases]
        con = self.connect()
        try:
            cur = con.cursor()
            cur.execute(f"CREATE SCHEMA [{self.scratch}]")
            con.commit()
            try:
                for r, case in zip(results, self.cases, strict=True):
                    r.warehouse = self._one(con, case, r.notes)
            finally:
                self.cleanup = self._drop(con)
        finally:
            con.close()
        return self._finish(results, dry=False)

    def _result(self, case: Case, local: dict[str, str]) -> Result:
        return Result(case.id, case.wr, case.section, case.title, case.basis, case.documented,
                      local.get(case.id, ""), sql=sql_for(case, self.scratch))

    def _one(self, con: Any, case: Case, notes: list[str]) -> str:
        sent = sql_for(case, self.scratch)
        setup, statements, query = sent[:len(case.setup)], sent[len(case.setup):-1], sent[-1]
        cur = con.cursor()
        if case.autocommit:
            con.autocommit = True
        try:
            for sql in setup:
                cur.execute(sql)
                failed: list[str] = []
                _drain(cur, failed)
                if failed:
                    return failed[0]  # the setup didn't run, so the case has no answer of its own
            for sql in statements:
                # A run-time error can end one statement of a batch while the rest runs on (SCR-01), so an
                # error here is noted and the batch's other results are still read.
                errors: list[str] = []
                try:
                    cur.execute(sql)
                except Exception as exc:  # noqa: BLE001 - noted; the case's query gives its answer
                    errors.append(_error(exc))
                _drain(cur, errors)
                notes += errors
            cur.execute(query)
            row = cur.fetchone()
            return answer(row[0]) if row else "no rows"
        except Exception as exc:  # noqa: BLE001 - an error is the warehouse's answer
            return _error(exc)
        finally:
            if case.autocommit:
                con.autocommit = False  # its tables stay in the scratch schema, which the end drops
            try:
                con.rollback()
            except Exception as exc:  # noqa: BLE001 - a rollback after a broken connection; _drop reports what's left
                _log.warning("The rollback after case %s failed: %s", case.id, livetest.scrub(str(exc)))

    def _drop(self, con: Any) -> str:
        """Drop the scratch schema's views and tables, then the schema; say whether it's gone."""
        cur = con.cursor()
        cur.execute("SELECT TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ?",
                    (self.scratch,))
        left = sorted(cur.fetchall(), key=lambda r: r[1] != "VIEW")  # views first
        for name, kind in left:
            what = "VIEW" if kind == "VIEW" else "TABLE"
            cur.execute(f"DROP {what} [{self.scratch}].[{str(name).replace(']', ']]')}]")
        cur.execute(f"DROP SCHEMA [{self.scratch}]")
        con.commit()
        cur.execute("SELECT SCHEMA_ID(?)", (self.scratch,))
        row = cur.fetchone()
        if row and row[0] is not None:
            raise RuntimeError(f"The scratch schema {self.scratch} is still there. Drop it in the warehouse.")
        return f"Dropped {self.scratch}" + (f" and {len(left)} object(s) left in it" if left else "") + "."

    def _finish(self, results: list[Result], dry: bool) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for r in results:
            counts[r.verdict] = counts.get(r.verdict, 0) + 1
        outcome = ("refused" if self.refused else "dry run" if dry else
                   "findings" if counts.get("docs are wrong") or counts.get("local differs") else "passed")
        result = {
            "schema": SCHEMA, "run_id": self.run_id, "workspace_id": self.workspace_id,
            "warehouse_id": self.warehouse_id, "scratch_schema": self.scratch, "outcome": outcome,
            "refused": self.refused, "cleanup": self.cleanup, "counts": counts,
            "cases": [{**asdict(r), "verdict": r.verdict} for r in results],
        }
        if dry:
            return result
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "warehouse-check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (self.dir / "warehouse-check.md").write_text(summary(result), encoding="utf-8")
        result["files"] = [str(self.dir / "warehouse-check.json"), str(self.dir / "warehouse-check.md")]
        return result


def summary(result: dict[str, Any]) -> str:
    lines = [f"# Warehouse live check {result['run_id']}", "", f"Outcome: **{result['outcome']}**.", ""]
    if result["refused"]:
        lines += ["Not run:", *[f"- {livetest.cell(r)}" for r in result["refused"]], ""]
    if result["cleanup"]:
        lines += [livetest.cell(result["cleanup"]), ""]
    lines += ["| Case | What | Basis | Local | Documented | Warehouse | Verdict |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for c in result["cases"]:
        lines.append(f"| {c['id']} ({c['wr']}) | {livetest.cell(c['title'])} | {c['basis']} | "
                     f"{livetest.cell(c['local'], 200)} | {livetest.cell(c['documented'] or 'not documented', 200)} | "
                     f"{livetest.cell(c['warehouse'] if c['warehouse'] is not None else '-', 200)} | {c['verdict']} |")
    return "\n".join(lines) + "\n"
