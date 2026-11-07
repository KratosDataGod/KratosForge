"""Snowflake SQL on this PC (SF-04): rewritten for DuckDB, with an honest label on every statement.

DuckDB is not Snowflake. ``translate(code)`` reads Snowflake SQL with sqlglot, applies KratosForge's
rewrite rules (SFX-*) and says, statement by statement, one of:

    translated        no known difference applies: it matches Snowflake's documented behaviour as far
                      as is known. Nothing here has been compared with a Snowflake account.
    difference        it runs, but a known difference applies (SFD-*); the note says what and what to do
    not_translatable  it runs only in Snowflake (streams, tasks, dynamic tables, Snowflake Scripting,
                      time travel, clones, stages...); the note says why

A cell runs only when every statement is translated or difference: never part of a cell. The rules
and differences come from the transpile probe of story #853 (29 statements on the pinned sqlglot and
DuckDB) and tests/test_snowflake_sql.py keeps one test per probe row.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError

RULES_VERSION = "2026.11.11"
_DOCS = "https://docs.snowflake.com/en/sql-reference"

VERDICTS = ("translated", "difference", "not_translatable")


@dataclass(frozen=True)
class Note:
    id: str
    title: str
    reason: str
    action: str
    source: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title, "reason": self.reason, "action": self.action,
                "source": self.source}


@dataclass(frozen=True)
class StatementReport:
    statement: int                       # 1-based
    verdict: str                         # VERDICTS
    duckdb_sql: str | None               # None when not_translatable
    notes: tuple[Note, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"statement": self.statement, "verdict": self.verdict, "duckdb_sql": self.duckdb_sql,
                "notes": [n.as_dict() for n in self.notes]}


@dataclass(frozen=True)
class Translation:
    statements: tuple[StatementReport, ...]
    rules_version: str = RULES_VERSION

    @property
    def runnable(self) -> bool:
        return bool(self.statements) and all(s.verdict != "not_translatable" for s in self.statements)

    @property
    def duckdb_sql(self) -> str:
        """Every statement for DuckDB, joined; only for a runnable translation."""
        if not self.runnable:
            raise ValueError(self.first_refusal())
        return ";\n".join(s.duckdb_sql or "" for s in self.statements)

    def first_refusal(self) -> str:
        for s in self.statements:
            if s.verdict == "not_translatable":
                note = s.notes[0] if s.notes else None
                why = f"{note.title}. {note.reason}" if note else "It can't be rewritten for DuckDB."
                return f"Statement {s.statement} runs only in Snowflake: {why}"
        return ""

    def summary(self) -> str:
        """One line per statement and note, for a cell's output and for exports."""
        words = {"translated": "translated (matches Snowflake's documented behaviour as far as known)",
                 "difference": "runs with a known difference", "not_translatable": "runs only in Snowflake"}
        lines = [f"Snowflake SQL rewritten for DuckDB on this PC (rules {self.rules_version}). "
                 "Not checked against a Snowflake account."]
        for s in self.statements:
            lines.append(f"Statement {s.statement}: {words[s.verdict]}.")
            lines += [f"  {n.id} {n.title}: {n.reason} {n.action}".rstrip() for n in s.notes]
        return "\n".join(lines)

    def as_output(self) -> dict[str, Any]:
        """The cell output that comes before the result, so a run keeps its label."""
        return {"type": "translation", "dialect": "snowflake", "rules_version": self.rules_version,
                "runnable": self.runnable, "statements": [s.as_dict() for s in self.statements],
                "text": self.summary()}


# ---------------------------------------------------------------- notes

def _n(id: str, title: str, reason: str, action: str, source: str = "") -> Note:
    return Note(id, title, reason, action, source)


SFD_CASE = _n("SFD-001", "Unquoted names keep their case",
              "Snowflake stores an unquoted name in upper case (MyCol becomes MYCOL); DuckDB keeps it as typed.",
              "Code that reads INFORMATION_SCHEMA or compares names can differ; quote names to make them match.",
              f"{_DOCS}/identifiers-syntax")
SFD_TZ = _n("SFD-002", "TIMESTAMP_TZ loses its offset",
            "DuckDB stores TIMESTAMP_TZ as TIMESTAMPTZ, an instant, so the offset Snowflake keeps with each value "
            "is lost.", "Compare instants, not offsets.", f"{_DOCS}/data-types-datetime")
SFD_DIV = _n("SFD-003", "Division returns DOUBLE",
             "DuckDB returns DOUBLE for /, where Snowflake returns NUMBER with its own scale rules.",
             "Round or cast the result where scale matters; use DIV0 where a zero divisor is expected.",
             f"{_DOCS}/operators-arithmetic")
SFD_LTZ = _n("SFD-004", "Local time zone is this PC's",
             "TIMESTAMP_LTZ and CURRENT_TIMESTAMP use this PC's time zone here, not Snowflake's TIMEZONE parameter.",
             "Set the same time zone, or compare in UTC.", f"{_DOCS}/parameters#timezone")
SFD_DATE = _n("SFD-006", "Date arithmetic on a DATE returns a TIMESTAMP",
              "DATEADD and TIMESTAMPADD by a date unit (day and longer), and DATE_TRUNC, of a value whose type "
              "isn't known here keep a DATE as a DATE in Snowflake; here the result is a TIMESTAMP at "
              "midnight. The date is the same. Raised when the value may be a DATE (a column's type isn't "
              "known here); a value that is plainly a DATE (a cast, TO_DATE, CURRENT_DATE) is cast back to "
              "DATE instead, and a TIMESTAMP cast or function raises nothing.",
              "Cast the result to DATE where its type matters, such as a join or a written table.",
              f"{_DOCS}/functions/date_trunc")
SFD_BRACKET = _n("SFD-009", "[i] on a value whose type isn't known",
                 "[i] is 0-based in Snowflake. Here [i] on a table's column whose type the statement doesn't show "
                 "is taken for an ARRAY: a literal index such as [0] is moved to DuckDB's 1-based lists, and an "
                 "integer column or expression as the index isn't. On a JSON (VARIANT) column [0] then reads the "
                 "second element, and v['a']::string keeps the JSON quotes ('\"Pine\"', not 'Pine').",
                 "Cast the column in the query (v::variant, PARSE_JSON(v), or l::array) so its type is known, "
                 "or check the result.", f"{_DOCS}/data-types-semistructured")
SFD_TYPEOF = _n("SFD-007", "TYPEOF can't always name Snowflake's type",
                "TYPEOF gives Snowflake's type names here (SFX-009), with two exceptions. A JSON number with a "
                "fraction or an exponent that isn't read from PARSE_JSON of text written in the query (a "
                "column, or a path into one such as v:a) comes back as 'DECIMAL or DOUBLE': Snowflake tells "
                "them apart by how the number was written (1.912e2 is DOUBLE, 191.2 is DECIMAL) and DuckDB doesn't keep that. A type with no single "
                "Snowflake name keeps DuckDB's name (TIMESTAMP WITH TIME ZONE, which is TIMESTAMP_TZ or "
                "TIMESTAMP_LTZ in Snowflake; INTERVAL, UUID, STRUCT, MAP). Raised when the value's type isn't "
                "known here: a column, a path or a computed value.",
                "Don't branch on TYPEOF's text for those values in code meant for both; TYPEOF(PARSE_JSON(...)) "
                "and TYPEOF of a cast are named as in Snowflake.", f"{_DOCS}/functions/typeof")

SFD_APPROX = _n("SFD-010", "Approximate answers use DuckDB's estimators",
                 "APPROX_COUNT_DISTINCT, HLL and DATASKETCHES_HLL run as DuckDB's approx_count_distinct, and "
                 "APPROX_PERCENTILE as approx_quantile. Snowflake uses its own estimators, so an estimate can be "
                 "a little different: APPROX_PERCENTILE(c1, 0.5) over 0 to 10 is 5.5 in Snowflake and 5.0 here, "
                 "and DATASKETCHES_HLL gives 2.000000005 there where 2.0 is given here. DuckDB's distinct "
                 "estimate can be off even for a few values: 11 distinct values can count as 12.",
                 "Use COUNT(DISTINCT ...) or PERCENTILE_CONT where an exact answer matters.",
                 f"{_DOCS}/functions/approx_percentile")
SFD_LINEAR = _n("SFD-011", "INTERPOLATE_LINEAR gives a DOUBLE",
                "Snowflake's INTERPOLATE_LINEAR returns the input's type; here the filled value is a DOUBLE, "
                "because the input's type isn't known when the SQL is rewritten. Snowflake's docs don't say how "
                "it rounds a filled value for a whole-number column, so no rounding is done here.",
                "Cast the result (ROUND, or ::NUMBER) where a whole number is needed.",
                f"{_DOCS}/functions/interpolate_bfill")
SFD_NUMBER = _n("SFD-008", "A JSON fraction counts as a decimal",
                "Snowflake's PARSE_JSON keeps a number written with a fraction (123.12) as DECIMAL and one written "
                "with an exponent (1.912e2) as DOUBLE; DuckDB's JSON reads both as DOUBLE. So IS_DECIMAL, AS_DECIMAL "
                "and AS_NUMBER take any JSON number with a fraction or exponent as a decimal: for 1.912e2 they give "
                "TRUE and a number here, where Snowflake gives FALSE and NULL. A VARIANT made with TO_VARIANT keeps "
                "its type and is exact.",
                "Write JSON numbers meant as DOUBLE without an exponent check, or cast before testing.",
                f"{_DOCS}/functions/is_decimal")

_ONLY_IN_SNOWFLAKE = "Runs only in Snowflake"


def _refuse(id: str, title: str, reason: str, source: str = "") -> Note:
    return _n(id, title, reason, _ONLY_IN_SNOWFLAKE + ".", source)


# ---------------------------------------------------------------- reading


def parse(code: str) -> list[exp.Expression]:
    """Snowflake SQL statements as sqlglot reads them; ValueError when it can't be read."""
    try:
        statements = [cast(exp.Expression, s) for s in sqlglot.parse(code, read="snowflake") if s is not None]
    except ParseError as exc:
        first = str(exc).splitlines()[0] if str(exc) else "it isn't valid Snowflake SQL"
        raise ValueError(f"Could not read the query: {first}") from None
    if not statements:
        raise ValueError("The query is empty.")
    return statements


# Statements sqlglot keeps only as a command, by their first words.
_COMMANDS = {
    "CREATE STREAM": ("SFN-001", "Streams", "A stream tracks changes inside Snowflake; DuckDB has no equivalent."),
    "CREATE OR REPLACE STREAM": ("SFN-001", "Streams", "A stream tracks changes inside Snowflake; DuckDB has no "
                                                       "equivalent."),
    "CREATE TASK": ("SFN-002", "Tasks", "A task is scheduled by Snowflake; DuckDB has no scheduler."),
    "CREATE OR REPLACE TASK": ("SFN-002", "Tasks", "A task is scheduled by Snowflake; DuckDB has no scheduler."),
    "EXECUTE IMMEDIATE": ("SFN-005", "EXECUTE IMMEDIATE", "Dynamic SQL runs in Snowflake Scripting."),
    "BEGIN": ("SFN-004", "Snowflake Scripting", "Scripting blocks run only in Snowflake."),
    "DECLARE": ("SFN-004", "Snowflake Scripting", "Scripting blocks run only in Snowflake."),
    "CALL": ("SFN-004", "Stored procedures", "Procedures run only in Snowflake."),
    "GET": ("SFN-008", "Stages", "Files in stages live in Snowflake; a session on this PC reads no files."),
    "REMOVE": ("SFN-008", "Stages", "Files in stages live in Snowflake; a session on this PC reads no files."),
    "LIST": ("SFN-008", "Stages", "Files in stages live in Snowflake; a session on this PC reads no files."),
    "GRANT": ("SFN-010", "Access control", "Roles and grants exist only in Snowflake."),
    "REVOKE": ("SFN-010", "Access control", "Roles and grants exist only in Snowflake."),
}


# Context functions that describe the Snowflake account, session or client (SFN-014).
_ACCOUNT_FUNCTIONS = frozenset({
    "CURRENT_ACCOUNT", "CURRENT_ACCOUNT_NAME", "CURRENT_AVAILABLE_ROLES", "CURRENT_CLIENT", "CURRENT_IP_ADDRESS",
    "CURRENT_ORGANIZATION_NAME", "CURRENT_REGION", "CURRENT_ROLE_TYPE", "CURRENT_SECONDARY_ROLES", "CURRENT_SESSION",
    "CURRENT_STATEMENT", "CURRENT_TRANSACTION", "CURRENT_WAREHOUSE", "LAST_QUERY_ID"})


# Functions that build, merge or read a sketch or bitmap state in Snowflake's own format (SFN-016).
# Text search, collation, Snowflake's own hash and XML: features DuckDB doesn't have (SFN-017).
_TEXT_FEATURES = {
    "SEARCH": "SEARCH and SEARCH_IP match with Snowflake's search analyzers, which DuckDB doesn't have.",
    "SEARCH_IP": "SEARCH and SEARCH_IP match with Snowflake's search analyzers, which DuckDB doesn't have.",
    "COLLATE": "Snowflake's collation specifications ('en-ci', for example) have no DuckDB equivalent.",
    "HASH_AGG": "HASH_AGG uses Snowflake's own hash, which DuckDB's hash doesn't reproduce.",
    "CHECK_XML": "DuckDB has no XML type.", "PARSE_XML": "DuckDB has no XML type.",
    "XMLGET": "DuckDB has no XML type.", "TO_XML": "DuckDB has no XML type."}


_SKETCH_FUNCTIONS = frozenset({
    "HLL_ACCUMULATE", "HLL_COMBINE", "HLL_ESTIMATE", "HLL_EXPORT", "HLL_IMPORT", "DATASKETCHES_HLL_ACCUMULATE",
    "DATASKETCHES_HLL_COMBINE", "DATASKETCHES_HLL_ESTIMATE", "APPROX_TOP_K", "APPROX_TOP_K_ACCUMULATE",
    "APPROX_TOP_K_COMBINE", "APPROX_TOP_K_ESTIMATE", "APPROX_PERCENTILE_ACCUMULATE", "APPROX_PERCENTILE_COMBINE",
    "APPROX_PERCENTILE_ESTIMATE", "MINHASH", "MINHASH_COMBINE", "APPROXIMATE_SIMILARITY",
    "APPROXIMATE_JACCARD_INDEX", "BITMAP_CONSTRUCT_AGG", "BITMAP_COUNT", "BITMAP_OR_AGG"})


_VARIANT_TIME_ZONES = frozenset({"IS_TIMESTAMP_LTZ", "IS_TIMESTAMP_TZ", "AS_TIMESTAMP_LTZ", "AS_TIMESTAMP_TZ"})


def _function_name(node: exp.Func) -> str:
    """The name Snowflake SQL calls ``node`` by."""
    return (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).upper()


def _not_translatable(statement: exp.Expression) -> Note | None:
    """Why ``statement`` runs only in Snowflake, or None."""
    if isinstance(statement, exp.Command):
        rest = statement.expression
        rest = rest.name if isinstance(rest, exp.Expression) else str(rest or "")
        words = " ".join(f"{statement.name} {rest}".upper().split())
        for start, (id, title, reason) in _COMMANDS.items():
            if words == start or words.startswith(start + " "):
                return _refuse(id, title, reason)
        return _refuse("SFN-099", "Not a statement DuckDB can run", f"{words.split(' ')[0] or 'It'} is a Snowflake "
                       "command KratosForge doesn't rewrite.")
    if isinstance(statement, exp.Create):
        kind = (statement.args.get("kind") or "").upper()
        props = statement.args.get("properties")
        names = {type(p).__name__ for p in (props.expressions if props else [])}
        if "DynamicProperty" in names or any("TARGET_LAG" in p.sql("snowflake").upper()
                                             for p in (props.expressions if props else [])):
            return _refuse("SFN-003", "Dynamic tables", "Snowflake keeps a dynamic table up to date itself. Its "
                           "SELECT can be tried here on its own.", f"{_DOCS}/sql/create-dynamic-table")
        if kind in ("PROCEDURE", "FUNCTION"):
            return _refuse("SFN-004", "Procedures and functions", "Snowflake procedures and user-defined functions "
                           "(SQL, Python, Java, JavaScript, Snowflake Scripting) run only in Snowflake.")
        if statement.args.get("clone"):
            return _refuse("SFN-007", "Clones", "A zero-copy clone exists only in Snowflake.")
        if kind in ("STAGE", "PIPE", "WAREHOUSE", "ROLE", "USER", "SHARE", "STREAM", "TASK", "INTEGRATION",
                    "FILE FORMAT", "SEQUENCE", "MASKING POLICY", "ROW ACCESS POLICY", "TAG"):
            return _refuse("SFN-011", f"{kind.title()} objects", f"A {kind.lower()} exists only in Snowflake.")
    if isinstance(statement, (exp.Grant, exp.Revoke)):
        return _refuse("SFN-010", "Access control", "Roles and grants exist only in Snowflake.")
    if isinstance(statement, exp.Insert) and statement.args.get("overwrite"):
        return _refuse("SFN-020", "INSERT OVERWRITE", "Snowflake empties the table and inserts in one statement "
                       "inside the current transaction; DuckDB has no INSERT OVERWRITE, and a DELETE then INSERT "
                       "written here wouldn't be one statement.", f"{_DOCS}/sql/insert")
    if isinstance(statement, exp.Undrop):
        return _refuse("SFN-007", "UNDROP", "Restoring a dropped object uses Snowflake's Time Travel.")
    if isinstance(statement, (exp.Copy, exp.Put)):
        return _refuse("SFN-008", "Stages", "COPY INTO and PUT move files through Snowflake stages; a session on "
                       "this PC reads no files.")
    if isinstance(statement, exp.Use):
        return _refuse("SFN-009", "USE", "Warehouses, roles, databases and schemas are chosen in Snowflake; the "
                       "session on this PC has one database.")
    if isinstance(statement, (exp.Show, exp.Describe)):
        return _refuse("SFN-009", "SHOW and DESCRIBE", "These list Snowflake's own objects.")
    if isinstance(statement, exp.Alter):
        text = statement.sql("snowflake").upper()
        if any(k in text for k in (" SET MASKING POLICY", " ROW ACCESS POLICY", " SET TAG", " SUSPEND", " RESUME")) \
                or (statement.args.get("kind") or "").upper() in ("WAREHOUSE", "STAGE", "TASK", "PIPE", "STREAM"):
            return _refuse("SFN-010", "Snowflake settings", "This changes something that exists only in Snowflake.")
    for node in statement.walk():
        if isinstance(node, exp.HistoricalData):
            return _refuse("SFN-006", "Time travel", "AT and BEFORE read an earlier version kept by Snowflake.",
                           f"{_DOCS}/constructs/at-before")
        if isinstance(node, exp.Var) and str(node.name).startswith("@"):
            return _refuse("SFN-008", "Stages", "A stage is a Snowflake file location; a session on this PC reads "
                           "no files.")
        if isinstance(node, exp.Func) and _function_name(node) in _VARIANT_TIME_ZONES:
            return _refuse("SFN-015", f"{_function_name(node)}: VARIANT timestamps with a time zone",
                           "DuckDB keeps one kind of timestamp with a time zone, so a VARIANT here can't tell "
                           "TIMESTAMP_LTZ from TIMESTAMP_TZ.", f"{_DOCS}/functions/is")
        if isinstance(node, (exp.Func, exp.Collate)) and _function_name(node) in _TEXT_FEATURES:
            return _refuse("SFN-017", f"{_function_name(node)}: a Snowflake feature DuckDB lacks",
                           _TEXT_FEATURES[_function_name(node)], f"{_DOCS}/functions/{_function_name(node).lower()}")
        if isinstance(node, (exp.ToDecfloat, exp.TryToDecfloat)) or (
                isinstance(node, exp.DataType) and node.this == exp.DataType.Type.DECFLOAT):
            return _refuse("SFN-018", "DECFLOAT", "DuckDB has no decimal floating-point type to hold Snowflake's "
                           "DECFLOAT (38 significant digits with an exponent).", f"{_DOCS}/functions/to_decfloat")
        if isinstance(node, exp.MatchRecognize):
            return _refuse("SFN-019", "MATCH_RECOGNIZE", "DuckDB has no row pattern matching.",
                           f"{_DOCS}/constructs/match_recognize")
        if isinstance(node, exp.Func) and _function_name(node) in _SKETCH_FUNCTIONS:
            return _refuse("SFN-016", f"{_function_name(node)}: Snowflake sketch and bitmap states",
                           "It builds, merges or reads a sketch or bitmap in Snowflake's own format, which DuckDB "
                           "can't make or read. HLL, DATASKETCHES_HLL, APPROX_COUNT_DISTINCT and APPROX_PERCENTILE "
                           "run here; COUNT(DISTINCT ...) gives what BITMAP_COUNT(BITMAP_CONSTRUCT_AGG(...)) "
                           "counts.", f"{_DOCS}/functions-aggregation")
        if isinstance(node, exp.Func) and _function_name(node) in _ACCOUNT_FUNCTIONS:
            return _refuse("SFN-014", f"{_function_name(node)}: Snowflake account and session",
                           "It reads the Snowflake account, session or client running the query; this PC has none.",
                           f"{_DOCS}/functions-context")
        if isinstance(node, exp.Dot) and node.sql("snowflake").upper().startswith("SNOWFLAKE.CORTEX."):
            return _refuse("SFN-012", "Cortex AI functions", "Cortex runs models in Snowflake.")
        if isinstance(node, exp.Cast) and node.to.this == exp.DataType.Type.TIMESTAMPTZ \
                and isinstance(node.this, exp.Literal) and node.this.is_string \
                and re.search(r"\s[+-]\d{2}:?\d{2}$", node.this.name):
            return _refuse("SFN-013", "TIMESTAMP_TZ text with an offset", "DuckDB can't read an offset written "
                           "after a space ('2026-01-01 10:00:00 +05:00'): it takes it for a time zone name.",
                           f"{_DOCS}/data-types-datetime")
    return None


# ---------------------------------------------------------------- rewrites (SFX-*)


class _Untranslatable(Exception):
    def __init__(self, note: Note):
        super().__init__(note.title)
        self.note = note


def _duck(node: exp.Expression) -> str:
    from .sql import KfDuckDB  # DuckDB 2.0 refuses "x -> body" lambdas (#1341)
    return node.sql(dialect=KfDuckDB, unsupported_level=sqlglot.ErrorLevel.RAISE)


_FLATTEN_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[0-9]+\])*(\.[A-Za-z_][A-Za-z0-9_]*(\[[0-9]+\])*)*")


def _flatten_parts(node: exp.Expression) -> tuple[exp.Expression, bool, str] | None:
    """The input, OUTER and PATH of a FLATTEN call sqlglot read as Explode, or None for another table."""
    explode = node.this if isinstance(node, (exp.Lateral, exp.TableFromRows)) else None
    if not isinstance(explode, exp.Explode):
        return None
    source, outer, path = None, bool(node.args.get("outer")), ""
    for arg in [explode.this, *explode.expressions]:
        if isinstance(arg, exp.Kwarg):
            key = arg.this.name.lower()
            if key == "input":
                source = arg.expression
            elif key == "outer":
                outer = isinstance(arg.expression, exp.Boolean) and bool(arg.expression.this)
            elif key == "path":
                given = arg.expression
                if not (isinstance(given, exp.Literal) and given.is_string and _FLATTEN_PATH.fullmatch(given.this)):
                    raise _Untranslatable(_refuse("SFX-001", "FLATTEN with this PATH",
                                                  "Only a PATH of plain names and [n] (b, c.d, b[0]) is rewritten.",
                                                  f"{_DOCS}/functions/flatten"))
                path = given.this
            else:
                raise _Untranslatable(_refuse("SFX-001", f"FLATTEN with {key.upper()}",
                                              f"FLATTEN's {key.upper()} argument has no DuckDB equivalent.",
                                              f"{_DOCS}/functions/flatten"))
        elif source is None:
            source = arg
    return (source, outer, path) if source is not None else None


def _flattened(source: exp.Expression, path: str, outer: bool) -> exp.Expression:
    """FLATTEN's rows as a subquery over json_each, with Snowflake's columns (KEY, PATH, INDEX, VALUE, THIS):
    KEY is NULL for an array element and INDEX for an object field, and PATH is written as Snowflake
    writes it. Only these columns come out, so json_each's own (id, type, parent...) can't clash with the
    person's. With OUTER, a value with nothing to flatten gives one row: NULL KEY, INDEX and VALUE, and the
    value as THIS."""
    from .sql import _with_args
    this = source.copy()
    if path:
        this = _with_args(f"json_extract(CAST(fj_arg0 AS JSON), '{'$' if path.startswith('[') else '$.'}{path}')",
                          [this])
    array = "json_type(CAST(fj_arg0 AS JSON)) = 'ARRAY'"
    field = "__sf_e.key" if not path else f"'{path}.' || __sf_e.key"
    return _with_args(f"(SELECT CASE WHEN {array} THEN NULL ELSE __sf_e.key END AS key, "
                      f"CASE WHEN {array} THEN '{path}[' || __sf_e.key || ']' ELSE {field} END AS path, "
                      f"CASE WHEN {array} THEN CAST(__sf_e.key AS INT) END AS index, __sf_e.value AS value, "
                      "CAST(fj_arg0 AS JSON) AS this FROM json_each(fj_arg0) AS __sf_e"
                      + (f" UNION ALL SELECT NULL, '{path}', NULL, NULL, CAST(fj_arg0 AS JSON) WHERE NOT EXISTS "
                         "(SELECT 1 FROM json_each(fj_arg0))" if outer else "") + ")", [this])


def _rewrite_flatten(statement: exp.Expression) -> None:
    """SFX-001: LATERAL FLATTEN and TABLE(FLATTEN(...)) become a lateral subquery over DuckDB's json_each
    with Snowflake's columns and OUTER rule (see _flattened)."""
    for select in list(statement.find_all(exp.Select)):
        source_node = select.args.get("from_")
        joins = select.args.get("joins") or []
        aliases: list[str] = []
        items: list[tuple[int | None, exp.Expression]] = [(None, source_node.this)] if source_node is not None else []
        items += [(i, j.this) for i, j in enumerate(joins)]
        for i, item in items:
            parts = _flatten_parts(item)
            if parts is None:
                continue
            source, outer, path = parts
            alias_node = item.args.get("alias")
            alias = alias_node.name if alias_node is not None and alias_node.name else "flatten"
            _duck(source)  # refused here when DuckDB can't take the input
            rows = exp.Subquery(this=_flattened(source, path, outer).unnest(),
                                alias=exp.TableAlias(this=exp.to_identifier(alias)))
            if i is None:
                if source_node is not None:
                    source_node.set("this", rows)
            else:
                joins[i] = sqlglot.parse_one("SELECT 1 FROM _y CROSS JOIN LATERAL (SELECT 1) AS _x",
                                             read="duckdb").args["joins"][0]
                lateral = joins[i].this
                lateral.set("this", exp.Subquery(this=rows.this))
                lateral.set("alias", rows.args["alias"])
            aliases.append(alias.lower())
        if not aliases:
            continue
        for col in list(select.find_all(exp.Column)):
            table = col.table.lower()
            if col.name.lower() == "seq" and (table in aliases or (not table and len(aliases) == 1)):
                raise _Untranslatable(_refuse("SFX-001", "FLATTEN's SEQ column", "json_each has no SEQ column.",
                                              f"{_DOCS}/functions/flatten"))


# Snowflake date and time format elements -> strftime, longest first. Matched without case.
_FORMAT = [("YYYY", "%Y"), ("MMMM", "%B"), ("HH24", "%H"), ("HH12", "%I"), ("FF9", "%n"), ("FF6", "%f"),
           ("FF3", "%g"), ("MON", "%b"), ("YY", "%y"), ("MM", "%m"), ("DD", "%d"), ("DY", "%a"), ("HH", "%H"),
           ("MI", "%M"), ("SS", "%S"), ("AM", "%p"), ("PM", "%p")]
_FORMAT_KEEP = set(" -/:.,_T")


def _strftime(model: str) -> str | None:
    """A Snowflake date/time format model as strftime, or None when part of it has no equivalent."""
    out, i = [], 0
    while i < len(model):
        if model[i] == '"':
            end = model.find('"', i + 1)
            if end < 0:
                return None
            out.append(model[i + 1:end].replace("%", "%%"))
            i = end + 1
            continue
        for element, directive in _FORMAT:
            if model[i:i + len(element)].upper() == element:
                out.append(directive)
                i += len(element)
                break
        else:
            if model[i] not in _FORMAT_KEEP:
                return None
            out.append(model[i])
            i += 1
    return "".join(out)


def _plainly_a_date(value: exp.Expression | None) -> bool:
    return value is not None and (value.is_type(exp.DataType.Type.DATE) or isinstance(value, exp.CurrentDate))


def _date_trunc_of_a_date(node: exp.Expr) -> bool:
    """DATE_TRUNC, DATEADD or TIMESTAMPADD by a unit of a day or longer on a value that is plainly a DATE:
    Snowflake gives a DATE back."""
    return (isinstance(node, (exp.DateTrunc, exp.DateAdd)) and _plainly_a_date(node.this)
            and str(getattr(node.args.get("unit"), "name", "") or "").upper() in _DATE_UNITS)


def _rewrite_date_trunc(statement: exp.Expression) -> None:
    """DATE_TRUNC and DATEADD of a DATE by a date unit stay a DATE, as in Snowflake (DuckDB gives a
    TIMESTAMP at midnight for DATE + INTERVAL; sqlglot 30.25 keeps DATE_TRUNC a DATE itself, earlier
    versions don't). Here every version gives a DATE (#1252, #1319)."""
    for node in [n for n in statement.find_all(exp.DateTrunc, exp.DateAdd) if _date_trunc_of_a_date(n)]:
        if isinstance(node.parent, exp.Cast) and node.parent.to.is_type(exp.DataType.Type.DATE):
            continue
        kept = node.copy()
        if isinstance(kept, exp.DateTrunc):
            kept.set("input_type_preserved", None)
        node.replace(exp.cast(kept, exp.DataType.Type.DATE))


def _rewrite_to_char(statement: exp.Expression) -> None:
    """SFX-002 and SFX-003: TO_VARCHAR / TO_CHAR with a format model."""
    ours: list[exp.Expression] = []   # rewritten here, so quoted text in them is meant
    for node in list(statement.find_all(exp.ToChar)):
        fmt = node.args.get("format")
        if fmt is None:
            continue
        if not (isinstance(fmt, exp.Literal) and fmt.is_string):
            raise _Untranslatable(_refuse("SFX-002", "TO_VARCHAR with a computed format",
                                          "Only a format written in the query can be rewritten."))
        model = fmt.name
        if re.search(r"[90]", model) or re.fullmatch(r"[\s$,.SMIXGDLB]*", model.upper()):
            raise _Untranslatable(_refuse("SFX-003", "Number format models",
                                          f"The number format '{model}' has no DuckDB equivalent.",
                                          f"{_DOCS}/sql-format-models"))
        directive = _strftime(model)
        if directive is None:
            raise _Untranslatable(_refuse("SFX-002", "Date or time format",
                                          f"Part of the format '{model}' has no DuckDB equivalent.",
                                          f"{_DOCS}/functions-conversion#date-and-time-formats-in-conversion-functions"))
        ours.append(exp.TimeToStr(this=exp.cast(node.this, exp.DataType.Type.TIMESTAMP),
                                  format=exp.Literal.string(directive)))
        node.replace(ours[-1])
    # sqlglot rewrites a format itself when it knows the argument is a date or time; whatever it
    # couldn't map stays as letters (Q for the quarter, quoted text), so those are refused, not printed.
    for t2s in list(statement.find_all(exp.TimeToStr)):
        fmt = t2s.args.get("format")
        if any(t2s is mine for mine in ours):
            continue
        if isinstance(fmt, exp.Literal) and re.search(r"(?<!%)[A-Za-z]", re.sub(r"%[A-Za-z%]", "", fmt.name)):
            raise _Untranslatable(_refuse("SFX-002", "Date or time format",
                                          "Part of the format has no DuckDB equivalent.",
                                          f"{_DOCS}/functions-conversion#date-and-time-formats-in-conversion-functions"))


def _object_key(key: exp.Expression) -> exp.Expression:
    """A key as text: an OBJECT constant's {'a': 1} reads its key as a name, which would be a column."""
    return exp.Literal.string(key.name) if isinstance(key, exp.Identifier) else key


def _rewrite_object_construct(statement: exp.Expression) -> None:
    """SFX-004: OBJECT_CONSTRUCT drops pairs whose value is SQL NULL and keeps JSON nulls. Keys are put in
    order, as Snowflake keeps them, so two objects with the same pairs are equal however they were written."""
    for _node in list(statement.find_all(exp.StarMap)):
        raise _Untranslatable(_refuse("SFX-004", "OBJECT_CONSTRUCT(*)", "Building an object from every column "
                                      "isn't rewritten.", f"{_DOCS}/functions/object_construct"))
    for node in list(statement.find_all(exp.Struct)):
        pairs = node.expressions
        if not pairs:
            node.replace(sqlglot.parse_one("'{}'::JSON", read="duckdb"))
            continue
        if not all(isinstance(p, exp.PropertyEQ) for p in pairs):
            continue
        entries = ", ".join(f"{{'k': {_duck(_object_key(p.this))}, 'v': to_json({_duck(p.expression)})}}"
                            for p in pairs)
        node.replace(sqlglot.parse_one(
            f"to_json(map_from_entries(list_sort(list_filter([{entries}], x -> x.v IS NOT NULL))))", read="duckdb"))


def _rewrite_soundex(statement: exp.Expression) -> None:
    """SFX-006: SOUNDEX is American Soundex in Snowflake, as in Spark, so it takes the DuckDB engine's
    Spark template (#1180)."""
    from .sql import _ANON_TEMPLATES, _from_template

    for node in list(statement.find_all(exp.Soundex)):
        node.replace(_from_template(_ANON_TEMPLATES["SOUNDEX"], [node.this]))


def _rewrite_values(statement: exp.Expression) -> None:
    """SFX-005: a VALUES list in FROM names its columns COLUMN1, COLUMN2... in Snowflake; DuckDB names
    them col0, col1..., so a query that reads COLUMN1 would fail. An alias with column names is kept."""
    for node in list(statement.find_all(exp.Values)):
        if isinstance(node.parent, exp.Insert) or not node.expressions:
            continue
        alias = node.args.get("alias")
        if alias is not None and alias.columns:
            continue
        first = node.expressions[0]
        width = len(first.expressions) if isinstance(first, exp.Tuple) else 1
        node.set("alias", exp.TableAlias(this=alias.this if alias is not None else exp.to_identifier("_values"),
                                         columns=[exp.to_identifier(f"column{i}") for i in range(1, width + 1)]))


def _rewrite_ratio_to_report(statement: exp.Expression) -> None:
    """SFX-007: RATIO_TO_REPORT(x) OVER (PARTITION BY p) is x over the partition's sum, and null when x is
    null or the sum is 0. Snowflake's docs say an ORDER BY in the window doesn't change the result, so it's
    dropped; a window frame isn't allowed there."""
    for node in list(statement.find_all(exp.Window)):
        func = node.this
        if not (isinstance(func, exp.Anonymous) and str(func.this).upper() == "RATIO_TO_REPORT"):
            continue
        if len(func.expressions) != 1 or node.args.get("spec") is not None:
            raise _Untranslatable(_refuse("SFX-007", "RATIO_TO_REPORT", "Only RATIO_TO_REPORT(expr) OVER "
                                          "(PARTITION BY ...) is rewritten.", f"{_DOCS}/functions/ratio_to_report"))
        value = _duck(func.expressions[0])
        partition = node.args.get("partition_by") or []
        over = f"PARTITION BY {', '.join(_duck(p) for p in partition)}" if partition else ""
        node.replace(sqlglot.parse_one(f"(({value}) / NULLIF(SUM({value}) OVER ({over}), 0))", read="duckdb"))


_EARTH_RADIUS_KM = 6371


def _soundex_p123_template() -> str:
    """SOUNDEX's template with one change: the second letter keeps its number even when it is the
    first letter's (Pfister is P123, not P236)."""
    from .sql import _ANON_TEMPLATES
    template = _ANON_TEMPLATES["SOUNDEX"]
    old = "kf_v <> '0' AND kf_v <> kf_c[kf_i]"
    if old not in template:  # sql.py's SOUNDEX changed: fail loudly rather than give SOUNDEX's codes
        raise RuntimeError("SOUNDEX_P123 can't be built from the SOUNDEX template")
    return template.replace(old, "kf_v <> '0' AND (kf_i = 1 OR kf_v <> kf_c[kf_i])")


def _binary_valued(node: exp.Expression) -> bool:
    """Plainly BINARY: X'..', a cast to BINARY, or a function that returns BINARY."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, (exp.HexString, exp.ByteString)):
        return True
    if isinstance(node, (exp.Cast, exp.TryCast)):
        return node.to.this in (exp.DataType.Type.BINARY, exp.DataType.Type.VARBINARY, exp.DataType.Type.BLOB)
    return isinstance(node, (exp.Unhex, exp.FromBase64, exp.TryBase64DecodeBinary, exp.TryHexDecodeBinary)) or (
        type(node).__name__ in ("Base64DecodeBinary", "HexDecodeBinary", "ToBinary", "TryToBinary"))


def _rewrite_time(statement: exp.Expression) -> None:
    """SFX-017: TIME(x) is TO_TIME(x): the time of a string or of a timestamp. sqlglot writes it as a cast
    through TIMESTAMPTZ at an empty time zone, which DuckDB can't read."""
    for node in list(statement.find_all(exp.Time)):
        if node.this is None:  # TIME() with no argument: left for sqlglot, which refuses it (SFX-000)
            continue
        value = node.this.copy()
        if _zoned(value):  # DuckDB has no TIMESTAMPTZ -> TIME cast; TIMESTAMP is the session zone's local time
            value = exp.Cast(this=value, to=exp.DataType.build("TIMESTAMP"))
        node.replace(exp.Cast(this=value, to=exp.DataType.build("TIME")))


def _zoned(node: exp.Expression) -> bool:
    """Whether ``node`` is plainly a timestamp with a time zone in DuckDB: CURRENT_TIMESTAMP or a cast to
    TIMESTAMP_TZ or TIMESTAMP_LTZ (SYSDATE and CONVERT_TIMEZONE already give a TIMESTAMP there)."""
    if isinstance(node, exp.CurrentTimestamp):
        return not node.args.get("sysdate")
    return isinstance(node, exp.Cast) and node.to.this in (exp.DataType.Type.TIMESTAMPTZ,
                                                           exp.DataType.Type.TIMESTAMPLTZ)


def _rewrite_strings(statement: exp.Expression) -> None:
    """SFX-015: string, binary and number functions DuckDB has under other names or not at all.
    OCTET_LENGTH counts bytes (strlen of text, octet_length of BINARY; a column's type isn't known, so it
    is taken for text and a BINARY column stops with DuckDB's error rather than a wrong count).
    MD5_NUMBER_LOWER64 and _UPPER64 read the last and first 16 hex digits of MD5 as an unsigned number.
    WIDTH_BUCKET follows Snowflake's rules: 0 below min, num_buckets + 1 at or above max, NULL when any
    argument is; min_value at or above max_value stops with an error rather than give a silent 0. TRY_BASE64_DECODE_* and TRY_HEX_DECODE_* give NULL where the text doesn't decode (an
    odd number of hex digits too). SOUNDEX_P123 keeps the second letter's number. Arguments are worked
    out more than once, which matters only when they aren't deterministic."""
    def sql(node: exp.Expression) -> str:
        return _duck(node)

    def put(node: exp.Expr, text: str) -> None:
        node.replace(sqlglot.parse_one(text, read="duckdb"))

    for length in list(statement.find_all(exp.ByteLength)):
        value = length.this
        put(length, f"octet_length({sql(value)})" if _binary_valued(value) else f"strlen(CAST({sql(value)} AS VARCHAR))")
    for md5 in list(statement.find_all(exp.MD5NumberLower64, exp.MD5NumberUpper64)):
        digits = "right" if isinstance(md5, exp.MD5NumberLower64) else "left"
        put(md5, f"CAST('0x' || {digits}(md5(CAST({sql(md5.this)} AS VARCHAR)), 16) AS UBIGINT)")
    for bucket in list(statement.find_all(exp.WidthBucket)):
        args = [bucket.this, bucket.args.get("min_value"), bucket.args.get("max_value"), bucket.args.get("num_buckets")]
        if any(a is None for a in args):
            raise _Untranslatable(_refuse("SFX-015", "WIDTH_BUCKET", "WIDTH_BUCKET takes a value, min_value, "
                                          "max_value and num_buckets.", f"{_DOCS}/functions/width_bucket"))
        e, lo, hi, n = (f"({sql(a)})" for a in args if a is not None)
        put(bucket, f"(CASE WHEN {e} IS NULL OR {lo} IS NULL OR {hi} IS NULL OR {n} IS NULL THEN NULL "
                  f"WHEN {lo} >= {hi} THEN error('WIDTH_BUCKET here needs min_value below max_value') "
                  f"WHEN {e} < {lo} THEN 0 WHEN {e} >= {hi} THEN {n} + 1 "
                  f"ELSE CAST(floor(CAST({e} - {lo} AS DOUBLE) * {n} / CAST({hi} - {lo} AS DOUBLE)) AS BIGINT) + 1 END)")
    decoders = {exp.TryBase64DecodeBinary: "try(from_base64({0}))",
                exp.TryBase64DecodeString: "try(decode(from_base64({0})))",
                exp.TryHexDecodeBinary: "(CASE WHEN length({0}) % 2 = 0 THEN try(unhex({0})) END)",
                exp.TryHexDecodeString: "(CASE WHEN length({0}) % 2 = 0 THEN try(decode(unhex({0}))) END)"}
    for decoder in list(statement.find_all(*decoders)):
        if any(decoder.args.get(k) is not None for k in decoder.args if k != "this"):
            raise _Untranslatable(_refuse("SFX-015", f"{_function_name(decoder)} with an alphabet", "The "
                                          "alphabet argument isn't rewritten; the default (+, / and =) is.",
                                          f"{_DOCS}/functions/{_function_name(decoder).lower()}"))
        put(decoder, decoders[type(decoder)].format(f"CAST({sql(decoder.this)} AS VARCHAR)"))
    from .sql import _from_template
    for soundex in list(statement.find_all(exp.SoundexP123)):
        soundex.replace(_from_template(_soundex_p123_template(), [soundex.this]))


def _entries(obj: str) -> str:
    return f"map_entries(CAST({obj} AS MAP(VARCHAR, JSON)))"


def _object(entries: str) -> str:
    """An object from {key, value} entries, keys in order as Snowflake keeps them."""
    return f"to_json(map_from_entries(list_sort({entries})))"


def _as_json(value: str) -> str:
    """``value`` as JSON whatever its type: a VARIANT round trip gives 21 for 21 and "Joe" for 'Joe'."""
    return f"CAST(CAST({value} AS VARIANT) AS JSON)"


def _bind(names: list[str], body: str) -> str:
    """A template in which the arguments (fj_arg0, fj_arg1...) are worked out once, as __sf_a.<name>, and
    ``body`` reads them there: a nested call would otherwise repeat its argument at every level."""
    fields = ", ".join(f"'{name}': fj_arg{i}" for i, name in enumerate(names))
    return f"list_extract(list_transform([{{{fields}}}], __sf_a -> {body}), 1)"


def _put(node: exp.Expr, template: str, args: list[exp.Expression]) -> None:
    """``node`` becomes ``template`` with the argument nodes put in (not their text, so a deep nest of
    calls isn't read again from SQL)."""
    from .sql import _with_args
    node.replace(_with_args(template, args))


def _object_refusal(name: str, why: str) -> _Untranslatable:
    return _Untranslatable(_refuse("SFX-016", name, why, f"{_DOCS}/functions/{name.lower()}"))


def _object_insert(insert: exp.ObjectInsert) -> None:
    args = [insert.args["this"], insert.args["key"], insert.args["value"]]
    obj, key, value = "__sf_a.o", "__sf_a.k", "__sf_a.v"
    flag = insert.args.get("update_flag")
    entries = (f"list_append(list_filter({_entries(obj)}, __sf_e -> __sf_e.key <> {key}), "
               f"{{'key': CAST({key} AS VARCHAR), 'value': {_as_json(value)}}})")
    exists = f"list_contains(map_keys(CAST({obj} AS MAP(VARCHAR, JSON))), {key})"
    duplicate = f"error(concat('Duplicate field key ', {key}))"
    names = ["o", "k", "v"]
    if isinstance(flag, exp.Boolean) and flag.this is True:
        guard = ""
    elif flag is not None:
        guard = f"WHEN {exists} AND NOT coalesce(__sf_a.u, FALSE) THEN {duplicate} "
        names.append("u")
        args.append(flag)
    else:
        guard = f"WHEN {exists} THEN {duplicate} "
    _put(insert, _bind(names, f"CASE WHEN {key} IS NULL OR {value} IS NULL THEN {obj} {guard}"
                              f"ELSE {_object(entries)} END"), args)


def _object_agg(agg: exp.ObjectAgg) -> None:
    if isinstance(agg.this, exp.Distinct) or agg.expression is None:
        raise _object_refusal("OBJECT_AGG", "OBJECT_AGG(DISTINCT ...) isn't rewritten; OBJECT_AGG(key, value) is.")
    from .sql import _with_args
    listed = _with_args("list({'key': CAST(fj_arg0 AS VARCHAR), 'value': " + _as_json("fj_arg1") + "}) "
                        "FILTER (WHERE fj_arg0 IS NOT NULL AND fj_arg1 IS NOT NULL)", [agg.this, agg.expression])
    target = _in_window(agg)  # OBJECT_AGG(...) OVER (...): the list is the window's, the object built outside it
    if target is not agg:
        agg.replace(listed)
        listed = target.copy()
    _put(target, _object("fj_arg0"), [listed])


def _object_function(node: exp.Anonymous) -> None:
    name = str(node.this).upper()
    args = list(node.expressions)
    if name == "OBJECTAGG":
        raise _object_refusal("OBJECT_AGG", "Write OBJECT_AGG; the OBJECTAGG spelling isn't rewritten.")
    if name in ("OBJECT_DELETE", "OBJECT_PICK"):
        if len(args) < 2:
            raise _object_refusal(name, f"{name} takes an object and at least one key.")
        # OBJECT_PICK(obj, array): only a value plainly an array is taken for one; a single column or other
        # expression is a key (a key column is the usual case, and an ARRAY column stops with DuckDB's error)
        one_array = len(args) == 2 and name == "OBJECT_PICK" and isinstance(args[1], (exp.Array, exp.ParseJSON))
        listed = ("CAST(fj_arg1 AS VARCHAR[])" if one_array
                  else "[" + ", ".join(f"fj_arg{i}" for i in range(1, len(args))) + "]")
        keep = "" if name == "OBJECT_PICK" else "NOT "
        _put(node, _object(f"list_filter({_entries('fj_arg0')}, __sf_e -> {keep}list_contains({listed}, "
                           "__sf_e.key))"), args)
    elif name == "TO_OBJECT":
        if len(args) != 1:
            raise _object_refusal(name, "TO_OBJECT takes one value.")
        value = "CAST(__sf_a.v AS JSON)"
        _put(node, _bind(["v"], f"CASE WHEN {value} IS NULL OR json_type({value}) = 'NULL' THEN NULL "
                                f"WHEN json_type({value}) = 'OBJECT' THEN {value} "
                                "ELSE error('TO_OBJECT needs an OBJECT') END"), args)
    elif name == "ARRAYS_TO_OBJECT":
        if len(args) != 2:
            raise _object_refusal(name, "ARRAYS_TO_OBJECT takes a key array and a value array.")
        ks, vs = "__sf_a.k", "__sf_a.v"
        value = _as_json(f"list_extract({vs}, __sf_i)")
        entries = (f"list_filter(list_transform(range(1, len({ks}) + 1), __sf_i -> {{'key': "
                   f"CAST(list_extract({ks}, __sf_i) AS VARCHAR), 'value': {value}}}), __sf_e -> __sf_e.key IS NOT NULL)")
        _put(node, _bind(["k", "v"], f"CASE WHEN {ks} IS NULL OR {vs} IS NULL THEN NULL WHEN len({ks}) <> len({vs}) "
                                     "THEN error('Key array and value array had unequal lengths in ARRAYS_TO_OBJECT') "
                                     f"ELSE {_object(entries)} END"), args)


_OBJECT_FUNCTIONS = ("OBJECT_DELETE", "OBJECT_PICK", "TO_OBJECT", "ARRAYS_TO_OBJECT", "OBJECTAGG")


def _rewrite_objects(statement: exp.Expression) -> None:
    """SFX-016: OBJECT_DELETE, OBJECT_PICK, OBJECT_INSERT, OBJECT_AGG, TO_OBJECT and ARRAYS_TO_OBJECT on
    DuckDB JSON, through a MAP of JSON values, with Snowflake's NULL rules: OBJECT_INSERT and
    ARRAYS_TO_OBJECT leave out a SQL NULL key, OBJECT_INSERT and OBJECT_AGG a SQL NULL value (JSON null is
    kept); an existing key in OBJECT_INSERT without updateFlag, a repeated key in OBJECT_AGG and arrays of
    unequal length stop with an error. Inner calls are rewritten first, so an outer one takes them as
    they now are."""
    found = [n for n in statement.find_all(exp.ObjectInsert, exp.ObjectAgg, exp.Anonymous)
             if not isinstance(n, exp.Anonymous) or str(n.this).upper() in _OBJECT_FUNCTIONS]
    for node in sorted(found, key=lambda n: -n.depth):
        if isinstance(node, exp.ObjectInsert):
            _object_insert(node)
        elif isinstance(node, exp.ObjectAgg):
            _object_agg(node)
        elif isinstance(node, exp.Anonymous):
            _object_function(node)


_EVENT_FUNCTIONS = ("CONDITIONAL_TRUE_EVENT", "CONDITIONAL_CHANGE_EVENT", "INTERPOLATE_FFILL", "INTERPOLATE_BFILL",
                    "INTERPOLATE_LINEAR")


def _event_refusal(name: str, why: str) -> _Untranslatable:
    page = "interpolate_bfill" if name.startswith("INTERPOLATE") else name.lower()
    return _Untranslatable(_refuse("SFX-018", name, why, f"{_DOCS}/functions/{page}"))


def _over(window: exp.Window, func: exp.Expression, frame: str, order: exp.Expression | None = None) -> exp.Window:
    """``func`` over ``window``'s partition and order (or ``order``), with ``frame`` (a ROWS frame)."""
    spec = cast(exp.Window, sqlglot.parse_one(f"f() OVER (ORDER BY 1 ROWS {frame})", read="duckdb")).args["spec"]
    over = exp.Window(this=func, partition_by=[p.copy() for p in window.args.get("partition_by") or []],
                      order=(order or window.args["order"]).copy(), spec=spec, over="OVER")
    return over


def _ignoring_nulls(func: type[exp.Func], value: exp.Expression) -> exp.Expression:
    return exp.IgnoreNulls(this=func(this=value))


def _linear(window: exp.Window, value: exp.Expression) -> exp.Expression:
    """INTERPOLATE_LINEAR: a NULL between two values is put on the line between them, by the distance of its
    ORDER BY value (a number, or a DATE or timestamp as seconds) from theirs."""
    order = window.args["order"].expressions
    if len(order) != 1:
        raise _event_refusal("INTERPOLATE_LINEAR", "INTERPOLATE_LINEAR needs exactly one ORDER BY expression.")
    from .sql import _with_args
    key = _with_args("CASE WHEN variant_typeof(CAST(fj_arg0 AS VARIANT)) = 'DATE' OR variant_typeof(CAST(fj_arg0 AS "
                     "VARIANT)) LIKE 'TIMESTAMP%' THEN epoch(CAST(CAST(fj_arg0 AS VARIANT) AS TIMESTAMP)) "
                     "ELSE CAST(CAST(fj_arg0 AS VARIANT) AS DOUBLE) END", [order[0].this])
    seen_key = _with_args("CASE WHEN fj_arg0 IS NOT NULL THEN fj_arg1 END", [value, key])
    before, after = "BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING", "BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING"
    parts = [value, exp.paren(key, copy=False),
             _over(window, _ignoring_nulls(exp.LastValue, value.copy()), before),
             _over(window, _ignoring_nulls(exp.LastValue, seen_key.copy()), before),
             _over(window, _ignoring_nulls(exp.FirstValue, value.copy()), after),
             _over(window, _ignoring_nulls(exp.FirstValue, seen_key.copy()), after)]
    return _with_args("CASE WHEN fj_arg0 IS NOT NULL THEN fj_arg0 WHEN fj_arg5 = fj_arg3 THEN fj_arg2 "
                      "ELSE fj_arg2 + (fj_arg4 - fj_arg2) * (fj_arg1 - fj_arg3) / (fj_arg5 - fj_arg3) END", parts)


def _event_select(window: exp.Window) -> exp.Select:
    """The SELECT a window function belongs to."""
    select = window.find_ancestor(exp.Select)
    if select is None:
        raise _event_refusal(str(cast(exp.Anonymous, window.this).this).upper(), "It is rewritten only in a SELECT.")
    return select


def _hoist(select: exp.Select, flags: list[tuple[exp.Window, exp.Expression]]) -> None:
    """Window functions can't nest in DuckDB, so each event's 0-or-1 flag (worked out with LAG or another
    window) and its row's place are worked out in a subquery over the SELECT's FROM and WHERE, and the
    count is a running sum over them outside it. A SELECT that joins, groups or aggregates isn't rewritten."""
    source = select.args.get("from_")
    name = str(cast(exp.Anonymous, flags[0][0].this).this).upper()
    shape = ("It is rewritten in a SELECT from one table or subquery without JOIN, GROUP BY, HAVING or an "
             "aggregate; put that SELECT in a subquery first.")
    if (source is None or select.args.get("joins") or select.args.get("group") or select.args.get("having")
            or select.args.get("laterals") or not isinstance(source.this, (exp.Table, exp.Subquery, exp.Values))
            or any(isinstance(a, exp.AggFunc) and a.find_ancestor(exp.Window, exp.Select) is select
                   for e in select.expressions for a in e.find_all(exp.AggFunc))):
        raise _event_refusal(name, shape)
    table = source.this
    alias = table.alias_or_name or "__sf_source"  # a subquery without a name can only be read by column name
    placed = exp.select(exp.Star()).from_(table.copy())
    where = select.args.get("where")
    if where is not None:
        placed.set("where", where.copy())
        select.set("where", None)
    inner = exp.select(exp.Star()).from_(exp.Subquery(this=placed, alias=exp.TableAlias(
        this=exp.to_identifier(alias))))
    added = []
    for i, (window, flag) in enumerate(flags):
        place, mark = f"__sf_place{i}", f"__sf_flag{i}"
        order = _over(window, exp.RowNumber(), "BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW")
        order.set("spec", None)
        placed.select(exp.alias_(order, place), copy=False)
        by_place = exp.Order(expressions=[exp.Ordered(this=exp.column(place))])
        same = _window_key(window)
        for lag in list(flag.find_all(exp.Window)):  # LAG over the event's window reads rows in place order,
            if _window_key(lag) == same:                # so tied ORDER BY values are taken in one order
                lag.set("order", by_place.copy())
        inner.select(exp.alias_(flag, mark), copy=False)
        added += [place, mark]
        running = _over(window, exp.Sum(this=exp.column(mark)), "BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW",
                        by_place)
        window.replace(exp.Cast(this=exp.Coalesce(this=running, expressions=[exp.Literal.number(0)]),
                                to=exp.DataType.build("BIGINT")))
    source.set("this", exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(alias))))
    hidden = [exp.column(n) for n in added]
    for star in [s for e in select.expressions for s in e.find_all(exp.Star)]:
        if star.find_ancestor(exp.Select) is select:
            star.set("except_", [*(star.args.get("except_") or []), *[h.copy() for h in hidden]])


def _window_key(window: exp.Window) -> tuple[str, str]:
    """A window's PARTITION BY and ORDER BY as text, to tell two windows over the same rows in the same order."""
    order = window.args.get("order")
    return (", ".join(p.sql() for p in window.args.get("partition_by") or []), order.sql() if order else "")


def _rewrite_events(statement: exp.Expression) -> None:
    """SFX-018: CONDITIONAL_TRUE_EVENT and CONDITIONAL_CHANGE_EVENT count events from the partition's
    first row (a NULL is neither true nor a change: CHANGE_EVENT(x) is TRUE_EVENT(x <> LAG(x))), and
    INTERPOLATE_FFILL, INTERPOLATE_BFILL and INTERPOLATE_LINEAR fill NULLs from the rows before and after,
    as running windows over the ORDER BY. A window frame isn't allowed, as in Snowflake."""
    windows = [w for w in statement.find_all(exp.Window)
               if isinstance(w.this, exp.Anonymous) and str(w.this.this).upper() in _EVENT_FUNCTIONS]
    hoisted: dict[int, tuple[exp.Select, list[tuple[exp.Window, exp.Expression]]]] = {}
    for window in sorted(windows, key=lambda w: -w.depth):
        func = cast(exp.Anonymous, window.this)
        name = str(func.this).upper()
        if len(func.expressions) != 1:
            raise _event_refusal(name, f"{name} takes one value.")
        if window.args.get("spec") is not None:
            raise _event_refusal(name, f"{name} takes no window frame, as in Snowflake.")
        if window.args.get("order") is None:
            raise _event_refusal(name, f"{name} needs an ORDER BY in its OVER clause.")
        value = exp.paren(func.expressions[0].copy(), copy=False)  # put into templates whole: o < 15 <> ...
        upto = "BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
        if name == "INTERPOLATE_FFILL":
            window.replace(_over(window, _ignoring_nulls(exp.LastValue, value.copy()), upto))
        elif name == "INTERPOLATE_BFILL":
            window.replace(_over(window, _ignoring_nulls(exp.FirstValue, value.copy()),
                                 "BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING"))
        elif name == "INTERPOLATE_LINEAR":
            window.replace(_linear(window, value))
        else:
            from .sql import _with_args
            if name == "CONDITIONAL_CHANGE_EVENT":
                previous = _over(window, exp.Lag(this=value.copy()), upto)
                previous.set("spec", None)
                flag = _with_args("CASE WHEN fj_arg0 <> fj_arg1 THEN 1 ELSE 0 END", [value, previous])
            else:
                flag = _with_args("CASE WHEN CAST(fj_arg0 AS BOOLEAN) THEN 1 ELSE 0 END", [value])
            if name == "CONDITIONAL_TRUE_EVENT" and not any(value.find_all(exp.Window)):
                running = _over(window, exp.Sum(this=flag), upto)
                window.replace(exp.Cast(this=exp.Coalesce(this=running, expressions=[exp.Literal.number(0)]),
                                        to=exp.DataType.build("BIGINT")))
                continue
            select = _event_select(window)
            hoisted.setdefault(id(select), (select, []))[1].append((window, flag))
    for select, flags in hoisted.values():
        _hoist(select, flags)


def _in_window(node: exp.Expression) -> exp.Expression:
    """The node a cast of aggregate ``node`` wraps: its window when it has one (x(...) OVER (...))."""
    return node.parent if isinstance(node.parent, exp.Window) and node.parent.this is node else node


def _rewrite_approximate(statement: exp.Expression) -> None:
    """SFX-014: HLL is APPROX_COUNT_DISTINCT; DATASKETCHES_HLL is the same estimate as a DOUBLE (its optional
    max_log_k is a precision setting with no DuckDB equivalent); APPROX_PERCENTILE returns a DOUBLE.
    DISTINCT inside HLL and DATASKETCHES_HLL has no effect in Snowflake and is dropped. SFD-010 applies."""
    for hll in list(statement.find_all(exp.Hll)):
        args = [hll.this, *hll.expressions]
        args = [a.expressions[0] if isinstance(a, exp.Distinct) and len(a.expressions) == 1 else a for a in args]
        if len(args) != 1 or isinstance(args[0], exp.Star) or isinstance(args[0], exp.Distinct):
            raise _Untranslatable(_refuse("SFX-014", "HLL of more than one value", "HLL(*) and HLL(a, b) "
                                          "aren't rewritten; HLL(x) is.", f"{_DOCS}/functions/hll"))
        hll.replace(exp.ApproxDistinct(this=args[0].copy()))
    for node in list(statement.find_all(exp.Anonymous)):
        if str(node.this).upper() != "DATASKETCHES_HLL":
            continue
        args = list(node.expressions)
        if args and isinstance(args[0], exp.Distinct):  # DISTINCT v, 12 reads as one DISTINCT of two
            args = [*args[0].expressions, *args[1:]]
        value = args[0] if args else None
        if value is None or len(args) > 2:
            raise _Untranslatable(_refuse("SFX-014", "DATASKETCHES_HLL", "DATASKETCHES_HLL takes a value and "
                                          "an optional max_log_k.", f"{_DOCS}/functions/datasketches_hll"))
        target = _in_window(node)
        estimate = exp.ApproxDistinct(this=value.copy())
        if target is node:
            node.replace(exp.Cast(this=estimate, to=exp.DataType.build("DOUBLE")))
        else:
            node.replace(estimate)
            target.replace(exp.Cast(this=target.copy(), to=exp.DataType.build("DOUBLE")))
    for quantile in list(statement.find_all(exp.ApproxQuantile)):
        target = _in_window(quantile)
        if isinstance(target.parent, exp.Cast) and target.parent.to.is_type(exp.DataType.Type.DOUBLE):
            continue
        target.replace(exp.Cast(this=target.copy(), to=exp.DataType.build("DOUBLE")))


def _rewrite_haversine(statement: exp.Expression) -> None:
    """SFX-008: HAVERSINE(lat1, lon1, lat2, lon2) is the great-circle distance in kilometres on a sphere of
    radius 6,371 km; that radius reproduces the docs example (3936.385096389 km, New York to Los Angeles)."""
    for node in list(statement.find_all(exp.Anonymous)):
        if str(node.this).upper() != "HAVERSINE":
            continue
        if len(node.expressions) != 4:
            raise _Untranslatable(_refuse("SFX-008", "HAVERSINE", "HAVERSINE takes four numbers.",
                                          f"{_DOCS}/functions/haversine"))
        lat1, lon1, lat2, lon2 = (f"radians(CAST({_duck(a)} AS DOUBLE))" for a in node.expressions)
        node.replace(sqlglot.parse_one(
            f"(2 * {_EARTH_RADIUS_KM} * asin(sqrt(power(sin(({lat2} - {lat1}) / 2), 2) + "
            f"cos({lat1}) * cos({lat2}) * power(sin(({lon2} - {lon1}) / 2), 2))))", read="duckdb"))


# SFX-011: the type a VARIANT holds, in Snowflake's words. A Snowflake VARIANT is DuckDB JSON (PARSE_JSON) or
# a DuckDB VARIANT (TO_VARIANT, a cast); variant_typeof names both once cast to VARIANT. A JSON number with a
# fraction or exponent is DOUBLE in DuckDB, where Snowflake has DECIMAL or DOUBLE: that is NUMBER (SFD-008).
_KIND_OF = "variant_typeof(CAST(__sf_v AS VARIANT))"
_KIND = (f"CASE WHEN __sf_v IS NULL THEN NULL "
         f"WHEN {_KIND_OF} LIKE 'INT%' OR {_KIND_OF} LIKE 'UINT%' OR {_KIND_OF} LIKE 'DECIMAL(%, 0)' THEN 'INTEGER' "
         f"WHEN {_KIND_OF} LIKE 'DECIMAL%' THEN 'DECIMAL' "
         f"WHEN {_KIND_OF} IN ('DOUBLE', 'FLOAT') THEN CASE WHEN typeof(__sf_v) = 'VARIANT' THEN 'DOUBLE' "
         f"ELSE 'NUMBER' END "
         f"WHEN {_KIND_OF} LIKE 'BOOL%' THEN 'BOOLEAN' "
         f"WHEN {_KIND_OF} = 'VARCHAR' THEN 'VARCHAR' "
         f"WHEN {_KIND_OF} LIKE 'ARRAY%' THEN 'ARRAY' "
         f"WHEN {_KIND_OF} LIKE 'OBJECT%' THEN 'OBJECT' "
         f"WHEN {_KIND_OF} = 'VARIANT_NULL' THEN 'NULL_VALUE' "
         f"WHEN {_KIND_OF} = 'DATE' THEN 'DATE' "
         f"WHEN {_KIND_OF} LIKE 'TIME\\_%' ESCAPE '\\' THEN 'TIME' "
         f"WHEN {_KIND_OF} LIKE 'TIMESTAMP%TZ' THEN 'TIMESTAMP_TZ' "
         f"WHEN {_KIND_OF} LIKE 'TIMESTAMP%' THEN 'TIMESTAMP_NTZ' "
         f"WHEN {_KIND_OF} = 'BLOB' THEN 'BINARY' ELSE {_KIND_OF} END")
_INTEGER, _NUMBERS = ("INTEGER",), ("INTEGER", "DECIMAL", "NUMBER", "DOUBLE")
_DECIMALS = ("INTEGER", "DECIMAL", "NUMBER")
# IS_<type>: the kinds that answer TRUE (Snowflake's IS_ pages).
_IS_KINDS = {"IS_INTEGER": _INTEGER, "IS_DECIMAL": _DECIMALS, "IS_DOUBLE": _NUMBERS, "IS_REAL": _NUMBERS,
             "IS_BOOLEAN": ("BOOLEAN",), "IS_CHAR": ("VARCHAR",), "IS_VARCHAR": ("VARCHAR",),
             "IS_ARRAY": ("ARRAY",), "IS_OBJECT": ("OBJECT",), "IS_BINARY": ("BINARY",), "IS_DATE": ("DATE",),
             "IS_DATE_VALUE": ("DATE",), "IS_TIME": ("TIME",), "IS_TIMESTAMP_NTZ": ("TIMESTAMP_NTZ",)}
# AS_<type>: the kinds it returns and the DuckDB type it returns them as (None: the VARIANT itself).
_AS_KINDS: dict[str, tuple[tuple[str, ...], str | None]] = {
    "AS_INTEGER": (_INTEGER, "DECIMAL(38, 0)"), "AS_DOUBLE": (_NUMBERS, "DOUBLE"), "AS_REAL": (_NUMBERS, "DOUBLE"),
    "AS_CHAR": (("VARCHAR",), "VARCHAR"), "AS_VARCHAR": (("VARCHAR",), "VARCHAR"),
    "AS_BOOLEAN": (("BOOLEAN",), "BOOLEAN"), "AS_ARRAY": (("ARRAY",), None), "AS_OBJECT": (("OBJECT",), None), "AS_BINARY": (("BINARY",), "BLOB"),
    "AS_DATE": (("DATE",), "DATE"), "AS_TIME": (("TIME",), "TIME"), "AS_TIMESTAMP_NTZ": (("TIMESTAMP_NTZ",), "TIMESTAMP"),
    "AS_DECIMAL": (_DECIMALS, "DECIMAL"), "AS_NUMBER": (_DECIMALS, "DECIMAL")}
_DECIMAL_FAMILY = frozenset({"IS_DECIMAL", "AS_DECIMAL", "AS_NUMBER"})


def _with_value(template: str, value: exp.Expression) -> exp.Expr:
    """``template`` (DuckDB SQL) applied to ``value``: __sf_v is a lambda's parameter, so the value is worked
    out once a row however often the template names it."""
    tree = sqlglot.parse_one(f"list_transform([__sf_arg], lambda __sf_v: {template})[1]", read="duckdb")
    for column in list(tree.find_all(exp.Column)):
        if column.name == "__sf_arg" and not column.table:
            column.replace(value.copy())
    return tree


def _decimal_type(name: str, args: list[exp.Expression]) -> str:
    """DECIMAL(p, s) for AS_DECIMAL(v [, p [, s]]); Snowflake's defaults are 38 and 0."""
    sizes = []
    for a in args:
        if not (isinstance(a, exp.Literal) and not a.is_string and a.name.isdigit()):
            raise _Untranslatable(_refuse("SFX-011", f"{name} with a computed precision or scale",
                                          "Precision and scale are rewritten only when they are numbers.",
                                          f"{_DOCS}/functions/as_decimal-number"))
        sizes.append(int(a.name))
    precision, scale = (sizes + [38, 0][len(sizes):])[:2]
    if not 1 <= precision <= 38 or not 0 <= scale <= precision:
        raise _Untranslatable(_refuse("SFX-011", f"{name} precision and scale",
                                      "Precision is 1 to 38 and scale 0 to the precision.",
                                      f"{_DOCS}/functions/as_decimal-number"))
    return f"DECIMAL({precision}, {scale})"


def _rewrite_variant_types(statement: exp.Expression) -> None:
    """SFX-011: IS_<type> and AS_<type> test and read a VARIANT's type as Snowflake names it (_KIND)."""
    # Innermost first: a call inside another's argument is rewritten before that argument is copied.
    for node in reversed(list(statement.find_all(exp.Anonymous))):
        name = str(node.this).upper()
        if name not in _IS_KINDS and name not in _AS_KINDS:
            continue
        args = list(node.expressions)
        if not args or (name not in ("AS_DECIMAL", "AS_NUMBER") and len(args) != 1) or len(args) > 3:
            raise _Untranslatable(_refuse("SFX-011", name, f"{name} takes one VARIANT"
                                          + (" and an optional precision and scale." if name.startswith("AS_D")
                                             or name == "AS_NUMBER" else "."), f"{_DOCS}/functions/is"))
        value = args[0]
        if name in _IS_KINDS:
            kinds = ", ".join(f"'{k}'" for k in _IS_KINDS[name])
            node.replace(_with_value(f"(({_KIND}) IN ({kinds}))", value))
            continue
        kinds_t, to = _AS_KINDS[name]
        if to == "DECIMAL":
            to = _decimal_type(name, args[1:])
        kinds = ", ".join(f"'{k}'" for k in kinds_t)
        read = "__sf_v" if to is None else f"CAST(CAST(__sf_v AS VARIANT) AS {to})"
        node.replace(_with_value(f"(CASE WHEN ({_KIND}) IN ({kinds}) THEN {read} END)", value))
_TEXT_TYPES = {exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR, exp.DataType.Type.CHAR,
               exp.DataType.Type.NVARCHAR, exp.DataType.Type.NCHAR}


def _is_json(node: exp.Expression) -> bool:
    return node.type is not None and node.type.this == exp.DataType.Type.JSON


def _json_valued(node: exp.Expression) -> bool:
    """Is ``node`` DuckDB JSON whatever the data: a path (v:a, which becomes ->), PARSE_JSON, a column or
    bracket that _type_values found JSON, or [...] on one of those, maybe in parentheses?"""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, (exp.JSONExtract, exp.ParseJSON)) or _is_json(node):
        return True
    return isinstance(node, exp.Bracket) and _json_valued(node.this)


def _json_text(value: exp.Expression, to: exp.DataType) -> exp.Expression:
    """CAST(value AS to) for a JSON ``value``: a JSON string unquoted, JSON null as SQL NULL (Snowflake's
    "To convert a JSON null value to a SQL NULL value, cast it as a string"), anything else as before."""
    def kind_is(name: str) -> exp.Expression:
        return exp.EQ(this=exp.Anonymous(this="json_type", expressions=[value.copy()]),
                      expression=exp.Literal.string(name))
    text = exp.JSONExtractScalar(this=value.copy(), expression=exp.Literal.string("$"))
    case = exp.Case().when(kind_is("VARCHAR"), text).when(kind_is("NULL"), exp.Null())
    return exp.Cast(this=case.else_(exp.Cast(this=value.copy(), to=to.copy())), to=to.copy())


def _rewrite_json_text(statement: exp.Expression) -> None:
    """SFX-012: v:a::string is the string itself in Snowflake ('Pine'), where DuckDB's cast of JSON to text
    keeps the JSON quotes ('"Pine"'), and JSON null cast to text is SQL NULL. Numbers, objects and arrays
    are cast as before."""
    for node in list(statement.find_all(exp.Cast)):
        if node.to.this in _TEXT_TYPES and _json_valued(node.this):
            node.replace(_json_text(node.this, node.to))
    for to_char in list(statement.find_all(exp.ToChar)):
        if to_char.args.get("format") is None and _json_valued(to_char.this):
            to_char.replace(_json_text(to_char.this, exp.DataType.build("VARCHAR")))


def _type_values(statement: exp.Expression, written: set[int] | None = None) -> bool:
    """SFX-012 and SFX-013 need to know which values are JSON. sqlglot's type inference, run on a qualified
    copy so the SQL itself isn't changed, tells a column read from PARSE_JSON (in a subquery or WITH) or
    an integer column; the types found are copied onto the same nodes here.

    SFX-013: [i] is 0-based in Snowflake, on an ARRAY and on JSON alike. DuckDB's JSON is 0-based too but
    its lists are 1-based, and sqlglot adds 1 only when it can tell the value is a list and the index an
    integer (a column it can't type is taken for a list). So v[0] stays [0] on JSON, and l[i] becomes
    l[i + 1] on a list.

    True when a [...] is on a value whose type isn't known (a table's column): SFD-009. A string key
    raises it only under a cast to text, since v['a'] alone is right on JSON and on a MAP. Only the
    [...] the user wrote count (``written``, their ids), not ones an earlier rewrite made."""
    kinds = (exp.Bracket, exp.Cast, exp.ToChar)
    mine = [n for n in statement.find_all(*kinds)]
    if not mine:
        return False
    from sqlglot.optimizer.annotate_types import annotate_types
    from sqlglot.optimizer.qualify import qualify
    try:
        typed = annotate_types(qualify(statement.copy(), dialect="snowflake", validate_qualify_columns=False,
                                       identify=False), dialect="snowflake")
    except SqlglotError:
        return any(isinstance(n, exp.Bracket) and _written(n, written) for n in mine)
    theirs = [n for n in typed.find_all(*kinds)]
    if [type(n) for n in theirs] != [type(n) for n in mine]:
        return any(isinstance(n, exp.Bracket) and _written(n, written) for n in mine)
    unknown = set()
    wanted = (exp.DataType.Type.JSON, exp.DataType.Type.ARRAY)
    for m, t in zip(mine, theirs, strict=True):
        if m.this.type is None and t.this.type is not None and t.this.type.this in wanted:
            m.this.type = t.this.type.copy()
        if isinstance(m, exp.Bracket) and (t.this.type is None or t.this.type.this == exp.DataType.Type.UNKNOWN):
            unknown.add(id(m))
        if isinstance(m, exp.Bracket) and len(m.expressions) == len(t.expressions) == 1 \
                and m.expressions[0].type is None and t.expressions[0].type is not None \
                and t.expressions[0].type.this in exp.DataType.INTEGER_TYPES:
            m.expressions[0].type = t.expressions[0].type.copy()
    for m in reversed(mine):  # v['arr'][0]: [...] on JSON is JSON too, inner first
        if isinstance(m, exp.Bracket) and m.this.type is None and _json_valued(m.this):
            m.this.type = exp.DataType.build("JSON")
    return any(id(m) in unknown and not _json_valued(m.this) and _bracket_may_differ(m)
               for m in mine if isinstance(m, exp.Bracket) and _written(m, written))


def _written(node: exp.Expression, written: set[int] | None) -> bool:
    return written is None or id(node) in written


def _bracket_may_differ(node: exp.Bracket) -> bool:
    """A [...] on a value whose type isn't known: a key that isn't a string (the 0- or 1-based question),
    or any key under a cast to text (the JSON quotes)."""
    if not (len(node.expressions) == 1 and node.expressions[0].is_string):
        return True
    parent = node.parent
    while isinstance(parent, exp.Paren):
        parent = parent.parent
    return (isinstance(parent, exp.Cast) and parent.to.this in _TEXT_TYPES) or \
        (isinstance(parent, exp.ToChar) and parent.args.get("format") is None)


# DuckDB's typeof -> Snowflake's TYPEOF, where the name is the same whatever the value.
_TYPEOF_NAMES = {**{t: "INTEGER" for t in ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT",
                                           "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT")},
                 "FLOAT": "DOUBLE", "DOUBLE": "DOUBLE", "VARCHAR": "VARCHAR", "BOOLEAN": "BOOLEAN", "DATE": "DATE",
                 "TIME": "TIME", "BLOB": "BINARY",
                 **{t: "TIMESTAMP_NTZ" for t in ("TIMESTAMP", "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS")}}
_JSON_NAMES = {"NULL": "NULL_VALUE", "BOOLEAN": "BOOLEAN", "VARCHAR": "VARCHAR", "ARRAY": "ARRAY",
               "OBJECT": "OBJECT", "BIGINT": "INTEGER", "UBIGINT": "INTEGER"}
_TYPEOF_AMBIGUOUS = "DECIMAL or DOUBLE"
# Types a cast gives whose name is one of _TYPEOF_NAMES (or DECIMAL), so TYPEOF of the cast is exact.
_TYPEOF_KNOWN_CASTS = {exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR, exp.DataType.Type.CHAR,
                       exp.DataType.Type.NCHAR, exp.DataType.Type.NVARCHAR, exp.DataType.Type.BOOLEAN,
                       exp.DataType.Type.DATE, exp.DataType.Type.TIME, exp.DataType.Type.TIMESTAMP,
                       exp.DataType.Type.TIMESTAMPNTZ, exp.DataType.Type.DATETIME, exp.DataType.Type.DOUBLE,
                       exp.DataType.Type.FLOAT, exp.DataType.Type.TINYINT, exp.DataType.Type.SMALLINT,
                       exp.DataType.Type.INT, exp.DataType.Type.BIGINT, exp.DataType.Type.DECIMAL,
                       exp.DataType.Type.BINARY, exp.DataType.Type.VARBINARY}


def _typeof_value(node: exp.Typeof) -> exp.Expression:
    """TYPEOF's argument without casts to VARIANT: Snowflake names the value inside, and DuckDB's VARIANT
    (1.5 and later) would hide it."""
    value = node.this
    while isinstance(value, exp.ToVariant) or (isinstance(value, (exp.Cast, exp.TryCast))
                                               and value.to.is_type(exp.DataType.Type.VARIANT)):
        value = value.this
    return value


class _JsonNumber(str):
    """A JSON number as written in the text, so its TYPEOF goes by how it was written."""


_NOT_KNOWN = object()


def _json_type_name(value: Any) -> str:
    """SFX-009's name for a value json.loads read with numbers kept as _JsonNumber."""
    if value is None:
        return "NULL_VALUE"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, _JsonNumber):
        if "e" in value.lower():
            return "DOUBLE"
        whole, _, frac = value.lstrip("-").partition(".")
        if len(whole.lstrip("0")) + len(frac) > 38 or len(frac) > 37:
            return "DOUBLE"
        return "DECIMAL" if "." in value else "INTEGER"
    if isinstance(value, str):
        return "VARCHAR"
    return "ARRAY" if isinstance(value, list) else "OBJECT"


def _not_json(text: str) -> Any:
    raise ValueError(f"{text} isn't JSON")  # json.loads takes NaN and Infinity; Snowflake's PARSE_JSON doesn't


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({k for k, _ in pairs}) != len(pairs):
        raise ValueError("duplicate key")
    return dict(pairs)


def _typeof_of_literal_path(value: exp.Expression) -> Any:
    """TYPEOF of a path into PARSE_JSON of text written in the query (PARSE_JSON('{"a": 1.5e2}'):a), worked
    out here from the text as written: None for SQL NULL (no such key or element), or _NOT_KNOWN."""
    if not (isinstance(value, exp.JSONExtract) and isinstance(value.this, exp.ParseJSON)
            and isinstance(value.this.this, exp.Literal) and value.this.this.is_string
            and isinstance(value.expression, exp.JSONPath)):
        return _NOT_KNOWN
    try:
        doc = json.loads(value.this.this.name, parse_float=_JsonNumber, parse_int=_JsonNumber,
                         parse_constant=_not_json, object_pairs_hook=_no_duplicate_keys)
    except ValueError:  # silent-ok: Snowflake's own error comes from the query, as without this rule
        return _NOT_KNOWN
    for part in value.expression.expressions:
        if isinstance(part, exp.JSONPathRoot):
            continue
        if isinstance(part, exp.JSONPathKey) and isinstance(part.this, str):
            found = isinstance(doc, dict) and part.this in doc
            key: Any = part.this
        elif isinstance(part, exp.JSONPathSubscript) and isinstance(part.this, int):
            found = isinstance(doc, list) and 0 <= part.this < len(doc)
            key = part.this
        else:
            return _NOT_KNOWN
        if not found:
            return None  # no such key or element: SQL NULL, as in Snowflake
        doc = doc[key]
    return _json_type_name(doc)


def _typeof_known(node: exp.Typeof) -> bool:
    """True when SFX-009 names the argument's type exactly whatever its value: PARSE_JSON in the same
    call or a path into PARSE_JSON of text in the query, a literal, or a cast to a type Snowflake names
    one way."""
    value = _typeof_value(node)
    if _typeof_of_literal_path(value) is not _NOT_KNOWN:
        return True
    if isinstance(value, (exp.ParseJSON, exp.Literal, exp.Boolean, exp.Null)):
        return True
    return isinstance(value, (exp.Cast, exp.TryCast)) and value.to.this in _TYPEOF_KNOWN_CASTS


def _quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _rewrite_typeof(statement: exp.Expression) -> None:
    """SFX-009: TYPEOF gives Snowflake's names. A JSON value goes by json_type; a JSON number by how it was
    written, as PARSE_JSON reads it: with an exponent it's DOUBLE; otherwise INTEGER or DECIMAL while it
    fits NUMBER (38 digits, 37 after the point) and DOUBLE when it doesn't. DuckDB writes a parsed double
    again its own way (1.912e2 becomes 191.2), so only PARSE_JSON's own input text tells; any other
    JSON number with a fraction is 'DECIMAL or DOUBLE' (SFD-007). Another type goes by DuckDB's typeof;
    one with no single Snowflake name keeps DuckDB's name."""
    for node in list(statement.find_all(exp.Typeof)):
        value = _typeof_value(node)
        known = _typeof_of_literal_path(value)
        if known is not _NOT_KNOWN:
            node.replace(exp.Literal.string(known) if known else exp.cast(exp.Null(), "VARCHAR"))
            continue
        if isinstance(value, exp.ParseJSON):
            # Snowflake's rule on the text as written: DuckDB's JSON() writes doubles again.
            text = "trim(CAST(_kf_t AS VARCHAR))"
            number = (f"CASE WHEN regexp_matches({text}, '[eE]') THEN 'DOUBLE' "
                      f"WHEN length(ltrim(regexp_extract({text}, '^-?([0-9]+)', 1), '0')) "
                      f"+ length(regexp_extract({text}, '\\.([0-9]+)', 1)) > 38 "
                      f"OR length(regexp_extract({text}, '\\.([0-9]+)', 1)) > 37 THEN 'DOUBLE' "
                      f"WHEN contains({text}, '.') THEN 'DECIMAL' ELSE 'INTEGER' END")
        else:
            # Only a whole number keeps its text (DuckDB keeps big integers as written).
            text = "trim(CAST(_kf_v AS VARCHAR))"
            number = (f"CASE WHEN NOT regexp_full_match({text}, '-?[0-9]+') THEN {_quoted(_TYPEOF_AMBIGUOUS)} "
                      f"WHEN length(ltrim(ltrim({text}, '-'), '0')) > 38 THEN 'DOUBLE' ELSE 'INTEGER' END")
        json_case = " ".join(f"WHEN {_quoted(k)} THEN {_quoted(v)}" for k, v in _JSON_NAMES.items())
        type_case = " ".join(f"WHEN {_quoted(k)} THEN {_quoted(v)}" for k, v in _TYPEOF_NAMES.items())
        template = (f"SELECT CASE WHEN _kf_v IS NULL THEN NULL "
                    f"WHEN typeof(_kf_v) = 'JSON' THEN CASE json_type(TRY_CAST(_kf_v AS JSON)) {json_case} "
                    f"ELSE {number} END "
                    f"WHEN regexp_full_match(typeof(_kf_v), 'DECIMAL\\([0-9]+,0\\)') THEN 'INTEGER' "
                    f"WHEN typeof(_kf_v) LIKE 'DECIMAL(%' THEN 'DECIMAL' "
                    f"WHEN typeof(_kf_v) LIKE '%[]' THEN 'ARRAY' "
                    f"ELSE CASE typeof(_kf_v) {type_case} ELSE typeof(_kf_v) END END")
        case = sqlglot.parse_one(template, read="duckdb").expressions[0]
        # The value goes in as a tree, not as text read again (#1252), as in SFX-001.
        for col in list(case.find_all(exp.Column)):
            if col.name == "_kf_v":
                col.replace(value.copy())
            elif col.name == "_kf_t":
                col.replace(value.this.copy())
        node.replace(case)


def _div0_branch(node: exp.Div) -> bool:
    """True for the division inside DIV0 and DIV0NULL as sqlglot reads them (IF(b = 0 ..., 0, a / b)),
    which never runs with a zero divisor."""
    parent, b = node.parent, node.expression
    if not (isinstance(parent, exp.If) and parent.args.get("false") is node):
        return False
    cond = parent.this

    def zero(e: exp.Expression) -> bool:
        return isinstance(e, exp.EQ) and e.this == b and e.expression == exp.Literal.number(0)

    if zero(cond):
        return True
    if isinstance(cond, exp.And):  # DIV0: b = 0 AND NOT a IS NULL
        return zero(cond.this) and isinstance(cond.expression, exp.Not) and cond.expression.this == exp.Is(
            this=node.this, expression=exp.Null())
    if isinstance(cond, exp.Or):  # DIV0NULL: b = 0 OR b IS NULL
        return zero(cond.this) and cond.expression == exp.Is(this=b, expression=exp.Null())
    return False


def _nonzero_number(e: exp.Expression) -> bool:
    if not (isinstance(e, exp.Literal) and not e.is_string):
        return False
    try:
        return float(e.name) != 0
    except ValueError:  # silent-ok: not a plain number, so the divisor gets the check
        return False


def _rewrite_division(statement: exp.Expression) -> None:
    """SFX-010: a / b stops with Snowflake's "Division by zero" error when b is 0, where DuckDB gives
    infinity or NULL. A divisor written as a non-zero number is left as it is."""
    for node in list(statement.find_all(exp.Div)):
        divisor = node.expression
        if _div0_branch(node) or _nonzero_number(divisor):
            continue
        check = exp.Case(ifs=[exp.If(this=exp.EQ(this=divisor.copy(), expression=exp.Literal.number(0)),
                                     true=exp.Anonymous(this="ERROR",
                                                        expressions=[exp.Literal.string("Division by zero")]))],
                         default=divisor.copy())
        divisor.replace(check)


# ---------------------------------------------------------------- known differences (SFD-*)

_TIMESTAMP_TYPES = {exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPNTZ, exp.DataType.Type.TIMESTAMPLTZ,
                    exp.DataType.Type.TIMESTAMPTZ, exp.DataType.Type.DATETIME}


_TIMESTAMP_FUNCTIONS = {f"{try_}TO_TIMESTAMP{kind}" for try_ in ("", "TRY_") for kind in ("", "_NTZ", "_LTZ", "_TZ")}


def _may_be_date(value: exp.Expression | None) -> bool:
    """False only when the value is plainly a timestamp: a cast to one (TO_TIMESTAMP of a literal reads
    as a cast), a TO_TIMESTAMP function (and its _NTZ, _LTZ, _TZ and TRY_ forms), CURRENT_TIMESTAMP or
    CONVERT_TIMEZONE. A column, a literal or anything else may be a DATE."""
    if isinstance(value, (exp.Cast, exp.TryCast)):
        return value.to.this not in _TIMESTAMP_TYPES
    if isinstance(value, exp.Anonymous) and str(value.this).upper() in _TIMESTAMP_FUNCTIONS:
        return False
    return not isinstance(value, (exp.CurrentTimestamp, exp.ConvertTimezone))


_DATE_UNITS = {"YEAR", "YEARS", "Y", "YY", "YYY", "YYYY", "YR", "YRS", "QUARTER", "QUARTERS", "Q", "QTR", "QTRS",
               "MONTH", "MONTHS", "MM", "MON", "MONS", "WEEK", "WEEKS", "W", "WK", "WEEKOFYEAR", "WOY", "WY",
               "DAY", "DAYS", "D", "DD", "DAYOFMONTH"}


def _differences(statement: exp.Expression) -> list[Note]:
    notes = []
    if isinstance(statement, (exp.Create, exp.Alter)):
        target = statement.this
        if any(not i.args.get("quoted") for i in target.find_all(exp.Identifier)):
            notes.append(SFD_CASE)
    types = {d.this for d in statement.find_all(exp.DataType)}
    if exp.DataType.Type.TIMESTAMPTZ in types:
        notes.append(SFD_TZ)
    if any(not any(isinstance(side, exp.Literal) and not side.is_string and "." in side.name
                   for side in (d.this, d.expression)) for d in statement.find_all(exp.Div)):
        notes.append(SFD_DIV)
    if exp.DataType.Type.TIMESTAMPLTZ in types or any(statement.find_all(exp.CurrentTimestamp)):
        notes.append(SFD_LTZ)
    if any(str(getattr(n.args.get("unit"), "name", "") or "").upper() in _DATE_UNITS and _may_be_date(n.this)
           and not _date_trunc_of_a_date(n)
           for n in statement.find_all(exp.TimestampTrunc, exp.DateTrunc, exp.DateAdd)):
        notes.append(SFD_DATE)
    if any(not _typeof_known(n) for n in statement.find_all(exp.Typeof)):
        notes.append(SFD_TYPEOF)
    if any(statement.find_all(exp.ApproxDistinct, exp.Hll, exp.ApproxQuantile)) or any(
            str(n.this).upper() == "DATASKETCHES_HLL" for n in statement.find_all(exp.Anonymous)):
        notes.append(SFD_APPROX)
    if any(str(n.this).upper() == "INTERPOLATE_LINEAR" for n in statement.find_all(exp.Anonymous)):
        notes.append(SFD_LINEAR)
    if any(str(n.this).upper() in _DECIMAL_FAMILY for n in statement.find_all(exp.Anonymous)):
        notes.append(SFD_NUMBER)
    return notes


# ---------------------------------------------------------------- the rule list for docs/engines.md

# Every rule and note id the code above uses, with what it covers. docs/engines.md holds docs_table()
# word for word (a test checks), so the page can't drift from the code.
CATALOG = (
    ("SFX-001", "Rewritten", "LATERAL FLATTEN and TABLE(FLATTEN(...)) become DuckDB's json_each, with Snowflake's "
                "columns: KEY is NULL for an array element and INDEX for an object field, PATH is written as "
                "Snowflake writes it (b, b[0], c.d), THIS is the value flattened, OUTER gives one row of NULLs for a value "
                "with nothing to flatten, and PATH => "
                "takes plain names and [n]. A key that isn't a plain name is written in PATH as it is, since "
                "Snowflake's docs don't show how it writes one. SEQ, RECURSIVE and MODE aren't rewritten; "
                "SFD-005 is retired."),
    ("SFX-002", "Rewritten", "TO_VARCHAR and TO_CHAR with a date or time format written in the query become "
                "strftime. Elements with no equivalent (quarter, week) aren't rewritten."),
    ("SFX-003", "Runs only in Snowflake", "TO_VARCHAR and TO_CHAR with a number format."),
    ("SFX-004", "Rewritten", "OBJECT_CONSTRUCT drops a pair whose value is SQL NULL and keeps JSON null, as "
                "Snowflake does. OBJECT_CONSTRUCT(*) isn't rewritten."),
    ("SFX-005", "Rewritten", "A VALUES list in FROM gets Snowflake's column names COLUMN1, COLUMN2..., unless "
                "its alias names the columns."),
    ("SFX-006", "Rewritten", "SOUNDEX becomes American Soundex in DuckDB SQL (the first letter and three digits). "
                "Text that starts with a non-letter comes back as it is, as in Spark; Snowflake doesn't document that "
                "case."),
    ("SFX-007", "Rewritten", "RATIO_TO_REPORT(x) OVER (PARTITION BY ...) becomes x divided by the partition's "
                "sum, null when x is null or the sum is 0. An ORDER BY in its window is dropped, as it doesn't "
                "change the result. x is worked out twice, which matters only when it isn't deterministic "
                "(RANDOM(), for example)."),
    ("SFX-008", "Rewritten", "HAVERSINE becomes the great-circle distance in kilometres on a sphere of radius "
                "6,371 km."),
    ("SFX-009", "Rewritten", "TYPEOF gives Snowflake's type names: NULL_VALUE, BOOLEAN, VARCHAR, ARRAY and OBJECT "
                "for JSON; INTEGER, DECIMAL or DOUBLE for a JSON number by how PARSE_JSON reads it (an exponent "
                "makes it DOUBLE; otherwise INTEGER or DECIMAL while it fits NUMBER); INTEGER, DECIMAL, DOUBLE, "
                "VARCHAR, BOOLEAN, DATE, TIME, TIMESTAMP_NTZ, BINARY and ARRAY for other values; SQL NULL for "
                "SQL NULL. A whole number is always INTEGER here, where Snowflake notes it may say DECIMAL. "
                "Where the name can't be known, SFD-007 applies. The argument is worked out more than once, "
                "which matters only when it isn't deterministic."),
    ("SFX-010", "Rewritten", "a / b stops with Snowflake's \"Division by zero\" error when b is 0, where DuckDB gives "
                "infinity or NULL. DIV0 and DIV0NULL still give 0. A divisor that isn't a column or a number is "
                "worked out twice, which matters only when it isn't deterministic."),
    ("SFX-011", "Rewritten", "IS_<type> and AS_<type> (INTEGER, DECIMAL, NUMBER, DOUBLE, REAL, BOOLEAN, CHAR, "
                "VARCHAR, ARRAY, OBJECT, BINARY, DATE, DATE_VALUE, TIME, TIMESTAMP_NTZ) test and read the type a "
                "VARIANT holds, as Snowflake names it: a PARSE_JSON value by its JSON type, a TO_VARIANT value by "
                "the type it was made from. AS_<type> gives NULL for any other type. A JSON integer too big for 64 "
                "bits is DOUBLE here."),
    ("SFX-012", "Rewritten", "A JSON value cast to text (v:a::string, v['a']::string, TO_VARCHAR(v:a), "
                "PARSE_JSON(...)::varchar) gives a string without its JSON quotes, and JSON null gives SQL NULL, as "
                "in Snowflake: DuckDB's cast keeps the quotes and writes 'null'. A table's column whose type the "
                "statement doesn't show is cast as written."),
    ("SFX-013", "Rewritten", "[i] on a value read from PARSE_JSON stays 0-based, and [i] with an integer column on "
                "an ARRAY becomes 1-based, as DuckDB needs. Where the type can't be told from the statement (a "
                "table's column), [i] is taken for an ARRAY."),
    ("SFX-014", "Rewritten", "HLL becomes APPROX_COUNT_DISTINCT, DATASKETCHES_HLL the same estimate as a DOUBLE (its "
                "max_log_k is dropped), and APPROX_PERCENTILE returns a DOUBLE, as in Snowflake. HLL(*) and HLL of "
                "more than one value aren't rewritten. BITMAP_BUCKET_NUMBER and BITMAP_BIT_POSITION run as "
                "arithmetic on 32,768-bit buckets."),
    ("SFX-015", "Rewritten", "OCTET_LENGTH counts bytes (a column is taken for text; a BINARY column stops with "
                "DuckDB's error). MD5_NUMBER_LOWER64 and MD5_NUMBER_UPPER64 read MD5's last and first 64 bits. "
                "WIDTH_BUCKET gives 0 below min_value, num_buckets + 1 at or above max_value, and NULL when any "
                "argument is NULL; min_value at or above max_value stops with an error. TRY_BASE64_DECODE_STRING, TRY_BASE64_DECODE_BINARY, TRY_HEX_DECODE_STRING and "
                "TRY_HEX_DECODE_BINARY give NULL where the text doesn't decode; an alphabet argument isn't "
                "rewritten. SOUNDEX_P123 keeps the second letter's number (Pfister is P123). Arguments are "
                "worked out more than once, which matters only when they aren't deterministic."),
    ("SFX-016", "Rewritten", "OBJECT_DELETE, OBJECT_PICK, OBJECT_INSERT, OBJECT_AGG, TO_OBJECT and ARRAYS_TO_OBJECT run "
                "on DuckDB JSON with Snowflake's NULL rules: a SQL NULL key or value is left out and JSON null is "
                "kept; an existing key in OBJECT_INSERT without updateFlag, a repeated key in OBJECT_AGG and "
                "arrays of unequal length stop with an error (OBJECT_AGG's text is DuckDB's \"Map keys must be unique\"). "
                "OBJECT_INSERT of a SQL NULL value leaves the object as it is, an existing key too, as the docs "
                "don't say otherwise; OBJECT_AGG of no rows is NULL. OBJECT_PICK with one argument takes it for an "
                "array only when it is written as one ([...], ARRAY_CONSTRUCT or PARSE_JSON); a column is a key. "
                "OBJECT_AGG(DISTINCT ...) isn't rewritten. Objects "
                "keep their keys in order, as Snowflake does, and an object constant ({'a': 1}) reads its keys "
                "as text. Arguments are worked out more than once, which matters only when they aren't "
                "deterministic."),
    ("SFX-017", "Rewritten", "TIME(x) is a cast to TIME, as TO_TIME(x) is: the time of a string or a timestamp. "
                "CURRENT_TIMESTAMP and a cast to TIMESTAMP_TZ or TIMESTAMP_LTZ go through TIMESTAMP, the "
                "local time; a TIMESTAMP_TZ column stops with DuckDB's cast error, since its type isn't known "
                "here."),
    ("SFX-018", "Rewritten", "CONDITIONAL_TRUE_EVENT and CONDITIONAL_CHANGE_EVENT count events from each "
                "partition's first row in ORDER BY order (a NULL is neither true nor a change), and "
                "INTERPOLATE_FFILL, INTERPOLATE_BFILL and INTERPOLATE_LINEAR fill a NULL from the rows before "
                "and after it. CONDITIONAL_CHANGE_EVENT, and CONDITIONAL_TRUE_EVENT of a LAG or LEAD, are worked "
                "out in a subquery over the SELECT's FROM and WHERE, so they are rewritten only in a SELECT from "
                "one table or subquery without JOIN, GROUP BY, HAVING or an aggregate. A window frame isn't "
                "allowed, as in Snowflake, and INTERPOLATE_LINEAR needs one ORDER BY expression: a number, DATE "
                "or timestamp."),
    ("SFX-000", "Runs only in Snowflake", "Anything else sqlglot can't write for DuckDB."),
    ("SFD-001", "Runs, with a difference: " + SFD_CASE.title, SFD_CASE.reason),
    ("SFD-002", "Runs, with a difference: " + SFD_TZ.title, SFD_TZ.reason),
    ("SFD-003", "Runs, with a difference: " + SFD_DIV.title, SFD_DIV.reason),
    ("SFD-004", "Runs, with a difference: " + SFD_LTZ.title, SFD_LTZ.reason),
    ("SFD-006", "Runs, with a difference: " + SFD_DATE.title, SFD_DATE.reason),
    ("SFD-007", "Runs, with a difference: " + SFD_TYPEOF.title, SFD_TYPEOF.reason),
    ("SFD-008", "Runs, with a difference: " + SFD_NUMBER.title, SFD_NUMBER.reason),
    ("SFD-009", "Runs, with a difference: " + SFD_BRACKET.title, SFD_BRACKET.reason),
    ("SFD-010", "Runs, with a difference: " + SFD_APPROX.title, SFD_APPROX.reason),
    ("SFD-011", "Runs, with a difference: " + SFD_LINEAR.title, SFD_LINEAR.reason),
    ("SFN-001", "Runs only in Snowflake", "Streams."),
    ("SFN-002", "Runs only in Snowflake", "Tasks."),
    ("SFN-003", "Runs only in Snowflake", "Dynamic tables (their SELECT can be tried on its own)."),
    ("SFN-004", "Runs only in Snowflake", "Snowflake Scripting, procedures, user-defined functions and CALL."),
    ("SFN-005", "Runs only in Snowflake", "EXECUTE IMMEDIATE."),
    ("SFN-006", "Runs only in Snowflake", "Time travel: AT and BEFORE."),
    ("SFN-007", "Runs only in Snowflake", "UNDROP and zero-copy CLONE."),
    ("SFN-008", "Runs only in Snowflake", "Stages: @stage references, COPY INTO, PUT, GET, LIST, REMOVE."),
    ("SFN-009", "Runs only in Snowflake", "USE, SHOW and DESCRIBE."),
    ("SFN-010", "Runs only in Snowflake", "GRANT, REVOKE, and ALTER of Snowflake settings (policies, tags, "
                "suspend and resume)."),
    ("SFN-011", "Runs only in Snowflake", "Snowflake-only objects: stages, pipes, warehouses, roles, users, shares, "
                "integrations, file formats, sequences, policies, tags."),
    ("SFN-012", "Runs only in Snowflake", "Cortex AI functions."),
    ("SFN-013", "Runs only in Snowflake", "TIMESTAMP_TZ text with an offset after a space; DuckDB reads it as a "
                "time zone name."),
    ("SFN-014", "Runs only in Snowflake", "Context functions about the Snowflake account, session or client: "
                "CURRENT_ACCOUNT, CURRENT_ACCOUNT_NAME, CURRENT_AVAILABLE_ROLES, CURRENT_CLIENT, CURRENT_IP_ADDRESS, "
                "CURRENT_ORGANIZATION_NAME, CURRENT_REGION, CURRENT_ROLE_TYPE, CURRENT_SECONDARY_ROLES, "
                "CURRENT_SESSION, CURRENT_STATEMENT, CURRENT_TRANSACTION, CURRENT_WAREHOUSE and LAST_QUERY_ID."),
    ("SFN-015", "Runs only in Snowflake", "IS_TIMESTAMP_LTZ, IS_TIMESTAMP_TZ, AS_TIMESTAMP_LTZ and AS_TIMESTAMP_TZ: "
                "DuckDB keeps one kind of timestamp with a time zone."),
    ("SFN-016", "Runs only in Snowflake", "Sketch and bitmap states in Snowflake's own format: HLL_ACCUMULATE, "
                "HLL_COMBINE, HLL_ESTIMATE, HLL_EXPORT, HLL_IMPORT, the DATASKETCHES_HLL_ACCUMULATE, _COMBINE and "
                "_ESTIMATE functions, APPROX_TOP_K and its _ACCUMULATE, _COMBINE and _ESTIMATE, the "
                "APPROX_PERCENTILE_ACCUMULATE, _COMBINE and _ESTIMATE functions, MINHASH, MINHASH_COMBINE, "
                "APPROXIMATE_SIMILARITY, APPROXIMATE_JACCARD_INDEX, BITMAP_CONSTRUCT_AGG, BITMAP_COUNT and "
                "BITMAP_OR_AGG."),
    ("SFN-017", "Runs only in Snowflake", "SEARCH and SEARCH_IP (Snowflake's search analyzers), COLLATE (Snowflake "
                "collation specifications), HASH_AGG (Snowflake's own hash), and XML: CHECK_XML, PARSE_XML, "
                "XMLGET, TO_XML."),
    ("SFN-018", "Runs only in Snowflake", "DECFLOAT, TO_DECFLOAT and TRY_TO_DECFLOAT: DuckDB has no decimal "
                "floating-point type."),
    ("SFN-019", "Runs only in Snowflake", "MATCH_RECOGNIZE: DuckDB has no row pattern matching."),
    ("SFN-020", "Runs only in Snowflake", "INSERT OVERWRITE: DuckDB has none, and a DELETE then INSERT wouldn't be "
                "one statement."),
    ("SFN-099", "Runs only in Snowflake", "Any other Snowflake command sqlglot can't read as a statement."),
)


def docs_table() -> str:
    """The rule list as the Markdown table in docs/engines.md."""
    lines = [f"Rules {RULES_VERSION}.", "", "| Id | What | Applies to |", "| --- | --- | --- |"]
    lines += [f"| {id} | {what} | {text} |" for id, what, text in CATALOG]
    return "\n".join(lines)


# ---------------------------------------------------------------- translate


def _statement(n: int, statement: exp.Expression) -> StatementReport:
    refusal = _not_translatable(statement)
    if refusal:
        return StatementReport(n, "not_translatable", None, (refusal,))
    tree = statement.copy()
    notes = _differences(tree)
    brackets = list(tree.find_all(exp.Bracket))  # held, so no new node can reuse one of these ids
    written = {id(b) for b in brackets}
    try:
        _rewrite_flatten(tree)
        _rewrite_to_char(tree)
        _rewrite_date_trunc(tree)
        _rewrite_object_construct(tree)
        _rewrite_values(tree)
        _rewrite_soundex(tree)
        _rewrite_ratio_to_report(tree)
        _rewrite_events(tree)
        _rewrite_haversine(tree)
        _rewrite_approximate(tree)
        _rewrite_strings(tree)
        _rewrite_time(tree)
        _rewrite_objects(tree)
        _rewrite_typeof(tree)
        _rewrite_variant_types(tree)
        unknown_brackets = _type_values(tree, written)
        _rewrite_json_text(tree)
        _rewrite_division(tree)
        sql = _duck(tree)
    except _Untranslatable as exc:
        return StatementReport(n, "not_translatable", None, (exc.note,))
    except SqlglotError as exc:  # UnsupportedError, or sqlglot stopping while it writes (TIME() with no argument)
        first = (str(exc).splitlines() or ["sqlglot can't rewrite it"])[0]
        return StatementReport(n, "not_translatable", None,
                               (_refuse("SFX-000", "Not rewritten for DuckDB", first + "."),))
    if unknown_brackets:
        notes.append(SFD_BRACKET)
    return StatementReport(n, "difference" if notes else "translated", sql, tuple(notes))


def translate(code: str) -> Translation:
    """Every statement of ``code`` rewritten for DuckDB, with its verdict. Nothing runs here.
    ValueError when the SQL can't be read at all."""
    return Translation(tuple(_statement(i, s) for i, s in enumerate(parse(code), 1)))
