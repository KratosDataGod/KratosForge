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

import datetime as dt
import json
import re
import uuid
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError

from . import sql_dialects

RULES_VERSION = "2026.11.23"
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


# What a cell knows of the session's tables, for SFX-074: (schema, table), both lower case, to each column's name
# (lower case) and kind, in the table's order. A kind is TIMESTAMP_TZ or TIMESTAMP_LTZ for a column a Snowflake
# cell made with that type, ZONED for a DuckDB TIMESTAMPTZ column whose Snowflake type isn't known, or OTHER.
Tables = Mapping[tuple[str, str], tuple[tuple[str, str], ...]]


@dataclass(frozen=True)
class Translation:
    statements: tuple[StatementReport, ...]
    rules_version: str = RULES_VERSION
    # the tables as they are after the cell, if every statement runs (SFX-074); not part of the label
    tables: tuple[tuple[tuple[str, str], tuple[tuple[str, str], ...]], ...] = dataclass_field(default=(), compare=False)

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
        lines = [(f"Snowflake SQL rewritten for DuckDB on this PC (rules {self.rules_version}). "
                 "Not checked against a Snowflake account.")]
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


SFD_CASE = _n("SFD-001", "A column made by an expression keeps DuckDB's name",
              "Snowflake stores and resolves an unquoted name in upper case, and so does this PC (SFX-072, SFX-073). "
              "A column made by an expression with no alias (a + 1) gets a name Snowflake writes its own way, and "
              "here it keeps DuckDB's. It's raised for CREATE TABLE ... AS and CREATE VIEW, where the name is "
              "stored. A query's result shows such a column under DuckDB's name too.",
              "Give such a column an alias.",
              f"{_DOCS}/identifiers-syntax")
SFD_TZ = _n("SFD-002", "TIMESTAMP_TZ loses its offset",
            "DuckDB stores TIMESTAMP_TZ as TIMESTAMPTZ, an instant, so the offset Snowflake keeps with each value "
            "is lost. Raised for a TIMESTAMP_TZ column, a TIMESTAMP_TZ made from a number, and an INSERT, UPDATE "
            "or MERGE that writes a value other than text to a TIMESTAMP_TZ column (or to a timestamp column "
            "with a time zone that no Snowflake cell in this session made). TO_TIMESTAMP_TZ, TRY_TO_TIMESTAMP_TZ, "
            "a cast to TIMESTAMP_TZ and text written to a TIMESTAMP_TZ column check the offset instead (SFX-071, "
            "SFX-074, SFN-071).",
            "Compare instants, not offsets.", f"{_DOCS}/data-types-datetime")
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
                "TYPEOF gives Snowflake's type names here (SFX-009). A JSON number with a fraction or an "
                "exponent that isn't read from PARSE_JSON of text written in the query stops the query (SFN-072), "
                "and so does a timestamp with a time zone whose Snowflake type isn't known (SFN-073). A type with "
                "no Snowflake name keeps DuckDB's name (INTERVAL, UUID, STRUCT, MAP). Raised when the value's type "
                "isn't known here: a column, a path or a computed value.",
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
SFD_MOMENTS = _n("SFD-013", "SKEW and STDDEV_POP can differ from the seventh digit",
                 "SKEW and STDDEV_POP give the exact sample skewness and population standard deviation here. "
                 "Snowflake's reference shows values that differ from about the seventh significant digit: "
                 "STDDEV_POP of 6, 10 and 14 is 3.265986375 there and 3.265986324 (the square root of 32/3) here; "
                 "SKEW of 1, 2, 2, 2, 2 is -2.236069766 there and -2.236067977 here. No Snowflake account was "
                 "used, so which digits Snowflake returns today isn't known.",
                 "Round SKEW and STDDEV_POP to six significant digits where results are compared with Snowflake's.",
                 f"{_DOCS}/functions/stddev_pop")

def _scalar_kind(node: exp.Expression | None) -> str:
    """The kind of a value written in the query (or cast to a type): text, number, null, or other."""
    while isinstance(node, (exp.Paren, exp.Neg)):
        node = node.this
    if isinstance(node, exp.Null):
        return "null"
    if isinstance(node, exp.Literal):
        return "text" if node.is_string else "number"
    if isinstance(node, (exp.Cast, exp.TryCast)) and _scalar_kind(node.this) in ("text", "number"):
        if node.to.this in exp.DataType.TEXT_TYPES:
            return "text"
        if node.to.this in exp.DataType.NUMERIC_TYPES:
            return "number"
    return "other"


def _mixed_array(node: object) -> bool:
    """SFN-025: an array written in the query with both text and numbers in it (ARRAY_CONSTRUCT,
    ARRAY_CONSTRUCT_COMPACT, or an ARRAY_INSERT, ARRAY_PREPEND or ARRAY_APPEND of the other kind to one)."""
    if isinstance(node, (exp.Array, exp.ArrayConstructCompact)):
        values = list(node.expressions)
    elif isinstance(node, (exp.ArrayInsert, exp.ArrayPrepend, exp.ArrayAppend)) and isinstance(node.this, exp.Array):
        values = [*node.this.expressions, node.args.get("expression")]
    else:
        return False
    return {_scalar_kind(v) for v in values} - {"null"} == {"text", "number"}


_ONLY_IN_SNOWFLAKE = "Runs only in Snowflake"


def _refuse(id: str, title: str, reason: str, source: str = "") -> Note:
    return _n(id, title, reason, _ONLY_IN_SNOWFLAKE + ".", source)


def _stop_sql(note: Note) -> str:
    """DuckDB SQL that stops the query with ``note``'s refusal, for a value this PC can tell only when the
    query runs: the statement runs, and stops at the first row that would answer differently."""
    text = f"{note.id} {note.title}: {note.reason} {note.action}"
    return "error(" + "'" + text.replace("'", "''") + "')"


def stopped_by_name(exc: BaseException) -> bool:
    """Whether DuckDB's error is a refusal _stop_sql wrote into the query, so it reads as one, not as a failure."""
    return bool(re.search(r"\bSFN-\d{3} .*" + _ONLY_IN_SNOWFLAKE + r"\.", str(exc)))


# SFN-070, SFN-071 and SFN-072 (2026.11.23): values that ran with another answer and only a label (SFD-012, SFD-014,
# SFD-008; SFD-002 and SFD-007 in part) now answer as Snowflake does or stop by name.
_JARO_REFUSED = _refuse(
    "SFN-070", "JAROWINKLER_SIMILARITY", "Snowflake doesn't document how it works out its score, and DuckDB's "
    "Jaro-Winkler gives other scores for pairs in Snowflake's own example: 'Gute nacht' and 'Ich weis nicht' score "
    "56 there and 57 here, 'Ich weiß nicht' and 'Ich weiss nicht' 97 there and 94 here, 'święta' and 'swieta' 77 "
    "there and 72 here. A threshold tuned on Snowflake's scores would match other rows. EDITDISTANCE runs here.",
    f"{_DOCS}/functions/jarowinkler_similarity")
_TZ_OFFSET = _refuse(
    "SFN-071", "TIMESTAMP_TZ with another UTC offset", "Snowflake keeps the UTC offset written in the text "
    "(2024-01-01T10:00:00+05:00) with the value. DuckDB keeps only the instant and shows it in the session's time "
    "zone, so its hour, date and text would differ. A text with no offset, or with the session zone's own "
    "offset, runs. Use TO_TIMESTAMP_LTZ, or set the session's time zone to one with that offset.",
    f"{_DOCS}/data-types-datetime")
_JSON_FRACTION = _refuse(
    "SFN-072", "The type of a JSON number with a fraction", "Snowflake's PARSE_JSON keeps a number written with a "
    "fraction (123.12) as DECIMAL and one written with an exponent (1.912e2) as DOUBLE. DuckDB's JSON keeps neither "
    "way of writing it once the value is stored or read from a path, so TYPEOF, IS_DECIMAL, AS_DECIMAL and AS_NUMBER "
    "of such a value stop. Of PARSE_JSON(text) itself, of a path into PARSE_JSON of text in the query, or of a "
    "TO_VARIANT value, they run.", f"{_DOCS}/functions/is_decimal")

# SFN-073 (2026.11.23): TYPEOF of a timestamp with a time zone gave DuckDB's name (SFD-007 in part).
_ZONED_TYPEOF = _refuse(
    "SFN-073", "TYPEOF of a timestamp with a time zone", "Snowflake's TYPEOF names TIMESTAMP_TZ and TIMESTAMP_LTZ "
    "apart. DuckDB keeps both as one type, so TYPEOF of such a value from a column or a computed value stops the query. "
    "TYPEOF of TO_TIMESTAMP_TZ, TO_TIMESTAMP_LTZ, a cast to either, or CURRENT_TIMESTAMP runs.",
    f"{_DOCS}/functions/is_timestamp")


# ---------------------------------------------------------------- reading


def parse(code: str) -> list[exp.Expression]:
    """Snowflake SQL statements as sqlglot reads them; ValueError when it can't be read."""
    try:
        statements = [cast(exp.Expression, s) for s in sql_dialects.parse(code, read="snowflake") if s is not None]
    except ParseError as exc:
        first = str(exc).splitlines()[0] if str(exc) else "it isn't valid Snowflake SQL"
        raise ValueError(f"Could not read the query: {first}") from None
    if not statements:
        raise ValueError("The query is empty.")
    return statements


# Statements sqlglot keeps only as a command, by their first words.
_COMMANDS = {
    "CREATE STREAM": ("SFN-001", "Streams", "A stream tracks changes inside Snowflake; DuckDB has no equivalent."),
    "CREATE OR REPLACE STREAM": ("SFN-001", "Streams", ("A stream tracks changes inside Snowflake; DuckDB has no "
                                                       "equivalent.")),
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
    "HASH": "HASH uses Snowflake's own hash, which DuckDB's hash doesn't reproduce.",
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
    if isinstance(statement, exp.MultitableInserts):
        return _refuse("SFN-026", f"INSERT {(statement.args.get('kind') or 'ALL').upper()}",
                       "DuckDB has no multi-table INSERT, and separate INSERT statements written here wouldn't be "
                       "one statement. Write one INSERT ... SELECT for each table, with a WHERE that picks the "
                       "rows it takes.", f"{_DOCS}/sql/insert-multi-table")
    if isinstance(statement, exp.Create) and any(
            isinstance(p, exp.LikeProperty) for p in (statement.args.get("properties") or exp.Properties()).expressions):
        return _refuse("SFN-027", "CREATE TABLE ... LIKE", "Snowflake copies the other table's column names, types, "
                       "defaults and constraints, and the rewrite can't see that table's definition. Write the "
                       "column definitions out in CREATE TABLE.", f"{_DOCS}/sql/create-table")
    if isinstance(statement, exp.TruncateTable) and statement.args.get("exists"):
        return _refuse("SFN-028", "TRUNCATE TABLE IF EXISTS", "DuckDB's TRUNCATE has no IF EXISTS, and the "
                       "rewrite can't tell whether the table is there. Leave out IF EXISTS when you know the table is there.",
                       f"{_DOCS}/sql/truncate-table")
    if isinstance(statement, exp.Alter) and (statement.args.get("kind") or "").upper() == "TABLE":
        actions = statement.args.get("actions") or []
        if any(isinstance(a, exp.SwapTable) for a in actions):
            return _refuse("SFN-029", "ALTER TABLE ... SWAP WITH", "DuckDB has no SWAP WITH. Rename the two "
                           "tables one at a time with ALTER TABLE ... RENAME TO.", f"{_DOCS}/sql/alter-table")
        if len(actions) > 1 or any(isinstance(a, exp.Drop) and len(a.args.get("tables") or []) > 1
                                   for a in actions):
            return _refuse("SFN-029", "ALTER TABLE with more than one column", "DuckDB's ALTER TABLE changes one "
                           "column at a time, and separate statements written here wouldn't change the table in "
                           "one step. Write one ALTER TABLE for each column.", f"{_DOCS}/sql/alter-table")
    if isinstance(statement, exp.Undrop):
        return _refuse("SFN-007", "UNDROP", "Restoring a dropped object uses Snowflake's Time Travel.")
    if isinstance(statement, (exp.Copy, exp.Put)):
        return _refuse("SFN-008", "Stages", "COPY INTO and PUT move files through Snowflake stages; a session on "
                       "this PC reads no files.")
    if isinstance(statement, exp.Use):
        return _refuse("SFN-009", "USE", "Warehouses, roles, databases and schemas are chosen in Snowflake; the "
                       "session on this PC has one database.")
    if isinstance(statement, (exp.Show, exp.Describe)):
        return _refuse("SFN-009", "SHOW and DESCRIBE", "These list Snowflake's own objects, in columns DuckDB's "
                       "SHOW and DESCRIBE don't give. To see a table this session made, query "
                       "information_schema.columns or information_schema.tables.")
    if isinstance(statement, exp.Alter) and (statement.args.get("kind") or "").upper() == "SESSION" \
            and _session_time_zone(statement) is None:
        return _refuse("SFN-022", "Session parameters", "Only ALTER SESSION SET TIMEZONE = '...' is rewritten "
                       "(to DuckDB's SET TimeZone). Other session parameters, and UNSET, change how Snowflake "
                       "itself reads or prints values.", f"{_DOCS}/sql/alter-session")
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
        if isinstance(node, exp.DynamicIdentifier):
            return _refuse("SFN-023", "IDENTIFIER() of a variable this cell doesn't set",
                           "IDENTIFIER($name) is rewritten only when the same cell sets the variable to a string "
                           "first (SET name = '...'); here its value isn't known. Set it in this cell, or write the name out.",
                           f"{_DOCS}/identifier-literal")
        if isinstance(node, exp.Pivot) and any(
                isinstance(value, (exp.PivotAny, exp.Select, exp.Subquery))
                for field in node.args.get("fields") or [] for value in field.expressions):
            return _refuse("SFN-024", "PIVOT on ANY or a subquery",
                           "DuckDB's PIVOT ... FOR ... IN takes a list of values; write the values out.",
                           f"{_DOCS}/constructs/pivot")
        if isinstance(node, exp.JarowinklerSimilarity):
            return _JARO_REFUSED
        if isinstance(node, exp.StrToTime) and isinstance(node.args.get("target_type"), exp.DataType) \
                and node.args["target_type"].this == exp.DataType.Type.TIMESTAMPTZ \
                and isinstance(node.args.get("format"), exp.Literal) and "%" in node.args["format"].name \
                and re.search(r"%:?z|%Z", node.args["format"].name):
            return _TZ_OFFSET  # TZH:TZM in the format reads an offset, which DuckDB doesn't keep
        if isinstance(node, exp.Uniform) and not (isinstance(node.args.get("gen"), exp.Rand)
                                                  and node.args["gen"].this is None):
            return _refuse("SFN-021", "UNIFORM with a generator other than RANDOM()",
                           "Snowflake turns any other generator (a number, a column or a seeded RANDOM) into its own series, "
                           "which this PC can't reproduce. UNIFORM(min, max, RANDOM()) runs here.",
                           f"{_DOCS}/functions/uniform")
        if isinstance(node, (exp.ToDecfloat, exp.TryToDecfloat)) or (
                isinstance(node, exp.DataType) and node.this == exp.DataType.Type.DECFLOAT):
            return _refuse("SFN-018", "DECFLOAT", "DuckDB has no decimal floating-point type to hold Snowflake's "
                           "DECFLOAT (38 significant digits with an exponent).", f"{_DOCS}/functions/to_decfloat")
        if _mixed_array(node) and isinstance(node, exp.Func):
            name = "ARRAY_CONSTRUCT" if isinstance(node, exp.Array) else _function_name(node)
            return _refuse("SFN-025", f"{name}: text and numbers in one array",
                           "A DuckDB list holds values of one type, so an array written with both text and numbers "
                           "can't be made here. Cast them to one type (for example each value ::VARCHAR), or "
                           "build the array from a column.", f"{_DOCS}/functions/array_construct")
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
                and _SPACED_OFFSET_TEXT.search(node.this.name) and not _typeof_argument(node):
            return _SPACED_OFFSET
    return None


_SPACED_OFFSET_TEXT = re.compile(r"\s([+-]\d{2}:?\d{2})$")


def _typeof_argument(node: exp.Expression) -> bool:
    """Whether ``node`` is what TYPEOF names (inside TO_VARIANT or a cast to VARIANT): SFX-009 needs only its
    type, so an offset DuckDB can't read in it doesn't matter."""
    parent = node.parent
    while isinstance(parent, exp.ToVariant) or (isinstance(parent, (exp.Cast, exp.TryCast))
                                                and parent.to.is_type(exp.DataType.Type.VARIANT)):
        parent = parent.parent
    return isinstance(parent, exp.Typeof)


def _offset_unspaced(value: exp.Expression) -> None:
    """Text with an offset after a space ('2024-01-02 16:00:00 +00:00') written as DuckDB reads it, with no space."""
    for literal in value.find_all(exp.Literal):
        if literal.is_string and _SPACED_OFFSET_TEXT.search(literal.name):
            literal.set("this", _SPACED_OFFSET_TEXT.sub(r"\1", literal.name))
_SPACED_OFFSET = _refuse("SFN-013", "TIMESTAMP_TZ text with an offset", "DuckDB can't read an offset written "
                         "after a space ('2026-01-01 10:00:00 +05:00'): it takes it for a time zone name.",
                         f"{_DOCS}/data-types-datetime")


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
    person's. A scalar has nothing to flatten. With OUTER, a value with nothing to flatten gives one row: NULL KEY, INDEX and VALUE, and the
    value as THIS."""
    from .sql import _with_args
    # a VARIANT column goes through JSON, which json_each reads (it reads a VARIANT as text that isn't JSON)
    this = source.copy()
    if path:
        this = _with_args(f"json_extract(CAST(fj_arg0 AS JSON), '{'$' if path.startswith('[') else '$.'}{path}')",
                          [this])
    array = "json_type(CAST(fj_arg0 AS JSON)) = 'ARRAY'"
    # only an array or an object has entries: a scalar, JSON null or a missing PATH gives no rows (json_each
    # would give a scalar one row), and one row of NULLs with OUTER
    compound = "coalesce(json_type(CAST(fj_arg0 AS JSON)) IN ('ARRAY', 'OBJECT'), FALSE)"
    field = "__sf_e.key" if not path else f"'{path}.' || __sf_e.key"
    # the columns are named in upper case, as Snowflake names them (SFX-073)
    return _with_args(f"(SELECT CASE WHEN {array} THEN NULL ELSE __sf_e.key END AS \"KEY\", "
                      f"CASE WHEN {array} THEN '{path}[' || __sf_e.key || ']' ELSE {field} END AS \"PATH\", "
                      f"CASE WHEN {array} THEN CAST(__sf_e.key AS INT) END AS \"INDEX\", __sf_e.value AS \"VALUE\", "
                      f"CAST(fj_arg0 AS JSON) AS \"THIS\" FROM json_each(CAST(fj_arg0 AS JSON)) AS __sf_e WHERE {compound}"
                      + (f" UNION ALL SELECT NULL, '{path}', NULL, NULL, CAST(fj_arg0 AS JSON) WHERE NOT {compound} "
                         "OR NOT EXISTS (SELECT 1 FROM json_each(CAST(fj_arg0 AS JSON)))" if outer else "") + ")",
                      [this])


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
                                         columns=[exp.to_identifier(f"COLUMN{i}", quoted=True)
                                                  for i in range(1, width + 1)]))


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


_WEEKDAYS = {"mo": "monday", "tu": "tuesday", "we": "wednesday", "th": "thursday", "fr": "friday", "sa": "saturday",
             "su": "sunday"}
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
# TIME_SLICE counts slices from 1970-01-01 (weeks from Monday 1969-12-29, the start of the week holding it);
# DuckDB's TIME_BUCKET counts from 2000-01-03 for days and shorter and from 2000-01-01 for months, so the
# offset moves its origin back to Snowflake's.
_SLICE_UNITS = {"SECOND": ("SECOND", 1, "-10959 days"), "MINUTE": ("MINUTE", 1, "-10959 days"),
                "HOUR": ("HOUR", 1, "-10959 days"), "DAY": ("DAY", 1, "-10959 days"),
                "WEEK": ("WEEK", 1, "-10962 days"), "MONTH": ("MONTH", 1, "-360 months"),
                "QUARTER": ("MONTH", 3, "-360 months"), "YEAR": ("MONTH", 12, "-360 months")}


def _auto_date(text: str) -> dt.date | None:
    """A date in one of the AUTO input formats DuckDB's cast doesn't read: MM/DD/YYYY and DD-MON-YYYY
    (date-time-input-output). None for any other text, or a date that doesn't exist."""
    try:
        if m := re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", text):
            return dt.date(int(m[3]), int(m[1]), int(m[2]))
        if (m := re.fullmatch(r"\s*(\d{1,2})-([A-Za-z]{3})-(\d{4})\s*", text)) and m[2].upper() in _MONTHS:
            return dt.date(int(m[3]), _MONTHS.index(m[2].upper()) + 1, int(m[1]))
    except ValueError:
        return None
    return None


def _epoch_date(text: str) -> dt.date | None:
    """TO_DATE of a string holding a whole number: seconds, milliseconds, microseconds or nanoseconds by its
    size, as TO_TIMESTAMP reads it (SFX-022), then the date."""
    if not re.fullmatch(r"-?\d+", text.strip()):
        return None
    number = int(text)
    scale = next(s for limit, s in ((31536000000, 0), (31536000000000, 3), (31536000000000000, 6), (None, 9))
                 if limit is None or number < limit)
    try:
        micro = number * 10 ** (6 - scale) if scale <= 6 else number // 10 ** (scale - 6)
        return (dt.datetime(1970, 1, 1) + dt.timedelta(microseconds=micro)).date()
    except OverflowError:
        return None


def _date_literal(day: dt.date | None) -> exp.Expression:
    return exp.cast(exp.Literal.string(day.isoformat()), "DATE") if day else exp.cast(exp.null(), "DATE")


def _rewrite_documented_edges(statement: exp.Expression) -> None:
    """SFX-026: where DuckDB's function answered differently from Snowflake's documented rule.

    - REGEXP_SUBSTR is NULL when nothing matches ("returns NULL ... No match is found"); DuckDB's
      REGEXP_EXTRACT gave ''.
    - CEIL and FLOOR with a scale written as a whole number stay exact for a NUMBER: CEIL(17.94, 2) is 17.94.
      DuckDB's rewrite went through FLOAT (17.94 * 100 is 1794.0000000000002) and gave 17.95.
    - EDITDISTANCE with a negative maximum "uses 0 as the maximum distance and returns 0".
    - NEXT_DAY and PREVIOUS_DAY read the first two letters of the day: "Any leading spaces and trailing
      characters, including spaces, in the string are ignored."
    - CONTAINS with a bare NULL is NULL (DuckDB couldn't choose which CONTAINS was meant).
    - TIME_SLICE counts its slices from 1970-01-01, as "All slices are aligned relative to midnight January
      1, 1970"; TIME_BUCKET counted them from 2000.
    - TO_DATE and a cast to DATE of a string written in the query read MM/DD/YYYY and DD-MON-YYYY, and TO_DATE
      of a whole number reads it as epoch seconds, milliseconds, microseconds or nanoseconds by its size.
    Arguments may be worked out more than once, which matters only when they aren't deterministic."""
    from .sql import _from_template
    for found in reversed(list(statement.find_all(exp.RegexpExtract))):
        if found.args.get("occurrence") is not None and not (isinstance(found.args["occurrence"], exp.Literal)
                                                             and found.args["occurrence"].name == "1"):
            continue  # a later occurrence is read from REGEXP_EXTRACT_ALL, which is NULL past the last match
        subject = found.this
        start = found.args.get("position")
        if start is not None and not (isinstance(start, exp.Literal) and start.name == "1"):
            subject = exp.Substring(this=subject.copy(), start=start.copy())
        flags = found.args.get("parameters")
        mode = "".join(c for c in flags.name if c in "cis") if isinstance(flags, exp.Literal) else ""
        test = [subject.copy(), found.expression.copy()] + ([exp.Literal.string(mode)] if mode else [])
        found.replace(exp.Case(ifs=[exp.If(this=exp.Anonymous(this="REGEXP_MATCHES", expressions=test),
                                           true=found.copy())]))
    for rounded in reversed(list(statement.find_all(exp.Ceil, exp.Floor))):
        scale = rounded.args.get("decimals")
        if scale is None:
            continue
        sign, places = (-1, scale.this) if isinstance(scale, exp.Neg) else (1, scale)
        if not (isinstance(places, exp.Literal) and not places.is_string and places.name.isdigit()):
            continue
        k = sign * int(places.name)
        # Up to 6 places (the step's scale joins the NUMBER's, so a larger one could leave too few digits for a very
        # large value) and down to -18; any other scale is left to the FLOAT rewrite, and no huge text is built.
        if not -18 <= k <= 6:
            continue
        step = f"0.{'0' * (k - 1)}1" if k > 0 else f"CAST(1{'0' * -k} AS DECIMAL({1 - k}, 0))"
        ceil = isinstance(rounded, exp.Ceil)
        beyond, move = ("kf_x > TRUNC(kf_x, {k})", step) if ceil else ("kf_x < TRUNC(kf_x, {k})", f"-{step}")
        # TRUNC to the scale, then one step up (CEIL) or down (FLOOR) when something was cut off: a NUMBER stays
        # exact and can't overflow as a multiply by 10^scale would, and ROUND clears a FLOAT's last bits. The value
        # is read once, in a lambda.
        body = (f"ROUND(TRUNC(kf_x, {k}) + CASE WHEN {beyond.format(k=k)} THEN {move} ELSE 0 END, {k})")
        rounded.replace(_from_template(f"(LIST_TRANSFORM([{{0}}], kf_x -> {body}))[1]", [rounded.this]))
    for distance in list(statement.find_all(exp.Levenshtein)):
        most = distance.args.get("max_dist")
        if most is None or (isinstance(most, exp.Literal) and not most.is_string):
            continue
        if isinstance(most, exp.Neg) and isinstance(most.this, exp.Literal):
            distance.set("max_dist", exp.Literal.number(0))
            continue
        distance.set("max_dist", _from_template("(CASE WHEN {0} < 0 THEN 0 ELSE {0} END)", [most]))
    for day in list(statement.find_all(exp.NextDay, *([exp.PreviousDay] if hasattr(exp, "PreviousDay") else []))):
        name = day.expression
        if isinstance(name, exp.Literal) and name.is_string:
            code = name.name.lstrip(" ")[:2].lower()
            if code not in _WEEKDAYS:
                raise _Untranslatable(_refuse("SFX-026", f"{_function_name(day)} day", "The day must start with "
                                              "the first two letters of a day's name.", f"{_DOCS}/functions/next_day"))
            day.set("expression", exp.Literal.string(_WEEKDAYS[code]))
    for contains in list(statement.find_all(exp.Contains)):
        for key in ("this", "expression"):
            if isinstance(contains.args.get(key), exp.Null):
                contains.set(key, exp.cast(exp.null(), "VARCHAR"))
    for slice_ in list(statement.find_all(exp.TimeSlice)):
        unit = slice_.args.get("unit")
        size = slice_.expression
        kind = slice_.args.get("kind")
        if not (isinstance(unit, exp.Var) and unit.name.upper() in _SLICE_UNITS and isinstance(size, exp.Literal)
                and not size.is_string and size.name.isdigit()):
            continue
        duck, times, offset = _SLICE_UNITS[unit.name.upper()]
        width = f"INTERVAL {int(size.name) * times} {duck}"
        end = isinstance(kind, exp.Literal) and kind.name.upper() == "END"
        bucket = f"TIME_BUCKET({width}, {{0}}, INTERVAL '{offset}')"
        if end:  # a DATE plus an INTERVAL is a TIMESTAMP in DuckDB; TIME_SLICE of a DATE is a DATE
            bucket = f"CAST({bucket} + {width} AS DATE)" if _plainly_a_date(slice_.this) else f"{bucket} + {width}"
        slice_.replace(_from_template(f"({bucket})", [slice_.this]))
    for cast_ in list(statement.find_all(exp.Cast, exp.TryCast)):
        value = cast_.this
        if cast_.to.is_type(exp.DataType.Type.DATE) and isinstance(value, exp.Literal) and value.is_string \
                and not re.fullmatch(r"\s*\d{4}-\d{1,2}-\d{1,2}.*", value.name):
            read = _auto_date(value.name)
            if read is not None or (isinstance(cast_, exp.TryCast) and re.fullmatch(r"\s*\d{1,2}[/-]\w+[/-]\d{4}\s*",
                                                                                     value.name)):
                cast_.replace(_date_literal(read))
    for call in list(statement.find_all(exp.Anonymous)):
        name = call.name.upper()
        if name in ("TO_DATE", "TRY_TO_DATE", "DATE") and len(call.expressions) == 1:
            value = call.expressions[0]
            if isinstance(value, exp.Literal) and value.is_string and re.fullmatch(r"\s*-?\d+\s*", value.name):
                epoch = _epoch_date(value.name)
                if epoch is None and name != "TRY_TO_DATE":
                    continue  # past the dates DuckDB holds: left to stop loudly
                call.replace(_date_literal(epoch))


def _rewrite_arrays(statement: exp.Expression) -> None:
    """SFX-019: ARRAY_DISTINCT keeps each value's first place and one NULL where the first NULL was, as
    Snowflake's does (DuckDB's LIST_DISTINCT doesn't keep the order). ARRAY_FLATTEN of an array holding
    a NULL in place of an array is NULL, as in Snowflake; DuckDB's FLATTEN skips it. ARRAY_INSERT past the
    end fills the gap with NULLs (Snowflake leaves empty elements); DuckDB's slices would close it."""
    for distinct in list(statement.find_all(exp.ArrayDistinct)):
        # the array is worked out once (fj_arg0 is read once), then searched for each element's first place
        _put(distinct, "LIST_TRANSFORM([fj_arg0], LAMBDA kf_l: "
                       "LIST_FILTER(kf_l, LAMBDA kf_x, kf_i: LIST_POSITION(kf_l, kf_x) = kf_i))[1]",
             [distinct.this])
    for flatten in list(statement.find_all(exp.Flatten)):
        if flatten.expressions:
            continue
        _put(flatten, "CASE WHEN LEN(LIST_FILTER(fj_arg0, LAMBDA kf_e: kf_e IS NULL)) > 0 THEN NULL "
                      "ELSE FLATTEN(fj_arg0) END", [flatten.this])
    # ARRAY_INSERT past the end: Snowflake fills the gap with empty elements; DuckDB's slices would close it
    for insert in list(statement.find_all(exp.ArrayInsert)):
        position = insert.args.get("position")
        if isinstance(position, exp.Literal) and not position.is_string and position.name.isdigit():
            _put(insert.this, "LIST_RESIZE(fj_arg0, GREATEST(LEN(fj_arg0), fj_arg1))",
                 [insert.this.copy(), position.copy()])


def _rewrite_variant_reads(statement: exp.Expression) -> None:
    """SFX-023: GET and the : path read a VARIANT made with TO_VARIANT (or a cast to VARIANT), or a column
    (a VARIANT column among them), as JSON, as Snowflake reads any VARIANT; DuckDB reads its own VARIANT as
    text that isn't JSON, and stops. A JSON or text column holding JSON reads the same once cast."""
    for read in list(statement.find_all(exp.GetExtract, exp.JSONExtract)):
        value = read.this
        if isinstance(value, (exp.ToVariant, exp.Column)) or (isinstance(value, (exp.Cast, exp.TryCast))
                                                              and value.to.is_type(exp.DataType.Type.VARIANT)):
            value.replace(exp.Cast(this=value.copy(), to=exp.DataType.build("JSON")))


_MERGE_DUPLICATE = ("A target row matches more than one source row, so the MERGE stopped without changing "
                    "anything. Snowflake stops on it too (ERROR_ON_NONDETERMINISTIC_MERGE). Make the source one row "
                    "per target row, for example with GROUP BY or QUALIFY ROW_NUMBER() OVER (PARTITION BY key "
                    "ORDER BY ...) = 1.")


def _rewrite_statements(statement: exp.Expression) -> None:
    """SFX-025: what Snowflake's statements do that DuckDB's don't by themselves.
    - MERGE with WHEN MATCHED ... UPDATE stops when a target row matches more than one source row, as
      Snowflake does by default; DuckDB would update it from one of them. (It stops even when those rows
      would give the same values, or when a DELETE clause would take the row: Snowflake's page doesn't
      say what happens then.)
    - PIVOT ... IN ('a', ...) names its columns 'a' with the quotes, as Snowflake does.
    - $name reads the session variable (DuckDB's getvariable).
    - ARRAY_SIZE of a JSON or VARIANT value counts a JSON array, and is NULL for anything else.
    - SAMPLE ROW and SAMPLE BLOCK are Snowflake's names for BERNOULLI and SYSTEM, which DuckDB reads.
    (A VARIANT column cast to text: _variant_text.)"""
    for sample in statement.find_all(exp.TableSample):
        method = sample.args.get("method")
        if isinstance(method, exp.Var) and method.name.upper() in ("ROW", "BLOCK"):
            method.replace(exp.var("BERNOULLI" if method.name.upper() == "ROW" else "SYSTEM"))
    merge_whens = statement.args.get("whens") if isinstance(statement, exp.Merge) else None
    if merge_whens is not None and any(
            isinstance(when, exp.When) and when.args.get("matched") and isinstance(when.args.get("then"), exp.Update)
            for when in merge_whens.expressions):
        source = statement.args.get("using")
        alias = source.alias_or_name if source is not None else ""
        on = statement.args.get("on")
        if source is not None and alias and on is not None:
            inner = source.copy()
            if not inner.alias:
                inner.set("alias", exp.TableAlias(this=exp.to_identifier(alias)))
            count = exp.select(exp.Count(this=exp.Star())).from_(inner).where(on.copy())
            check = exp.When(matched=True, condition=exp.GT(this=exp.Subquery(this=count),
                                                            expression=exp.Literal.number(1)),
                             then=exp.var(f"ERROR {_quoted(_MERGE_DUPLICATE)}"))
            merge_whens.expressions.insert(0, check)
    for pivot in statement.find_all(exp.Pivot):
        for field in pivot.args.get("fields") or []:
            for value in list(field.expressions):
                if isinstance(value, exp.Literal) and value.is_string:
                    value.replace(exp.PivotAlias(this=value.copy(),
                                                 alias=exp.to_identifier(f"'{value.name}'", quoted=True)))
    for parameter in list(statement.find_all(exp.Parameter)):
        if isinstance(parameter.this, exp.Var) and not isinstance(parameter.parent, exp.DynamicIdentifier):
            parameter.replace(exp.Anonymous(this="getvariable",
                                            expressions=[exp.Literal.string(parameter.this.name.upper())]))
    for size in list(statement.find_all(exp.ArraySize)):
        value = size.this
        if isinstance(value, (exp.Column, exp.JSONExtract, exp.GetExtract, exp.ParseJSON, exp.ToVariant)) or (
                isinstance(value, (exp.Cast, exp.TryCast)) and value.to.is_type(exp.DataType.Type.VARIANT,
                                                                                 exp.DataType.Type.JSON)):
            _keep_name(size)
            _put(size, "CASE WHEN json_type(CAST(CAST(fj_arg0 AS VARIANT) AS JSON)) = 'ARRAY' "
                       "THEN CAST(json_array_length(CAST(CAST(fj_arg0 AS VARIANT) AS JSON)) AS BIGINT) END", [value])


def _merge_set_names(statement: exp.Expression) -> None:
    """SFX-027: UPDATE SET target.col = ... in a MERGE names the target's column, as Snowflake's own MERGE
    examples write it; DuckDB takes only the bare column name there."""
    if not isinstance(statement, exp.Merge) or not isinstance(statement.this, exp.Table):
        return
    target = {statement.this.name.upper(), statement.this.alias_or_name.upper()}
    for when in (statement.args.get("whens") or exp.Whens()).expressions:
        update = when.args.get("then") if isinstance(when, exp.When) else None
        if not isinstance(update, exp.Update):
            continue
        for assignment in update.expressions:
            column = assignment.this if isinstance(assignment, exp.EQ) else None
            if isinstance(column, exp.Column) and column.table and column.table.upper() in target \
                    and not column.args.get("db"):
                column.set("table", None)


# databricks_builtins_sql.IDENTITY_PREFIXES names it, so DuckDBSession.plan() makes these sequences before it
# plans the CREATE TABLE, as for a Databricks identity column
_IDENTITY_SEQUENCE = "__kf_identity_"
_BIGINT_RANGE = "MINVALUE -9223372036854775808 MAXVALUE 9223372036854775807"


def _whole(value: exp.Expression | None, default: int) -> int:
    if value is None:
        return default
    negative = isinstance(value, exp.Neg)
    number = value.this if negative else value
    if not (isinstance(number, exp.Literal) and not number.is_string and number.name.isdigit()):
        raise _Untranslatable(_refuse("SFX-000", "Not rewritten for DuckDB", "AUTOINCREMENT and IDENTITY are "
                                      "rewritten only with whole numbers written for their start and step."))
    whole = None if len(number.name) > 19 else (-int(number.name) if negative else int(number.name))
    if whole is None or not -(2 ** 63) <= whole < 2 ** 63:
        raise _Untranslatable(_refuse("SFX-000", "Not rewritten for DuckDB", "AUTOINCREMENT and IDENTITY are "
                                      "rewritten only when their start and step fit in a BIGINT."))
    return whole


def _rewrite_tables(statement: exp.Expression) -> list[exp.Expression]:
    """SFX-027: CREATE TABLE as Snowflake makes a standard table. Returns statements to run before it.
    - PRIMARY KEY, UNIQUE and FOREIGN KEY aren't enforced by Snowflake, so they are dropped; a PRIMARY KEY
      column keeps NOT NULL, which the constraint implies (constraints-overview).
    - AUTOINCREMENT and IDENTITY (start and step 1 unless given) become a DuckDB sequence the column takes
      its DEFAULT from. Snowflake's values are unique but not always one after another, so DuckDB's are
      among those it could give.
    - TRANSIENT and CLUSTER BY change how Snowflake keeps the table, not what a query reads; both are dropped."""
    if not (isinstance(statement, exp.Create) and (statement.args.get("kind") or "").upper() == "TABLE"):
        return []
    properties = statement.args.get("properties")
    if isinstance(properties, exp.Properties):
        for prop in list(properties.expressions):
            if isinstance(prop, (exp.TransientProperty, exp.ClusterProperty)):
                prop.pop()
        if not properties.expressions:
            statement.set("properties", None)
    schema = statement.this
    if not isinstance(schema, exp.Schema):
        return []
    columns = {c.name.upper(): c for c in schema.expressions if isinstance(c, exp.ColumnDef)}
    not_null: set[str] = set()
    for item in list(schema.expressions):
        kinds = item.expressions if isinstance(item, exp.Constraint) else [item]
        keys = [k for k in kinds if isinstance(k, (exp.PrimaryKey, exp.UniqueColumnConstraint, exp.ForeignKey))]
        if not keys:
            continue
        for key in keys:
            if isinstance(key, exp.PrimaryKey):
                not_null.update(e.name.upper() for e in key.expressions)
        if len(keys) == len(kinds):
            item.pop()
    sequences: list[exp.Expression] = []
    for name, column in columns.items():
        constraints = column.args.get("constraints") or []
        if any(isinstance(c.kind, exp.PrimaryKeyColumnConstraint) for c in constraints):
            not_null.add(name)
        for constraint in list(constraints):
            kind = constraint.kind
            if isinstance(kind, (exp.PrimaryKeyColumnConstraint, exp.UniqueColumnConstraint, exp.Reference)):
                constraint.pop()
            elif isinstance(kind, (exp.AutoIncrementColumnConstraint, exp.GeneratedAsIdentityColumnConstraint)):
                start = _whole(kind.args.get("start"), 1) if isinstance(kind, exp.GeneratedAsIdentityColumnConstraint) else 1
                step = _whole(kind.args.get("increment"), 1) if isinstance(kind, exp.GeneratedAsIdentityColumnConstraint) else 1
                if step == 0:
                    raise _Untranslatable(_refuse("SFX-000", "Not rewritten for DuckDB",
                                                  "An AUTOINCREMENT step of 0 isn't rewritten."))
                sequence = f"{_IDENTITY_SEQUENCE}{uuid.uuid4().hex}"
                constraint.set("kind", exp.DefaultColumnConstraint(
                    this=exp.Anonymous(this="nextval", expressions=[exp.Literal.string(sequence)])))
                sequences.append(cast(exp.Expression, sqlglot.parse_one(  # this rule's own name, two whole numbers
                    f"CREATE SEQUENCE {sequence} START WITH {start} INCREMENT BY {step} {_BIGINT_RANGE}",
                    read="duckdb")))
    for name in not_null:
        key_column = columns.get(name)
        if key_column is not None and not any(isinstance(c.kind, exp.NotNullColumnConstraint)
                                              for c in key_column.args.get("constraints") or []):
            key_column.append("constraints", exp.ColumnConstraint(kind=exp.NotNullColumnConstraint()))
    return sequences


def _upper(identifier: object) -> None:
    """An unquoted name as Snowflake stores it: upper case, and quoted so DuckDB keeps it so."""
    if isinstance(identifier, exp.Identifier) and not identifier.args.get("quoted"):
        identifier.set("this", identifier.name.upper())
        identifier.set("quoted", True)


def _output_names(query: object) -> bool:
    """SFX-072 for a CREATE TABLE AS or CREATE VIEW query: each column it names (a column, or an alias) gets its
    name as Snowflake stores it. False when a column is an expression with no alias, whose name Snowflake writes
    its own way (SFD-001)."""
    while isinstance(query, exp.SetOperation):  # the first query names a UNION's columns
        query = query.this
    if isinstance(query, exp.Subquery):
        return _output_names(query.this)
    if not isinstance(query, exp.Select):
        return True
    named = True
    for item in query.expressions:
        if isinstance(item, exp.Alias):
            _upper(item.args.get("alias"))
        elif isinstance(item, exp.Column) and isinstance(item.this, exp.Identifier):
            if item.this.args.get("quoted"):
                item.replace(exp.alias_(item.copy(), exp.Identifier(this=item.name, quoted=True)))
            else:
                item.replace(exp.alias_(item.copy(), exp.Identifier(this=item.name.upper(), quoted=True)))
        elif not isinstance(item, exp.Star) and not (isinstance(item, exp.Column) and isinstance(item.this, exp.Star)):
            named = False
    return named


_SELF_ALIAS = re.compile(r'Binder Error: Column "((?:[^"]|"")+)" referenced that exists in the SELECT clause - but '
                         r'this column cannot be referenced before it is defined')


def column_error(message: str) -> str:
    """SFX-073 names ``SELECT nosuch`` as ``nosuch AS "NOSUCH"``. When the table has no such column, DuckDB
    then finds the alias by its name and says the column can't be used before it's defined, which hides the
    typo. That message becomes DuckDB's own for a missing column (Snowflake reports an invalid identifier).
    Any other message is given back as it is."""
    found = _SELF_ALIAS.search(message)
    if found is None:
        return message
    return (f'{message[:found.start()]}Binder Error: Referenced column "{found.group(1)}" not found in FROM clause!'
            + message[found.end():])


def _rewrite_names(statement: exp.Expression) -> bool:
    """SFX-072: Snowflake stores an unquoted name in upper case (identifiers-syntax), so CREATE TABLE, CREATE
    TEMPORARY TABLE, CREATE VIEW, CREATE SCHEMA and ALTER TABLE store the names they make that way here too: the
    table, view or schema, its columns, a renamed table or column, an added column. INFORMATION_SCHEMA and
    SELECT * then give Snowflake's names; a query finds them by any case, as Snowflake finds an unquoted
    name (DuckDB also finds a quoted name by any case, where Snowflake needs its stored case). The schema
    of mysch.tab and a column list with no types (CREATE VIEW v (a, b) AS, CREATE TABLE t (a, b) AS) are
    names the statement makes too. False when a CREATE ... AS column is an expression with no alias and no
    column list names it (SFD-001)."""
    named = True
    kind = (statement.args.get("kind") or "").upper()
    if isinstance(statement, exp.Create) and kind in ("TABLE", "VIEW", "SCHEMA"):
        target = statement.this
        table = target.this if isinstance(target, exp.Schema) else target
        if isinstance(table, exp.Table):
            if kind == "SCHEMA" and table.args.get("this") is None:
                _upper(table.args.get("db"))
            else:
                _upper(table.args.get("this"))
                _upper(table.args.get("db"))  # mysch.tab: the schema is resolved as MYSCH
        listed = False
        if isinstance(target, exp.Schema):
            for column in target.expressions:
                if isinstance(column, exp.ColumnDef):
                    _upper(column.this)
                elif isinstance(column, exp.Identifier):  # CREATE VIEW v (a, b) AS: a name with no type
                    _upper(column)
                listed = True
        if statement.expression is not None:
            named = _output_names(statement.expression) or listed
    elif isinstance(statement, exp.Alter) and (statement.args.get("kind") or "").upper() == "TABLE":
        for action in statement.args.get("actions") or []:
            if isinstance(action, exp.ColumnDef):
                _upper(action.this)
            elif isinstance(action, exp.AlterRename) and isinstance(action.this, exp.Table):
                _upper(action.this.args.get("this"))
            elif isinstance(action, exp.RenameColumn) and isinstance(action.args.get("to"), exp.Column):
                _upper(action.args["to"].this)
    return named


_WRITTEN_NAMES = "kf_sf_written_names"  # a SELECT or alias list the query wrote: SFX-073 names its columns


def _mark_result_names(statement: exp.Expression) -> None:
    """SFX-073, before the rewrites: the SELECTs and the alias column lists (AS v(a, b), WITH c(a) AS ...) the
    query wrote are marked (a copy keeps the mark), so only those are named, not the ones a rewrite writes."""
    for node in statement.find_all(exp.Select, exp.TableAlias, exp.Typeof):
        node.meta[_WRITTEN_NAMES] = True  # and the TYPEOF calls the query wrote, for SFN-073


def _rewrite_result_names(statement: exp.Expression) -> None:
    """SFX-073: Snowflake resolves an unquoted name in upper case (identifiers-syntax), so a result's column is
    named that way: SELECT last_name FROM t gives LAST_NAME and ... AS area_of_circle gives AREA_OF_CIRCLE (the
    SELECT page). In each SELECT the query wrote, a column with no alias and an unquoted alias (also in * RENAME
    and * REPLACE) get the upper-case name, quoted so DuckDB keeps it; so do the names of an alias column list.
    A quoted name keeps its case. DuckDB finds a name by any case, so the query reads as before. A column made
    by an expression with no alias keeps DuckDB's name (SFD-001)."""
    for node in list(statement.find_all(exp.Select, exp.TableAlias)):
        if not node.meta.pop(_WRITTEN_NAMES, False):
            continue
        if isinstance(node, exp.TableAlias):
            for column in node.columns:
                _upper(column)
            continue
        for item in list(node.expressions):
            if isinstance(item, exp.Alias):
                _upper(item.args.get("alias"))
            elif isinstance(item, exp.Column) and isinstance(item.this, exp.Identifier) \
                    and not item.this.args.get("quoted"):
                item.replace(exp.alias_(item.copy(), exp.Identifier(this=item.name.upper(), quoted=True)))
            star = item.this if isinstance(item, exp.Column) else item
            if isinstance(star, exp.Star):
                for key in ("rename", "replace"):
                    for renamed in star.args.get(key) or []:
                        if isinstance(renamed, exp.Alias):
                            _upper(renamed.args.get("alias"))


def _variant_text(statement: exp.Expression) -> None:
    """SFX-025: a VARIANT column holding an object or array, cast to text (or TO_VARCHAR with no format), is
    JSON text, as Snowflake's TO_VARCHAR gives it; DuckDB writes its own VARIANT its own way ({'k': 1}).
    Run first, so only a cast written in the query is read (not one another rewrite writes)."""
    for to_text_node in list(statement.find_all(exp.Cast, exp.TryCast, exp.ToChar)):
        to_text = (isinstance(to_text_node, exp.ToChar) and to_text_node.args.get("format") is None
                   and not to_text_node.args.get("nlsparam")) or (isinstance(to_text_node, exp.Cast)
                                                         and to_text_node.to.this in _TEXT_TYPES)
        if isinstance(to_text_node.this, exp.Column) and to_text:
            column = to_text_node.this
            kind = exp.Anonymous(this="variant_typeof", expressions=[
                exp.Cast(this=column.copy(), to=exp.DataType.build("VARIANT"))])
            compound = exp.and_(
                exp.EQ(this=exp.Anonymous(this="typeof", expressions=[column.copy()]),
                       expression=exp.Literal.string("VARIANT")),
                exp.RegexpLike(this=kind, expression=exp.Literal.string("^(OBJECT|ARRAY)")))
            # TRY_CAST: DuckDB may work out a shared part of several columns once for every row, before the
            # CASE picks its branch, and a text column that isn't JSON mustn't stop the query there
            written: exp.Expression = exp.Case(ifs=[exp.If(this=compound, true=exp.Cast(
                this=exp.TryCast(this=column.copy(), to=exp.DataType.build("JSON")),
                to=to_text_node.to.copy() if isinstance(to_text_node, exp.Cast) else exp.DataType.build("VARCHAR")))],
                default=to_text_node.copy())
            _keep_name(to_text_node)
            to_text_node.replace(written)


def _keep_name(node: exp.Expr) -> None:
    """Name the select-list item ``node`` is in as it reads now, when it has no name of its own, so a
    rewrite that writes a long CASE in its place doesn't give the column that CASE's text as its name. The
    same for each query it sits in, outward, so a scalar subquery in a select list keeps its name too."""
    from . import sql
    item = node
    while item.parent is not None:
        while isinstance(item.parent, exp.Expression) and not isinstance(item.parent, exp.Select):
            item = item.parent
        select = item.parent
        if select is None:
            return
        if not isinstance(item, exp.Alias) and any(e is item for e in select.expressions):
            named = exp.Alias(alias=exp.to_identifier(sql.duckdb_sql(item), quoted=True))
            item.replace(named)
            named.set("this", item)
        item = select


def _rewrite_semi_structured_types(statement: exp.Expression) -> None:
    """SFX-024: Snowflake's ARRAY type, with no element type, is a list of VARIANT values here (VARIANT[]),
    so an ARRAY column holds the lists ARRAY_CONSTRUCT makes and the array functions read it; a cast to it
    goes through VARIANT, which also reads a JSON array. OBJECT is JSON, as OBJECT_CONSTRUCT makes it."""
    for kind in list(statement.find_all(exp.DataType)):
        if kind.this == exp.DataType.Type.OBJECT and not kind.expressions:
            kind.replace(exp.DataType.build("JSON"))
        elif kind.this == exp.DataType.Type.ARRAY and not kind.expressions:
            cast = kind.parent
            if isinstance(cast, (exp.Cast, exp.TryCast)) and cast.args.get("to") is kind:
                cast.this.replace(exp.Cast(this=cast.this.copy(), to=exp.DataType.build("VARIANT")))
            kind.replace(exp.DataType.build("VARIANT[]", dialect="duckdb"))


def _variant_list_type(kind: exp.DataType) -> bool:
    """Whether ``kind`` is VARIANT[], the type SFX-024 gives Snowflake's ARRAY."""
    return (kind.this == exp.DataType.Type.ARRAY and len(kind.expressions) == 1
            and isinstance(kind.expressions[0], exp.DataType) and kind.expressions[0].this == exp.DataType.Type.VARIANT)


def _may_be_variant_list(value: object) -> bool:
    """Whether ``value`` may be or hold a list of VARIANT values: it reads a column or casts to VARIANT[], and
    isn't a map, JSON value or literal, which never is (so a nested OBJECT_CONSTRUCT isn't copied)."""
    if not isinstance(value, exp.Expression) or isinstance(value, (exp.Literal, exp.Map, exp.Null,
                                                                   exp.MapFromEntries, exp.ParseJSON)):
        return False
    if isinstance(value, exp.Anonymous) and value.name.upper() in ("MAP_FROM_ENTRIES", "TO_JSON", "JSON"):
        return False
    return any(value.find_all(exp.Column)) or any(_variant_list_type(d) for d in value.find_all(exp.DataType))


def _json_of_variant_lists(statement: exp.Expression) -> None:
    """SFX-024: DuckDB's TO_JSON writes each element of a list of VARIANT values (an ARRAY column) as text
    ("1"), so a value that may be such a list goes through VARIANT to JSON, which keeps each element's
    type. Run last, on the DuckDB tree: TO_JSON may come from TO_JSON itself or from another rewrite
    (OBJECT_CONSTRUCT). Only a value with a column or a VARIANT[] cast in it can be such a list. A VARIANT
    column itself goes the same way: DuckDB 1.x's TO_JSON writes an object it holds as quoted text."""
    # innermost first, so an outer TO_JSON copies an inner one already rewritten
    for node in reversed(list(statement.find_all(exp.JSONFormat, exp.Anonymous))):
        if isinstance(node, exp.Anonymous) and (node.name.upper() != "TO_JSON" or len(node.expressions) != 1):
            continue
        value = node.this if isinstance(node, exp.JSONFormat) else node.expressions[0]
        if not _may_be_variant_list(value):
            continue
        listed = exp.Case(ifs=[exp.If(
            this=exp.and_(exp.Like(this=exp.Anonymous(this="typeof", expressions=[value.copy()]),
                                   expression=exp.Literal.string("%VARIANT%")),
                          exp.Not(this=exp.Is(this=value.copy(), expression=exp.Null()))),
            true=exp.Cast(this=exp.Cast(this=value.copy(), to=exp.DataType.build("VARIANT")),
                          to=exp.DataType.build("JSON")))],
            default=exp.Anonymous(this="to_json", expressions=[value.copy()]))
        new = exp.Cast(this=listed, to=exp.DataType.build("VARCHAR")) if isinstance(node, exp.JSONFormat) else listed
        if node.meta.get(_SNOWFLAKE_TO_JSON):  # still Snowflake's TO_JSON, for SFX-070
            new.meta[_SNOWFLAKE_TO_JSON] = node.meta[_SNOWFLAKE_TO_JSON]
        node.replace(new)


_REGEXP_PARAMETERS = {exp.RegexpLike: "flag", exp.RegexpReplace: "modifiers", exp.RegexpExtract: "parameters",
                      exp.RegexpExtractAll: "parameters"}
_QUANTIFIER = re.compile(r"\{\d+(,\d*)?\}")


def _rewrite_regexp_patterns(statement: exp.Expression) -> None:
    """SFX-028 and SFN-031 (WR-257): Snowflake's regular expression parameters and patterns as Snowflake reads
    them. The m parameter (multi-line: ^ and $ at each line) and s (. matches a line break) become RE2's (?m) and
    (?s), as regex_flags explains; DuckDB's own m option changed only what . matches, and 'sm' turned s off.
    An empty pattern "matches nothing, not even an empty subject" in every function but REGEXP_LIKE and its
    aliases, where it is '^$'; RE2's matched at every position, so REGEXP_REPLACE('abc', '', 'x') gave 'xaxbxcx'.
    A pattern written in the query with a non-greedy quantifier ("the regular expression functions don't
    support non-greedy quantifiers"), a backreference ("Snowflake does not support backreferences in regular
    expression patterns") or a lookaround, which RE2 can't read, is refused by name (functions-regexp)."""
    from .regex_flags import NO_MATCH, case_flag_only_i, inline_flags
    for node in list(statement.find_all(exp.RegexpLike, exp.RegexpReplace, exp.RegexpExtract, exp.RegexpExtractAll,
                                        exp.RegexpCount, exp.RegexpInstr)):
        pattern = node.expression
        written = isinstance(pattern, exp.Literal) and pattern.is_string
        if written:
            found = _unsupported_regex(pattern.name)
            if found:
                what, step = found
                raise _Untranslatable(_refuse(
                    "SFN-031", "This regular expression",
                    f"The pattern '{pattern.name}' uses {what}, so nothing ran. {step}",
                    f"{_DOCS}/functions-regexp"))
        key = _REGEXP_PARAMETERS.get(type(node))
        if key is None:
            case_flag_only_i(node)  # REGEXP_COUNT and REGEXP_INSTR read '' and m, s, i as Snowflake does
            continue
        if not isinstance(node, exp.RegexpLike):
            if written and pattern.name == "":
                node.set("expression", exp.Literal.string(NO_MATCH))
            elif not written:
                node.set("expression", exp.func("REGEXP_REPLACE", pattern.copy(), exp.Literal.string("^$"),
                                                 exp.Literal.string(NO_MATCH)))
        inline_flags(node, key)


_REWRITE = "Rewrite the pattern without it."


def _unsupported_regex(pattern: str) -> tuple[str, str] | None:
    """What in ``pattern`` neither Snowflake nor RE2 reads, named for the user, with the step to take, or None."""
    i, n, in_class, class_start, quantified = 0, len(pattern), False, 0, False
    unclosed: set[str] = set()  # a [: (or [. [=) with no :] after it: later ones have none either, so no rescan
    while i < n:
        c = pattern[i]
        if c == "\\":
            d = pattern[i + 1:i + 2]
            if d and d in "123456789" and not in_class:
                return ((f"a backreference ('\\{d}'), which Snowflake's regular expressions don't support "
                        "(a replacement can use one)"), _REWRITE)
            i, quantified = i + 2, False
            continue
        if in_class:
            if c == "[" and pattern[i + 1:i + 2] in (":", ".", "=") and pattern[i + 1] not in unclosed:
                end = pattern.find(pattern[i + 1] + "]", i + 2)  # [:alpha:] inside a class
                if end > 0:
                    i = end + 2
                    continue
                unclosed.add(pattern[i + 1])
            if c == "]" and i > class_start:
                in_class = False
            i += 1
            continue
        if c == "[":
            in_class, quantified = True, False
            i += 2 if pattern.startswith("^", i + 1) else 1
            class_start = i  # a ] right here is a character of the class
            continue
        if pattern.startswith(("(?=", "(?!", "(?<=", "(?<!"), i):
            return (("a lookahead or lookbehind ('(?=', '(?!', '(?<=', '(?<!'), which RE2, the engine that runs it here, "
                    "can't read"), _REWRITE)
        if c == "?" and quantified:
            return ("a non-greedy quantifier ('*?', '+?', '??'), which Snowflake's regular expressions don't support",
                    "Use a narrower class, such as [^,]* in place of .*?.")
        if c == "{" and (quantifier := _QUANTIFIER.match(pattern, i)):
            i, quantified = quantifier.end(), True
            continue
        quantified = c in "*+?"
        i += 1
    return None


def _rewrite_regexp_groups(statement: exp.Expression) -> None:
    """SFX-021: REGEXP_SUBSTR and REGEXP_SUBSTR_ALL with the 'e' parameter and no group number give the
    first group, as Snowflake does; sqlglot writes group 0, the whole match. (sqlglot reads group 0 written
    in the query the same way, so it gives the first group too.) Only a pattern written in the query with
    at least one group is changed; any other pattern keeps the whole match, as before."""
    for node in list(statement.find_all(exp.RegexpExtract, exp.RegexpExtractAll)):
        params, group = node.args.get("parameters"), node.args.get("group")
        if isinstance(params, exp.Literal) and "e" in params.name.lower() and (
                group is None or (isinstance(group, exp.Literal) and group.name == "0")) \
                and _pattern_groups(node.expression) > 0:
            node.set("group", exp.Literal.number(1))


def _pattern_groups(pattern: exp.Expression | None) -> int:
    """The number of capture groups in a pattern written in the query as a string, else 0."""
    if not (isinstance(pattern, exp.Literal) and pattern.is_string):
        return 0
    try:
        with warnings.catch_warnings():  # POSIX classes such as [[:alpha:]] warn in Python's re
            warnings.simplefilter("ignore")
            return re.compile(pattern.name).groups
    except (re.error, OverflowError, RecursionError):  # silent-ok: a pattern Python can't read is left unchanged
        return 0


def _rewrite_string_epochs(statement: exp.Expression) -> None:
    """SFX-022: TO_TIMESTAMP of a string holding a whole number reads it by its size, as Snowflake does:
    seconds below 31536000000, then milliseconds, microseconds, and nanoseconds from 31536000000000000.
    Only a string written in the query is read this way here; a column is read as before."""
    for node in list(statement.find_all(exp.UnixToTime)):
        value = node.this
        if not (isinstance(value, exp.Literal) and value.is_string and re.fullmatch(r"-?\d+", value.name)):
            continue
        number = int(value.name)
        scale = next(s for limit, s in ((31536000000, 0), (31536000000000, 3), (31536000000000000, 6), (None, 9))
                     if limit is None or number < limit)
        node.set("this", exp.Literal.number(number))
        node.set("scale", exp.Literal.number(scale) if scale else None)


def _rewrite_timestamp_parts(statement: exp.Expression) -> None:
    """SFX-017: TIMESTAMP_FROM_PARTS and TIMESTAMP_NTZ_FROM_PARTS from numbers take a month, day, hour,
    minute or second outside its range and carry it over, as Snowflake does (seconds of -3600 is an hour
    earlier); DuckDB's MAKE_TIMESTAMP stops on them. TO_TIMESTAMP and TO_TIMESTAMP_NTZ of a number written
    in the query (40 * 365.25 * 86400) are seconds since 1970 with no time zone. TIMESTAMP_TZ_FROM_PARTS with
    no time zone argument is in the session's time zone."""
    for node in list(statement.find_all(exp.TimestampFromParts)):
        parts = [node.args.get(k) for k in ("year", "month", "day", "hour", "min", "sec")]
        if any(p is None for p in parts) or node.args.get("zone") is not None or node.args.get("milli") is not None:
            continue
        nano = node.args.get("nano")
        template = ("CAST(MAKE_DATE(fj_arg0, 1, 1) + INTERVAL (fj_arg1 - 1) MONTH + INTERVAL (fj_arg2 - 1) DAY AS TIMESTAMP)"
                    " + INTERVAL (fj_arg3 * 3600 + fj_arg4 * 60 + fj_arg5) SECOND")
        if nano is not None:
            template += " + INTERVAL (CAST(fj_arg6 AS BIGINT) // 1000) MICROSECOND"
            parts.append(nano)
        _put(node, template, [p for p in parts if p is not None])
    # TIMESTAMP_TZ_FROM_PARTS with no time zone is in the session's zone, as TIMESTAMP_LTZ_FROM_PARTS is;
    # sqlglot writes a plain MAKE_TIMESTAMP, which DuckDB reads as UTC
    for tz_parts in list(statement.find_all(exp.TimestampTzFromParts)):
        if tz_parts.args.get("zone") is None:
            ltz = exp.TimestampLtzFromParts(**{k: v for k, v in tz_parts.args.items() if v is not None})
            tz_parts.replace(ltz)
    for anonymous in list(statement.find_all(exp.Anonymous)):
        if str(anonymous.this).upper() in ("TO_TIMESTAMP", "TO_TIMESTAMP_NTZ") and len(anonymous.expressions) == 1:
            if _written_number(anonymous.expressions[0]):
                _put(anonymous, "CAST(TO_TIMESTAMP(fj_arg0) AT TIME ZONE 'UTC' AS TIMESTAMP)", [anonymous.expressions[0]])
            else:  # a column or expression, whose type is known only when the query runs
                _put(anonymous, _TIMESTAMP_OF_ANY, [anonymous.expressions[0]])


# TO_TIMESTAMP and TO_TIMESTAMP_NTZ of a value whose type isn't known here (a column), by its type when the query
# runs: a number is seconds since 1970 with no time zone (fractions to the microsecond, cut off); text holding a
# whole number is read by its size, as SFX-022 reads one written in the query; anything else (a date, other text)
# is cast to TIMESTAMP. DuckDB has no TO_TIMESTAMP_NTZ, so a column stopped with its catalog error. The value is
# read once; each branch binds for any type, and TYPEOF picks one when the query is planned.
_SF_NUMBER_TYPES = ("'TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'HUGEINT', 'UTINYINT', 'USMALLINT', 'UINTEGER', "
                    "'UBIGINT', 'UHUGEINT', 'FLOAT', 'DOUBLE'")
_SF_WHOLE_TEXT = r"^\s*-?[0-9]+\s*$"
_TIMESTAMP_OF_ANY = (
    "(LIST_TRANSFORM([fj_arg0], kf_ts -> CASE "
    f"WHEN TYPEOF(kf_ts) IN ({_SF_NUMBER_TYPES}) OR TYPEOF(kf_ts) LIKE 'DECIMAL%' "
    "THEN MAKE_TIMESTAMP(CAST(TRUNC(TRY_CAST(CAST(kf_ts AS VARCHAR) AS DECIMAL(38, 9)) * 1000000) AS BIGINT)) "
    f"WHEN TYPEOF(kf_ts) = 'VARCHAR' AND REGEXP_MATCHES(CAST(kf_ts AS VARCHAR), '{_SF_WHOLE_TEXT}') THEN "
    "(LIST_TRANSFORM([TRY_CAST(TRIM(CAST(kf_ts AS VARCHAR)) AS HUGEINT)], kf_n -> MAKE_TIMESTAMP(CAST(CASE "
    "WHEN kf_n < 31536000000 THEN kf_n * 1000000 WHEN kf_n < 31536000000000 THEN kf_n * 1000 "
    "WHEN kf_n < 31536000000000000 THEN kf_n ELSE kf_n // 1000 END AS BIGINT))))[1] "
    # Text with a UTC offset ('... 03:04:05 +02:00') is read as Snowflake's NTZ reads it: the offset is dropped.
    # DuckDB 1.5.6 stops on it ("not UTC") where 2.0 drops it, so it is dropped here for both.
    r"ELSE CAST(REGEXP_REPLACE(CAST(kf_ts AS VARCHAR), '(\d:\d\d(:\d\d(\.\d+)?)?)\s*(Z|[+-]\d\d(:?\d\d)?)$', "
    r"'\1') AS TIMESTAMP) END))[1]")


def _written_number(node: exp.Expression) -> bool:
    """Whether ``node`` is a number written in the query: number literals and arithmetic on them."""
    if isinstance(node, exp.Literal):
        return not node.is_string
    if isinstance(node, (exp.Paren, exp.Neg)):
        return _written_number(node.this)
    if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div)):
        return _written_number(node.this) and _written_number(node.expression)
    return False


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
    for getbit in list(statement.find_all(exp.Getbit)):
        put(getbit, f"CAST((CAST({sql(getbit.this)} AS HUGEINT) >> CAST({sql(getbit.expression)} AS INTEGER)) & 1 AS INTEGER)")
    for agg in list(statement.find_all(exp.BitwiseAndAgg, exp.BitwiseOrAgg, exp.BitwiseXorAgg)):
        if not isinstance(agg.this, exp.Distinct):  # a decimal is rounded to a whole number first, as in Snowflake
            agg.set("this", exp.Cast(this=agg.this, to=exp.DataType.build("HUGEINT", dialect="duckdb")))
    from .sql import _from_template
    for soundex in list(statement.find_all(exp.SoundexP123)):
        soundex.replace(_from_template(_soundex_p123_template(), [soundex.this]))
    # POSITION and CHARINDEX: "If the specified optional start_pos is beyond the end of the second argument (the
    # string to search), the function returns 0." DuckDB's search from past the end found an empty search text
    # there (POSITION('', 'a', 11) was 11). Binary values are left as they are.
    # Deepest first in both loops, so a call nested in another is rewritten before the outer one is copied.
    for found in reversed(list(statement.find_all(exp.StrPosition))):
        start = found.args.get("position")
        if start is None or _binary_valued(found.this):
            continue
        found.replace(_from_template("(CASE WHEN {1} > LENGTH({2}) THEN 0 ELSE {0} END)",
                                     [found.copy(), start, found.this]))
    # LPAD and RPAD: "If the pad argument is empty, no padding is inserted, but the result is still truncated to
    # length length_expr." DuckDB's stopped with "Insufficient padding". A pad written as non-empty text needs nothing.
    # A negative length gives '' (GREATEST), where DuckDB's LEFT would drop characters from the end.
    for pad in reversed(list(statement.find_all(exp.Pad))):
        fill = pad.args.get("fill_pattern")
        if fill is None or (isinstance(fill, exp.Literal) and fill.is_string and fill.name) or _binary_valued(pad.this):
            continue
        pad.replace(_from_template("(CASE WHEN {2} = '' THEN LEFT({0}, GREATEST({1}, 0)) ELSE {3} END)",
                                   [pad.this, pad.expression, fill, pad.copy()]))


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
# fraction or exponent is DOUBLE in DuckDB, where Snowflake has DECIMAL or DOUBLE: that is NUMBER (SFN-072).
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
    if "__sf_v_text" in template and isinstance(value, exp.ParseJSON):  # PARSE_JSON's text too, read once
        template = f"list_transform([__sf_text], lambda __sf_v_text: {template})[1]"
    tree = sqlglot.parse_one(f"list_transform([__sf_arg], lambda __sf_v: {template})[1]", read="duckdb")
    for column in list(tree.find_all(exp.Column)):
        if column.name == "__sf_arg" and not column.table:
            column.replace(value.copy())
        elif column.name == "__sf_text" and not column.table and isinstance(value, exp.ParseJSON):
            column.replace(value.this.copy())
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
        kind = _decimal_kind(value) if name in _DECIMAL_FAMILY else _KIND
        if name in _IS_KINDS:
            kinds = ", ".join(f"'{k}'" for k in _IS_KINDS[name])
            node.replace(_with_value(f"(({kind}) IN ({kinds}))", value))
            continue
        kinds_t, to = _AS_KINDS[name]
        if to == "DECIMAL":
            to = _decimal_type(name, args[1:])
        kinds = ", ".join(f"'{k}'" for k in kinds_t)
        read = "__sf_v" if to is None else f"CAST(CAST(__sf_v AS VARIANT) AS {to})"
        node.replace(_with_value(f"(CASE WHEN ({kind}) IN ({kinds}) THEN {read} END)", value))


def _decimal_kind(value: exp.Expression) -> str:
    """_KIND for IS_DECIMAL, AS_DECIMAL and AS_NUMBER, where a JSON number with a fraction or exponent (NUMBER)
    is DECIMAL or DOUBLE in Snowflake by how it was written: worked out from the text of a path into PARSE_JSON
    of text in the query, or of PARSE_JSON's own text when the value is PARSE_JSON(...); otherwise the query
    stops at such a value (SFN-072)."""
    known = _typeof_of_literal_path(value)
    if known is not _NOT_KNOWN:
        return f"CASE WHEN ({_KIND}) = 'NUMBER' THEN {_quoted(known) if known else 'NULL'} ELSE ({_KIND}) END"
    if isinstance(value, exp.ParseJSON):  # the text is PARSE_JSON's own argument, read once by the lambda
        return f"CASE WHEN ({_KIND}) = 'NUMBER' THEN {_written_number_kind('__sf_v_text')} ELSE ({_KIND}) END"
    return f"CASE WHEN ({_KIND}) = 'NUMBER' THEN {_stop_sql(_JSON_FRACTION)} ELSE ({_KIND}) END"
_TEXT_TYPES = {exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR, exp.DataType.Type.CHAR,
               exp.DataType.Type.NVARCHAR, exp.DataType.Type.NCHAR}


def _is_json(node: exp.Expression) -> bool:
    return node.type is not None and node.type.this == exp.DataType.Type.JSON


def _json_valued(node: exp.Expression) -> bool:
    """Is ``node`` DuckDB JSON whatever the data: a path (v:a, which becomes ->), PARSE_JSON, a column or
    bracket that _type_values found JSON, or [...] on one of those, maybe in parentheses?"""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, (exp.JSONExtract, exp.ParseJSON, exp.StripNullValue)) or _is_json(node):
        return True
    if isinstance(node, exp.GetExtract) and isinstance(node.expression, exp.Literal) and node.expression.is_string:
        return True  # GET(object, 'key') is JSON; GET(array, 0) is the list's element
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
# Types a cast gives whose name is one of _TYPEOF_NAMES (or DECIMAL), so TYPEOF of the cast is exact.
_TYPEOF_KNOWN_CASTS = {exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR, exp.DataType.Type.CHAR,
                       exp.DataType.Type.NCHAR, exp.DataType.Type.NVARCHAR, exp.DataType.Type.BOOLEAN,
                       exp.DataType.Type.DATE, exp.DataType.Type.TIME, exp.DataType.Type.TIMESTAMP,
                       exp.DataType.Type.TIMESTAMPNTZ, exp.DataType.Type.DATETIME, exp.DataType.Type.DOUBLE,
                       exp.DataType.Type.FLOAT, exp.DataType.Type.TINYINT, exp.DataType.Type.SMALLINT,
                       exp.DataType.Type.INT, exp.DataType.Type.BIGINT, exp.DataType.Type.DECIMAL,
                       exp.DataType.Type.BINARY, exp.DataType.Type.VARBINARY}


_ZONED_FUNCTIONS = {"TO_TIMESTAMP_TZ": "TIMESTAMP_TZ", "TRY_TO_TIMESTAMP_TZ": "TIMESTAMP_TZ",
                    "TO_TIMESTAMP_LTZ": "TIMESTAMP_LTZ", "TRY_TO_TIMESTAMP_LTZ": "TIMESTAMP_LTZ"}


def _zoned_type_name(value: exp.Expression) -> str | None:
    """SFX-009: TIMESTAMP_TZ or TIMESTAMP_LTZ for a value the query plainly makes as one (is_timestamp's
    example: TYPEOF of TO_TIMESTAMP_TZ is TIMESTAMP_TZ and of TO_TIMESTAMP_LTZ is TIMESTAMP_LTZ; CURRENT_TIMESTAMP
    returns TIMESTAMP_LTZ), else None."""
    if isinstance(value, exp.CurrentTimestamp):
        return "TIMESTAMP_LTZ"
    kind = value.to if isinstance(value, (exp.Cast, exp.TryCast)) else \
        value.args.get("target_type") if isinstance(value, exp.StrToTime) else None
    if isinstance(kind, exp.DataType) and kind.this == exp.DataType.Type.TIMESTAMPTZ:
        return "TIMESTAMP_TZ"
    if isinstance(kind, exp.DataType) and kind.this == exp.DataType.Type.TIMESTAMPLTZ:
        return "TIMESTAMP_LTZ"
    if isinstance(value, exp.Anonymous):
        return _ZONED_FUNCTIONS.get(value.name.upper())
    return None


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
    if isinstance(value, (exp.ParseJSON, exp.Literal, exp.Boolean, exp.Null)) or _zoned_type_name(value):
        return True
    return isinstance(value, (exp.Cast, exp.TryCast)) and value.to.this in _TYPEOF_KNOWN_CASTS


def _quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _written_number_kind(text_column: str) -> str:
    """DuckDB SQL naming a JSON number by how its text (``text_column``) was written, as Snowflake's PARSE_JSON
    reads it: with an exponent it's DOUBLE; otherwise INTEGER or DECIMAL while it fits NUMBER (38 digits, 37
    after the point), and DOUBLE when it doesn't."""
    text = f"trim(CAST({text_column} AS VARCHAR))"
    return (f"CASE WHEN regexp_matches({text}, '[eE]') THEN 'DOUBLE' "
            f"WHEN length(ltrim(regexp_extract({text}, '^-?([0-9]+)', 1), '0')) "
            f"+ length(regexp_extract({text}, '\\.([0-9]+)', 1)) > 38 "
            f"OR length(regexp_extract({text}, '\\.([0-9]+)', 1)) > 37 THEN 'DOUBLE' "
            f"WHEN contains({text}, '.') THEN 'DECIMAL' ELSE 'INTEGER' END")


def _rewrite_typeof(statement: exp.Expression) -> None:
    """SFX-009: TYPEOF gives Snowflake's names. A JSON value goes by json_type; a JSON number by how it was
    written, as PARSE_JSON reads it: with an exponent it's DOUBLE; otherwise INTEGER or DECIMAL while it
    fits NUMBER (38 digits, 37 after the point) and DOUBLE when it doesn't. DuckDB writes a parsed double
    again its own way (1.912e2 becomes 191.2), so only PARSE_JSON's own input text tells; any other
    JSON number with a fraction stops the query (SFN-072). Another type goes by DuckDB's typeof;
    one with no single Snowflake name keeps DuckDB's name."""
    for node in list(statement.find_all(exp.Typeof)):
        written = node.meta.pop(_WRITTEN_NAMES, False)  # not one a rewrite wrote, which reads DuckDB's name
        value = _typeof_value(node)
        known = _typeof_of_literal_path(value)
        if known is not _NOT_KNOWN:
            node.replace(exp.Literal.string(known) if known else exp.cast(exp.Null(), "VARCHAR"))
            continue
        zoned = _zoned_type_name(value)
        if zoned:
            # the name is the type's; only a NULL (TRY_TO_TIMESTAMP_TZ of bad text) is NULL. The value's offset
            # doesn't change its type, so SFX-071's check is left out here
            value = value.copy()
            for marked in value.find_all(exp.Cast, exp.TryCast):
                marked.meta.pop(_OFFSET_CHECK, None)
            _offset_unspaced(value)
            node.replace(exp.Case(ifs=[exp.If(this=exp.Is(this=value, expression=exp.Null()),
                                               true=exp.cast(exp.Null(), "VARCHAR"))],
                                  default=exp.Literal.string(zoned)))
            continue
        if isinstance(value, exp.ParseJSON):
            # Snowflake's rule on the text as written: DuckDB's JSON() writes doubles again.
            number = _written_number_kind("_kf_t")
        else:
            # Only a whole number keeps its text (DuckDB keeps big integers as written); one with a fraction
            # or an exponent stops the query (SFN-072).
            text = "trim(CAST(_kf_v AS VARCHAR))"
            number = (f"CASE WHEN NOT regexp_full_match({text}, '-?[0-9]+') THEN {_stop_sql(_JSON_FRACTION)} "
                      f"WHEN length(ltrim(ltrim({text}, '-'), '0')) > 38 THEN 'DOUBLE' ELSE 'INTEGER' END")
        # a timestamp with a time zone is TIMESTAMP_TZ or TIMESTAMP_LTZ in Snowflake: which isn't known (SFN-073)
        stop = _stop_sql(_ZONED_TYPEOF) if written else "typeof(_kf_v)"
        zoned_kind = _KIND.replace("'TIMESTAMP_TZ'", _stop_sql(_ZONED_TYPEOF) if written else "'TIMESTAMP_TZ'") \
            .replace("__sf_v", "_kf_v")
        json_case = " ".join(f"WHEN {_quoted(k)} THEN {_quoted(v)}" for k, v in _JSON_NAMES.items())
        type_case = " ".join(f"WHEN {_quoted(k)} THEN {_quoted(v)}" for k, v in _TYPEOF_NAMES.items())
        template = (f"SELECT CASE WHEN _kf_v IS NULL THEN NULL "
                    f"WHEN typeof(_kf_v) = 'JSON' THEN CASE json_type(TRY_CAST(_kf_v AS JSON)) {json_case} "
                    f"ELSE {number} END "
                    # a DuckDB VARIANT, such as an ARRAY column's element (SFX-024), by the value it holds
                    f"WHEN typeof(_kf_v) = 'VARIANT' THEN {zoned_kind} "
                    f"WHEN regexp_full_match(typeof(_kf_v), 'DECIMAL\\([0-9]+,0\\)') THEN 'INTEGER' "
                    f"WHEN typeof(_kf_v) LIKE 'DECIMAL(%' THEN 'DECIMAL' "
                    f"WHEN typeof(_kf_v) LIKE '%[]' THEN 'ARRAY' "
                    f"WHEN typeof(_kf_v) = 'TIMESTAMP WITH TIME ZONE' THEN {stop} "
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
            this=node.this.copy(), expression=exp.Null())
    if isinstance(cond, exp.Or):  # DIV0NULL: b = 0 OR b IS NULL
        return zero(cond.this) and cond.expression == exp.Is(this=b.copy(), expression=exp.Null())
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


_OFFSET_CHECK = "kf_sf_offset_check"   # a cast to TIMESTAMP_TZ the query wrote: SFX-071 checks its offset
_SNOWFLAKE_TO_JSON = "kf_sf_to_json"    # Snowflake's own TO_JSON, whose keys SFX-070 sorts


def _offset_checked(kind: exp.DataType) -> bool:
    """Whether a TIMESTAMP_TZ type in the query is one SFX-071 or SFN-071 deals with, so SFD-002 isn't raised
    for it: the type of a cast (TO_TIMESTAMP_TZ, TRY_TO_TIMESTAMP_TZ, ::TIMESTAMP_TZ), or of TO_TIMESTAMP_TZ
    with a format (which has no offset in it, or is refused)."""
    parent = kind.parent
    if isinstance(parent, (exp.Cast, exp.TryCast)) and parent.args.get("to") is kind:
        return True
    return isinstance(parent, exp.StrToTime) and parent.args.get("target_type") is kind


# ---------------------------------------------------------------- writes to a TIMESTAMP_TZ column (SFX-074)

_TZ_KIND, _LTZ_KIND, _ZONED_KIND, _OTHER_KIND = "TIMESTAMP_TZ", "TIMESTAMP_LTZ", "ZONED", "OTHER"


def _column_kind(kind: object) -> str:
    if isinstance(kind, exp.DataType) and kind.this == exp.DataType.Type.TIMESTAMPTZ:
        return _TZ_KIND
    if isinstance(kind, exp.DataType) and kind.this == exp.DataType.Type.TIMESTAMPLTZ:
        return _LTZ_KIND
    return _OTHER_KIND


def _table_key(table: object) -> tuple[str, str] | None:
    if not isinstance(table, exp.Table) or not table.name:
        return None
    return ((table.db or "main").lower(), table.name.lower())


def _track_tables(statement: exp.Expression, tables: dict[tuple[str, str], tuple[tuple[str, str], ...]]) -> None:
    """``tables`` as it is after ``statement`` runs: a CREATE TABLE with its columns written out adds the
    table (with each column's Snowflake type); one whose columns aren't written (AS, LIKE, CLONE), a DROP or a
    rename forgets it, and ALTER TABLE ... ADD COLUMN adds the column."""
    if isinstance(statement, exp.Create) and (statement.args.get("kind") or "").upper() == "TABLE":
        target = statement.this
        key = _table_key(target.this if isinstance(target, exp.Schema) else target)
        if key is None:
            return
        tables.pop(key, None)
        if isinstance(target, exp.Schema) and statement.expression is None and not statement.args.get("clone"):
            columns = [c for c in target.expressions if isinstance(c, exp.ColumnDef)]
            if len(columns) == len(target.expressions):
                tables[key] = tuple((c.name.lower(), _column_kind(c.args.get("kind"))) for c in columns)
    elif isinstance(statement, exp.Drop):
        key = _table_key(statement.this)
        if key is not None:
            tables.pop(key, None)
    elif isinstance(statement, exp.Alter) and (statement.args.get("kind") or "").upper() == "TABLE":
        key = _table_key(statement.this)
        if key is None or key not in tables:
            return
        for action in statement.args.get("actions") or []:
            if isinstance(action, exp.ColumnDef) and key in tables:
                tables[key] = (*tables[key], (action.name.lower(), _column_kind(action.args.get("kind"))))
            else:  # a rename, a dropped or changed column: no longer known here
                tables.pop(key, None)


def session_tables(con: Any, made: Tables | None = None) -> dict[tuple[str, str], tuple[tuple[str, str], ...]]:
    """SFX-074: the session's tables, from DuckDB's catalog (the session's database and its temporary
    tables, which hide one of the same name). A TIMESTAMPTZ column is TIMESTAMP_TZ or TIMESTAMP_LTZ as ``made``
    (what earlier Snowflake cells created) says, while the table still has those columns in that order;
    otherwise ZONED, as DuckDB keeps one type for both."""
    rows = con.execute(
        "SELECT database_name = 'temp', schema_name, table_name, column_name, data_type FROM duckdb_columns() "
        "WHERE database_name = current_database() OR database_name = 'temp' "
        "ORDER BY 1, schema_name, table_name, column_index").fetchall()
    by_database: tuple[dict[tuple[str, str], list[tuple[str, str]]], ...] = ({}, {})
    for temporary, schema, table, column, kind in rows:
        by_database[bool(temporary)].setdefault((str(schema).lower(), str(table).lower()), []).append(
            (str(column).lower(), _ZONED_KIND if str(kind) == "TIMESTAMP WITH TIME ZONE" else _OTHER_KIND))
    found = {**by_database[0], **by_database[1]}
    tables: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {}
    for key, columns in found.items():
        known = (made or {}).get(key)
        if known is not None and [c for c, _ in known] == [c for c, _ in columns] and all(
                (k in (_TZ_KIND, _LTZ_KIND)) == (z == _ZONED_KIND) for (_, k), (_, z) in zip(known, columns, strict=True)):
            tables[key] = tuple(known)
        else:
            tables[key] = tuple(columns)
    return tables


_WRITES = re.compile(r"\b(INSERT|UPDATE|MERGE)\b", re.IGNORECASE)


def may_write(code: str) -> bool:
    """Whether ``code`` may hold an INSERT, UPDATE or MERGE, so SFX-074 needs the session's tables."""
    return bool(_WRITES.search(code))


def tables_after(code: str, tables: Tables) -> dict[tuple[str, str], tuple[tuple[str, str], ...]]:
    """``tables`` as they are once ``code`` (Snowflake SQL that translate() read) has run."""
    known = dict(tables)
    for statement in parse(code):
        _track_tables(statement, known)
    return known


def _plain_text(value: exp.Expression) -> bool:
    """Text or NULL written in the query, or a value already cast to TIMESTAMP_TZ (which SFX-071 checks)."""
    if _scalar_kind(value) in ("text", "null"):
        return True
    return isinstance(value, (exp.Cast, exp.TryCast)) and value.to.this == exp.DataType.Type.TIMESTAMPTZ


def _zoned_value(value: exp.Expression, kind: str) -> bool:
    """A value written to a column of ``kind``: one written to a TIMESTAMP_TZ column becomes a cast to
    TIMESTAMP_TZ, which SFX-071 checks. True when SFD-002 still applies: a value that isn't text (its offset
    isn't known here), or any value but NULL written to a ZONED column."""
    if kind == _ZONED_KIND:
        return _scalar_kind(value) != "null"
    if kind == _LTZ_KIND and isinstance(value, exp.Literal) and value.is_string:
        _offset_unspaced(value)  # Snowflake reads the offset and keeps the instant, as DuckDB does
        return False
    if kind != _TZ_KIND or (isinstance(value, exp.Var) and value.name.upper() == "DEFAULT"):
        return False
    if isinstance(value, exp.Literal) and value.is_string and _SPACED_OFFSET_TEXT.search(value.name):
        raise _Untranslatable(_SPACED_OFFSET)
    plain = _plain_text(value)
    if not (isinstance(value, (exp.Cast, exp.TryCast)) and value.to.this == exp.DataType.Type.TIMESTAMPTZ):
        value.replace(exp.Cast(this=value.copy(), to=exp.DataType.build("TIMESTAMPTZ")))
    return not plain


def _written_columns(table: object, columns: list[exp.Expression] | None,
                     tables: Tables) -> list[str] | None:
    """The kinds of the columns a write names (all the table's, in order, when it names none), or None when
    the table isn't known."""
    key = _table_key(table)
    spec = tables.get(key) if key is not None else None
    if spec is None:
        return None
    if not columns:
        return [k for _, k in spec]
    kinds = dict(spec)
    return [kinds.get(c.name.lower(), _OTHER_KIND) for c in columns]


def _set_values(assignments: list[exp.Expression], table: object, tables: Tables) -> bool:
    kinds = _written_columns(table, None, tables)
    key = _table_key(table)
    if kinds is None or key is None:
        return False
    named = dict(tables[key])
    unknown = False
    for assignment in assignments:
        if isinstance(assignment, exp.EQ) and isinstance(assignment.this, exp.Column):
            unknown |= _zoned_value(assignment.expression, named.get(assignment.this.name.lower(), _OTHER_KIND))
    return unknown


def _query_values(query: exp.Expression, kinds: list[str]) -> bool:
    """The rows of INSERT ... SELECT, with each value for a TIMESTAMP_TZ column cast as _zoned_value does."""
    if isinstance(query, exp.Select) and len(query.expressions) == len(kinds) \
            and not any(isinstance(item, exp.Star) or (isinstance(item, exp.Column) and isinstance(item.this, exp.Star))
                        for item in query.expressions):
        unknown = False
        for item, kind in zip(list(query.expressions), kinds, strict=True):
            value = item.this if isinstance(item, exp.Alias) else item
            unknown |= _zoned_value(value, kind)
        return unknown
    if not any(k in (_TZ_KIND, _ZONED_KIND) for k in kinds):
        return False  # an LTZ column reads text as DuckDB does
    if _TZ_KIND in kinds:  # a star or a set operation: its columns by place, through a subquery
        names = [f"__sf_c{i}" for i in range(len(kinds))]
        rows = exp.Subquery(this=query.copy(), alias=exp.TableAlias(
            this=exp.to_identifier("__sf_rows"), columns=[exp.to_identifier(n) for n in names]))
        outer = exp.select(*[exp.column(n) for n in names]).from_(rows)
        query.replace(outer)
        _query_values(outer, kinds)
    return True


def _rewrite_zoned_writes(statement: exp.Expression, tables: Tables) -> bool:
    """SFX-074: Snowflake keeps the UTC offset of each value in a TIMESTAMP_TZ column, and DuckDB keeps only its
    instant. Each value INSERT, UPDATE or MERGE writes to a TIMESTAMP_TZ column that a Snowflake cell in this
    session made becomes a cast to TIMESTAMP_TZ, so SFX-071 checks it: text with no offset, or the session
    zone's own, is stored as Snowflake stores it, and text with another offset stops the query (SFN-071).
    True when SFD-002 still applies: a value that isn't text, or a TIMESTAMPTZ column whose Snowflake type
    isn't known here (one not made by a Snowflake cell in this session)."""
    if not tables:
        return False
    if isinstance(statement, exp.Insert):
        target = statement.this
        table = target.this if isinstance(target, exp.Schema) else target
        kinds = _written_columns(table, list(target.expressions) if isinstance(target, exp.Schema) else None, tables)
        source = statement.expression
        if kinds is None or source is None or all(k == _OTHER_KIND for k in kinds):
            return False
        if isinstance(source, exp.Values):
            unknown = False
            for row in source.expressions:
                values = list(row.expressions) if isinstance(row, exp.Tuple) else [row]
                for value, kind in zip(values, kinds, strict=False):  # a short row stops in DuckDB
                    unknown |= _zoned_value(value, kind)
            return unknown
        return _query_values(source.this if isinstance(source, exp.Subquery) and not source.alias else source,
                             kinds)
    if isinstance(statement, exp.Update):
        return _set_values(list(statement.expressions), statement.this, tables)
    if isinstance(statement, exp.Merge):
        unknown = False
        whens = statement.args.get("whens")
        for when in (whens.expressions if isinstance(whens, exp.Whens) else []):
            then = when.args.get("then")
            if isinstance(then, exp.Update):
                unknown |= _set_values(list(then.expressions), statement.this, tables)
            elif isinstance(then, exp.Insert) and isinstance(then.expression, exp.Tuple):
                columns = list(then.this.expressions) if isinstance(then.this, exp.Tuple) else None
                kinds = _written_columns(statement.this, columns, tables)
                for value, kind in zip(list(then.expression.expressions), kinds or [], strict=False):
                    unknown |= _zoned_value(value, kind)
        return unknown
    return False


def _mark_offsets_and_json(statement: exp.Expression) -> None:
    """SFX-070 and SFX-071, before the rewrites: Snowflake's TO_JSON calls and its casts to TIMESTAMP_TZ are
    marked (a copy keeps the mark), and TO_TIMESTAMP_TZ or TRY_TO_TIMESTAMP_TZ of a column (which sqlglot
    leaves as a function DuckDB doesn't have) becomes the cast it is of text."""
    for node in list(statement.find_all(exp.Anonymous)):
        name = node.name.upper()
        if name in ("TO_TIMESTAMP_TZ", "TRY_TO_TIMESTAMP_TZ") and len(node.expressions) == 1:
            kind = exp.TryCast if name.startswith("TRY_") else exp.Cast
            node.replace(kind(this=node.expressions[0].copy(), to=exp.DataType.build("TIMESTAMPTZ")))
    for found in statement.find_all(exp.Cast, exp.TryCast, exp.JSONFormat):
        if isinstance(found, exp.JSONFormat):
            found.meta[_SNOWFLAKE_TO_JSON] = found.sql(dialect="snowflake")  # the call as written, for its name
        elif isinstance(found, (exp.Cast, exp.TryCast)) and found.to.this == exp.DataType.Type.TIMESTAMPTZ:
            found.meta[_OFFSET_CHECK] = True


def _check_offsets(statement: exp.Expression) -> exp.Expression:
    """SFX-071, on the DuckDB tree: a marked cast of text to TIMESTAMP_TZ gives the instant DuckDB reads, which
    is Snowflake's value when the text has no offset, or has the session zone's own (reading it without its
    offset gives the same instant); text with another offset stops the query by name (SFN-071)."""
    for node in list(statement.find_all(exp.Cast, exp.TryCast)):
        if not node.meta.get(_OFFSET_CHECK):
            continue
        how = "TRY_CAST" if isinstance(node, exp.TryCast) else "CAST"
        template = (f"list_transform([__sf_arg], lambda __sf_tz: CASE WHEN typeof(__sf_tz) = 'VARCHAR' "
                    f"AND {how}(__sf_tz AS TIMESTAMPTZ) IS DISTINCT FROM "
                    f"CAST(TRY_CAST(__sf_tz AS TIMESTAMP) AS TIMESTAMPTZ) "
                    f"{'AND TRY_CAST(__sf_tz AS TIMESTAMPTZ) IS NOT NULL ' if how == 'TRY_CAST' else ''}"
                    f"THEN {_stop_sql(_TZ_OFFSET)} ELSE {how}(__sf_tz AS TIMESTAMPTZ) END)[1]")
        tree = cast(exp.Expression, sqlglot.parse_one(template, read="duckdb"))
        for column in list(tree.find_all(exp.Column)):
            if column.name == "__sf_arg" and not column.table:
                column.replace(node.this.copy())
        if node is statement:
            statement = tree
        else:
            node.replace(tree)
    return statement


# SFX-070: the JSON text with every object's keys sorted, from json_tree's nodes: each node's place is the list
# of its ancestors' keys (an object's) or positions (an array's), so a node comes after its parent, siblings in
# key order, and a container's closing bracket after its last child. The tree is MATERIALIZED: DuckDB 2.0
# otherwise reruns json_tree for each reference in the recursion and drops rows ({"b":1,"a":2} gave {"a":2}).
# A key given twice in one object stops the query, as Snowflake's PARSE_JSON does ("Duplicate field key"):
# the two would share a place, so the text would come out with no comma between them.
_SORTED_JSON = """(
  WITH RECURSIVE __sf_tree AS MATERIALIZED (SELECT * FROM json_tree(CAST(__sf_arg AS JSON))),
  __sf_walk(id, place) AS (
    SELECT id, [{'z': 0, 'k': CAST(NULL AS VARCHAR), 'i': CAST(NULL AS BIGINT)}] FROM __sf_tree WHERE parent IS NULL
    UNION ALL
    SELECT c.id, w.place || [{'z': 0, 'k': CASE WHEN p.type = 'OBJECT' THEN c.key END,
                              'i': CASE WHEN p.type = 'ARRAY' THEN CAST(c.key AS BIGINT) END}]
    FROM __sf_walk AS w JOIN __sf_tree AS c ON c.parent = w.id JOIN __sf_tree AS p ON p.id = w.id),
  __sf_tokens(place, token) AS (
    SELECT w.place, CASE WHEN c.parent IS NOT NULL AND w.place[-1] <> min(w.place[-1]) OVER (PARTITION BY c.parent)
                         THEN ',' ELSE '' END
                    || CASE WHEN p.type = 'OBJECT' THEN CAST(to_json(c.key) AS VARCHAR) || ':' ELSE '' END
                    || CASE c.type WHEN 'OBJECT' THEN '{' WHEN 'ARRAY' THEN '[' ELSE CAST(c.value AS VARCHAR) END
    FROM __sf_walk AS w JOIN __sf_tree AS c ON c.id = w.id LEFT JOIN __sf_tree AS p ON p.id = c.parent
    UNION ALL
    SELECT w.place || [{'z': 1, 'k': CAST(NULL AS VARCHAR), 'i': CAST(NULL AS BIGINT)}],
           CASE c.type WHEN 'OBJECT' THEN '}' ELSE ']' END
    FROM __sf_walk AS w JOIN __sf_tree AS c ON c.id = w.id WHERE c.type IN ('OBJECT', 'ARRAY'))
  SELECT coalesce((SELECT error(concat('Duplicate field key ', c.key)) FROM __sf_tree AS c
                    JOIN __sf_tree AS p ON p.id = c.parent WHERE p.type = 'OBJECT'
                    GROUP BY c.parent, c.key HAVING count(*) > 1 LIMIT 1),
                  string_agg(token, '' ORDER BY place)) FROM __sf_tokens)"""


def _sort_json_keys(statement: exp.Expression) -> exp.Expression:
    """SFX-070, last, on the DuckDB tree: Snowflake's TO_JSON writes an OBJECT's keys sorted at every level
    (TO_JSON(PARSE_JSON('{"b":1,"a":2}')) is {"a":2,"b":1}); DuckDB's keeps them as written."""
    for node in list(statement.find_all(exp.Expression)):
        if not node.meta.get(_SNOWFLAKE_TO_JSON):
            continue
        written = node.meta.pop(_SNOWFLAKE_TO_JSON)
        tree = cast(exp.Expression, sqlglot.parse_one(_SORTED_JSON, read="duckdb"))
        for column in list(tree.find_all(exp.Column)):
            if column.name == "__sf_arg" and not column.table:
                column.replace(node.copy())
        if node is statement:
            return tree
        parent = node.parent
        if isinstance(parent, exp.Select) and any(e is node for e in parent.expressions) and isinstance(written, str):
            tree = cast(exp.Expression, exp.alias_(tree, exp.to_identifier(written, quoted=True)))  # named as written, not by the query
        node.replace(tree)
    return statement


def _differences(statement: exp.Expression) -> list[Note]:
    notes = []
    if isinstance(statement, exp.Create) and statement.expression is not None \
            and (statement.args.get("kind") or "").upper() in ("TABLE", "VIEW") \
            and not (isinstance(statement.this, exp.Schema) and statement.this.expressions) \
            and not _output_names(statement.expression.copy()):
        notes.append(SFD_CASE)  # a column made by an expression with no alias or column list (SFX-072 names the rest)
    types = {d.this for d in statement.find_all(exp.DataType)}
    if any(d.this == exp.DataType.Type.TIMESTAMPTZ and not _offset_checked(d) for d in statement.find_all(exp.DataType)):
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
    if any(statement.find_all(exp.Skewness, exp.StddevPop)):
        notes.append(SFD_MOMENTS)
    return notes


# ---------------------------------------------------------------- the rule list for docs/engines.md

# Every rule and note id the code above uses, with what it covers. docs/engines.md holds docs_table()
# word for word (a test checks), so the page can't drift from the code.
CATALOG = (
    ("SFX-001", "Rewritten", ("LATERAL FLATTEN and TABLE(FLATTEN(...)) become DuckDB's json_each, with Snowflake's "
                "columns: KEY is NULL for an array element and INDEX for an object field, PATH is written as "
                "Snowflake writes it (b, b[0], c.d), THIS is the value flattened, OUTER gives one row of NULLs for a value "
                "with nothing to flatten, and PATH => "
                "takes plain names and [n]. A scalar has nothing to flatten (no row, or the OUTER row). A key that isn't a plain name is written in PATH as it is, since "
                "Snowflake's docs don't show how it writes one. SEQ, RECURSIVE and MODE aren't rewritten; "
                "SFD-005 is retired. The columns are named KEY, PATH, INDEX, VALUE and THIS (SFX-073).")),
    ("SFX-002", "Rewritten", ("TO_VARCHAR and TO_CHAR with a date or time format written in the query become "
                "strftime. Elements with no equivalent (quarter, week) aren't rewritten.")),
    ("SFX-003", "Runs only in Snowflake", "TO_VARCHAR and TO_CHAR with a number format."),
    ("SFX-004", "Rewritten", ("OBJECT_CONSTRUCT drops a pair whose value is SQL NULL and keeps JSON null, as "
                "Snowflake does. OBJECT_CONSTRUCT(*) isn't rewritten.")),
    ("SFX-005", "Rewritten", ("A VALUES list in FROM gets Snowflake's column names COLUMN1, COLUMN2..., unless "
                "its alias names the columns.")),
    ("SFX-006", "Rewritten", ("SOUNDEX becomes American Soundex in DuckDB SQL (the first letter and three digits). "
                "Text that starts with a non-letter comes back as it is, as in Spark; Snowflake doesn't document that "
                "case.")),
    ("SFX-007", "Rewritten", ("RATIO_TO_REPORT(x) OVER (PARTITION BY ...) becomes x divided by the partition's "
                "sum, null when x is null or the sum is 0. An ORDER BY in its window is dropped, as it doesn't "
                "change the result. x is worked out twice, which matters only when it isn't deterministic "
                "(RANDOM(), for example).")),
    ("SFX-008", "Rewritten", ("HAVERSINE becomes the great-circle distance in kilometres on a sphere of radius "
                "6,371 km.")),
    ("SFX-009", "Rewritten", ("TYPEOF gives Snowflake's type names: NULL_VALUE, BOOLEAN, VARCHAR, ARRAY and OBJECT "
                "for JSON; INTEGER, DECIMAL or DOUBLE for a JSON number by how PARSE_JSON reads it (an exponent "
                "makes it DOUBLE; otherwise INTEGER or DECIMAL while it fits NUMBER); INTEGER, DECIMAL, DOUBLE, "
                "VARCHAR, BOOLEAN, DATE, TIME, TIMESTAMP_NTZ, BINARY and ARRAY for other values; TIMESTAMP_TZ "
                "and TIMESTAMP_LTZ for TO_TIMESTAMP_TZ, TO_TIMESTAMP_LTZ (and TRY_), a cast to either, and "
                "CURRENT_TIMESTAMP (LTZ); SQL NULL for "
                "SQL NULL. A whole number is always INTEGER here, where Snowflake notes it may say DECIMAL. "
                "A JSON number with a fraction whose writing isn't known stops the query (SFN-072), and so does "
                "another timestamp with a time zone (SFN-073). A type with no Snowflake name is SFD-007. The argument "
                "is worked out more than once, which matters only when it isn't deterministic.")),
    ("SFX-010", "Rewritten", ("a / b stops with Snowflake's \"Division by zero\" error when b is 0, where DuckDB gives "
                "infinity or NULL. DIV0 and DIV0NULL still give 0. A divisor that isn't a column or a number is "
                "worked out twice, which matters only when it isn't deterministic.")),
    ("SFX-011", "Rewritten", ("IS_<type> and AS_<type> (INTEGER, DECIMAL, NUMBER, DOUBLE, REAL, BOOLEAN, CHAR, "
                "VARCHAR, ARRAY, OBJECT, BINARY, DATE, DATE_VALUE, TIME, TIMESTAMP_NTZ) test and read the type a "
                "VARIANT holds, as Snowflake names it: a PARSE_JSON value by its JSON type, a TO_VARIANT value by "
                "the type it was made from. AS_<type> gives NULL for any other type. A JSON integer too big for 64 "
                "bits is DOUBLE here.")),
    ("SFX-012", "Rewritten", ("A JSON value cast to text (v:a::string, v['a']::string, TO_VARCHAR(v:a), "
                "PARSE_JSON(...)::varchar, GET(o, 'key')::varchar, STRIP_NULL_VALUE(...)::varchar) gives a string without its JSON quotes, and JSON null gives SQL NULL, as "
                "in Snowflake: DuckDB's cast keeps the quotes and writes 'null'. A table's column whose type the "
                "statement doesn't show is cast as written.")),
    ("SFX-013", "Rewritten", ("[i] on a value read from PARSE_JSON stays 0-based, and [i] with an integer column on "
                "an ARRAY becomes 1-based, as DuckDB needs. Where the type can't be told from the statement (a "
                "table's column), [i] is taken for an ARRAY.")),
    ("SFX-014", "Rewritten", ("HLL becomes APPROX_COUNT_DISTINCT, DATASKETCHES_HLL the same estimate as a DOUBLE (its "
                "max_log_k is dropped), and APPROX_PERCENTILE returns a DOUBLE, as in Snowflake. HLL(*) and HLL of "
                "more than one value aren't rewritten. BITMAP_BUCKET_NUMBER and BITMAP_BIT_POSITION run as "
                "arithmetic on 32,768-bit buckets.")),
    ("SFX-015", "Rewritten", ("OCTET_LENGTH counts bytes (a column is taken for text; a BINARY column stops with "
                "DuckDB's error). MD5_NUMBER_LOWER64 and MD5_NUMBER_UPPER64 read MD5's last and first 64 bits. "
                "WIDTH_BUCKET gives 0 below min_value, num_buckets + 1 at or above max_value, and NULL when any "
                "argument is NULL; min_value at or above max_value stops with an error. TRY_BASE64_DECODE_STRING, TRY_BASE64_DECODE_BINARY, TRY_HEX_DECODE_STRING and "
                "TRY_HEX_DECODE_BINARY give NULL where the text doesn't decode; an alphabet argument isn't "
                "rewritten. SOUNDEX_P123 keeps the second letter's number (Pfister is P123). GETBIT reads a bit "
                "of a whole number (0 to 127). BITAND_AGG, BITOR_AGG and BITXOR_AGG round a decimal to a whole "
                "number first. Arguments are "
                "worked out more than once, which matters only when they aren't deterministic.")),
    ("SFX-016", "Rewritten", ("OBJECT_DELETE, OBJECT_PICK, OBJECT_INSERT, OBJECT_AGG, TO_OBJECT and ARRAYS_TO_OBJECT run "
                "on DuckDB JSON with Snowflake's NULL rules: a SQL NULL key or value is left out and JSON null is "
                "kept; an existing key in OBJECT_INSERT without updateFlag, a repeated key in OBJECT_AGG and "
                "arrays of unequal length stop with an error (OBJECT_AGG's text is DuckDB's \"Map keys must be unique\"). "
                "OBJECT_INSERT of a SQL NULL value leaves the object as it is, an existing key too, as the docs "
                "don't say otherwise; OBJECT_AGG of no rows is NULL. OBJECT_PICK with one argument takes it for an "
                "array only when it is written as one ([...], ARRAY_CONSTRUCT or PARSE_JSON); a column is a key. "
                "OBJECT_AGG(DISTINCT ...) isn't rewritten. Objects "
                "keep their keys in order, as Snowflake does, and an object constant ({'a': 1}) reads its keys "
                "as text. Arguments are worked out more than once, which matters only when they aren't "
                "deterministic.")),
    ("SFX-017", "Rewritten", ("TIME(x) is a cast to TIME, as TO_TIME(x) is: the time of a string or a timestamp. "
                "CURRENT_TIMESTAMP and a cast to TIMESTAMP_TZ or TIMESTAMP_LTZ go through TIMESTAMP, the "
                "local time; a TIMESTAMP_TZ column stops with DuckDB's cast error, since its type isn't known "
                "here. TIMESTAMP_FROM_PARTS and TIMESTAMP_NTZ_FROM_PARTS carry a part outside its range over "
                "(seconds of -3600 is an hour earlier). TO_TIMESTAMP and TO_TIMESTAMP_NTZ of a number are seconds "
                "since 1970, with no time zone; of a column, a number is read that way, text holding a whole "
                "number by its size, and anything else is cast to TIMESTAMP, with a UTC offset in text dropped. A TIMESTAMP_TZ "
                "column gives the session time zone's wall clock, since DuckDB doesn't keep a value's own offset, "
                "where Snowflake gives the value's own local time. TIMESTAMP_TZ_FROM_PARTS with no time zone "
                "argument is in the session's time zone.")),
    ("SFX-018", "Rewritten", ("CONDITIONAL_TRUE_EVENT and CONDITIONAL_CHANGE_EVENT count events from each "
                "partition's first row in ORDER BY order (a NULL is neither true nor a change), and "
                "INTERPOLATE_FFILL, INTERPOLATE_BFILL and INTERPOLATE_LINEAR fill a NULL from the rows before "
                "and after it. CONDITIONAL_CHANGE_EVENT, and CONDITIONAL_TRUE_EVENT of a LAG or LEAD, are worked "
                "out in a subquery over the SELECT's FROM and WHERE, so they are rewritten only in a SELECT from "
                "one table or subquery without JOIN, GROUP BY, HAVING or an aggregate. A window frame isn't "
                "allowed, as in Snowflake, and INTERPOLATE_LINEAR needs one ORDER BY expression: a number, DATE "
                "or timestamp.")),
    ("SFX-019", "Rewritten", ("ARRAY_DISTINCT keeps each value where it first appears, and one NULL, as "
                "Snowflake does. ARRAY_FLATTEN of an array with a NULL in place of an array is NULL. ARRAY_INSERT "
                "past the end fills the gap with NULLs, where Snowflake leaves empty elements.")),
    ("SFX-020", "Rewritten", "ALTER SESSION SET TIMEZONE = '...' becomes DuckDB's SET TimeZone, for the statements after it."),
    ("SFX-021", "Rewritten", ("REGEXP_SUBSTR and REGEXP_SUBSTR_ALL with the 'e' parameter and no group number, "
                "or group 0, give the first group, as in Snowflake, when the pattern is written in the query "
                "and has a group.")),
    ("SFX-022", "Rewritten", ("TO_TIMESTAMP of a string written in the query that holds a whole number reads it "
                "as seconds, milliseconds, microseconds or nanoseconds by its size, as Snowflake does. A column "
                "is read as before.")),
    ("SFX-023", "Rewritten", ("GET and the : path read a VARIANT made with TO_VARIANT or a cast to VARIANT as JSON, "
                "as Snowflake does.")),
    ("SFX-025", "Rewritten", ("MERGE with WHEN MATCHED ... UPDATE stops when a target row matches more than one "
                "source row, as Snowflake does by default (here also when those rows would give the same values, or a "
                "DELETE clause would take the row). PIVOT ... IN ('a', ...) names its columns 'a' with the quotes. "
                "$name reads the session variable, and IDENTIFIER($name) the name the same cell sets it to. "
                "ARRAY_SIZE of a JSON or VARIANT value is NULL when it isn't an array. A VARIANT column holding an object or "
                "array, cast to text, is JSON text. SAMPLE ROW and SAMPLE BLOCK are BERNOULLI and SYSTEM sampling.")),
    ("SFX-024", "Rewritten", ("An ARRAY column or cast becomes a list of VARIANT values, so it holds what "
                "ARRAY_CONSTRUCT makes and can read a JSON array. An OBJECT column or cast becomes JSON.")),
    ("SFX-026", "Rewritten", ("REGEXP_SUBSTR is NULL when nothing matches. CEIL and FLOOR with a written scale stay exact for a NUM"
                "BER. EDITDISTANCE with a negative maximum is 0. NEXT_DAY and PREVIOUS_DAY read the first two letters of the day, after any leading spaces. CONTAINS with NULL is NULL. TIME_SLICE counts slices from 1970-01-01 (weeks from Monday 1969-12-29). TO_DATE and a cast to DATE of a string written in the query read MM/DD/YYYY and DD-MON-YYYY, and TO_DATE of a string holding a whole number reads it as epoch seconds, milliseconds, microseconds or nanoseconds by its size.")),
    ("SFX-027", "Rewritten", ("CREATE TABLE makes a table as Snowflake does: PRIMARY KEY, UNIQUE and FOREIGN KEY "
                "aren't enforced, so they are dropped, and a PRIMARY KEY column keeps NOT NULL, which the "
                "constraint implies. AUTOINCREMENT and IDENTITY columns take their DEFAULT from a sequence that "
                "starts and steps as written (1 and 1 unless given); Snowflake's values are unique but not always "
                "one after another, so these are among those it could give. The sequence stays in the session when the "
                "table is replaced or dropped. TRANSIENT and CLUSTER BY are dropped, "
                "as they don't change what a query reads. In MERGE, UPDATE SET target.column = ... names the "
                "target's column. START TRANSACTION is BEGIN.")),
    ("SFX-028", "Rewritten", ("The regular expression parameters m (^ and $ match at each line) and s (. matches "
                "a line break) are read as Snowflake reads them, also together ('sm'), in REGEXP_LIKE, RLIKE, "
                "REGEXP_REPLACE, REGEXP_SUBSTR and REGEXP_SUBSTR_ALL. The c parameter is taken in REGEXP_COUNT and "
                "REGEXP_INSTR too. An empty pattern matches nothing, except in REGEXP_LIKE and its aliases, where it "
                "matches only an empty subject.")),
    ("SFX-070", "Rewritten", ("TO_JSON writes an object's keys in sorted order at every level, as Snowflake keeps "
                "an OBJECT's keys, and writes an object a VARIANT column holds as JSON (DuckDB 1.x's TO_JSON "
                "wrote it as quoted text). SFD-014 is retired.")),
    ("SFX-071", "Rewritten", ("TO_TIMESTAMP_TZ and TRY_TO_TIMESTAMP_TZ of a column run as a cast to TIMESTAMP_TZ. "
                "A cast to TIMESTAMP_TZ (and TO_TIMESTAMP_TZ, TRY_TO_TIMESTAMP_TZ) of text with no offset, or with "
                "the session zone's own offset at that time, gives Snowflake's value; text with another offset "
                "stops the query (SFN-071).")),
    ("SFX-072", "Rewritten", ("CREATE TABLE, CREATE TEMPORARY TABLE, CREATE VIEW, CREATE SCHEMA and ALTER TABLE "
                "store an unquoted name in upper case, as Snowflake does (MyCol becomes MYCOL): the object and the "
                "schema it is made in (mysch.tab), its columns, a column list (CREATE VIEW v (a, b) AS), a column "
                "or alias of CREATE ... AS and CREATE VIEW, an alias column list of VALUES (AS t(c1, c2), SFX-073), "
                "a renamed table or column, an added column. A quoted name keeps its case.")),
    ("SFX-073", "Rewritten", ("A query's result names a column, or an unquoted alias, in upper case, as Snowflake "
                "resolves an unquoted name (SELECT last_name gives LAST_NAME, AS area_of_circle gives AREA_OF_CIRCLE): in "
                "each SELECT, in * RENAME and * REPLACE, and in an alias column list (AS v(a, b), WITH c(a) AS). A "
                "quoted name keeps its case. A column made by an expression with no alias keeps DuckDB's name (SFD-001).")),
    ("SFX-074", "Rewritten", ("INSERT, UPDATE and MERGE check each value they write to a TIMESTAMP_TZ column that a "
                "Snowflake cell in this session made (this cell or an earlier one), as SFX-071 checks a cast: text "
                "with no offset, or the session zone's own, is stored as Snowflake stores it; text with another "
                "offset stops the query (SFN-071). Any other value, and a column whose Snowflake type isn't known "
                "here, is SFD-002. INSERT ... SELECT * reads the query's columns by place.")),
    ("SFX-000", "Runs only in Snowflake", "Anything else sqlglot can't write for DuckDB."),
    ("SFD-001", "Runs, with a difference: " + SFD_CASE.title, SFD_CASE.reason),
    ("SFD-002", "Runs, with a difference: " + SFD_TZ.title, SFD_TZ.reason),
    ("SFD-003", "Runs, with a difference: " + SFD_DIV.title, SFD_DIV.reason),
    ("SFD-004", "Runs, with a difference: " + SFD_LTZ.title, SFD_LTZ.reason),
    ("SFD-006", "Runs, with a difference: " + SFD_DATE.title, SFD_DATE.reason),
    ("SFD-007", "Runs, with a difference: " + SFD_TYPEOF.title, SFD_TYPEOF.reason),
    ("SFD-009", "Runs, with a difference: " + SFD_BRACKET.title, SFD_BRACKET.reason),
    ("SFD-010", "Runs, with a difference: " + SFD_APPROX.title, SFD_APPROX.reason),
    ("SFD-011", "Runs, with a difference: " + SFD_LINEAR.title, SFD_LINEAR.reason),
    ("SFD-013", "Runs, with a difference: " + SFD_MOMENTS.title, SFD_MOMENTS.reason),
    ("SFN-001", "Runs only in Snowflake", "Streams."),
    ("SFN-002", "Runs only in Snowflake", "Tasks."),
    ("SFN-003", "Runs only in Snowflake", "Dynamic tables (their SELECT can be tried on its own)."),
    ("SFN-004", "Runs only in Snowflake", "Snowflake Scripting, procedures, user-defined functions and CALL."),
    ("SFN-005", "Runs only in Snowflake", "EXECUTE IMMEDIATE."),
    ("SFN-006", "Runs only in Snowflake", "Time travel: AT and BEFORE."),
    ("SFN-007", "Runs only in Snowflake", "UNDROP and zero-copy CLONE."),
    ("SFN-008", "Runs only in Snowflake", "Stages: @stage references, COPY INTO, PUT, GET, LIST, REMOVE."),
    ("SFN-009", "Runs only in Snowflake", "USE, SHOW and DESCRIBE."),
    ("SFN-010", "Runs only in Snowflake", ("GRANT, REVOKE, and ALTER of Snowflake settings (policies, tags, "
                "suspend and resume).")),
    ("SFN-011", "Runs only in Snowflake", ("Snowflake-only objects: stages, pipes, warehouses, roles, users, shares, "
                "integrations, file formats, sequences, policies, tags.")),
    ("SFN-012", "Runs only in Snowflake", "Cortex AI functions."),
    ("SFN-013", "Runs only in Snowflake", ("TIMESTAMP_TZ text with an offset after a space; DuckDB reads it as a "
                "time zone name.")),
    ("SFN-014", "Runs only in Snowflake", ("Context functions about the Snowflake account, session or client: "
                "CURRENT_ACCOUNT, CURRENT_ACCOUNT_NAME, CURRENT_AVAILABLE_ROLES, CURRENT_CLIENT, CURRENT_IP_ADDRESS, "
                "CURRENT_ORGANIZATION_NAME, CURRENT_REGION, CURRENT_ROLE_TYPE, CURRENT_SECONDARY_ROLES, "
                "CURRENT_SESSION, CURRENT_STATEMENT, CURRENT_TRANSACTION, CURRENT_WAREHOUSE and LAST_QUERY_ID.")),
    ("SFN-015", "Runs only in Snowflake", ("IS_TIMESTAMP_LTZ, IS_TIMESTAMP_TZ, AS_TIMESTAMP_LTZ and AS_TIMESTAMP_TZ: "
                "DuckDB keeps one kind of timestamp with a time zone.")),
    ("SFN-016", "Runs only in Snowflake", ("Sketch and bitmap states in Snowflake's own format: HLL_ACCUMULATE, "
                "HLL_COMBINE, HLL_ESTIMATE, HLL_EXPORT, HLL_IMPORT, the DATASKETCHES_HLL_ACCUMULATE, _COMBINE and "
                "_ESTIMATE functions, APPROX_TOP_K and its _ACCUMULATE, _COMBINE and _ESTIMATE, the "
                "APPROX_PERCENTILE_ACCUMULATE, _COMBINE and _ESTIMATE functions, MINHASH, MINHASH_COMBINE, "
                "APPROXIMATE_SIMILARITY, APPROXIMATE_JACCARD_INDEX, BITMAP_CONSTRUCT_AGG, BITMAP_COUNT and "
                "BITMAP_OR_AGG.")),
    ("SFN-017", "Runs only in Snowflake", ("SEARCH and SEARCH_IP (Snowflake's search analyzers), COLLATE (Snowflake "
                "collation specifications), HASH and HASH_AGG (Snowflake's own hash), and XML: CHECK_XML, PARSE_XML, "
                "XMLGET, TO_XML.")),
    ("SFN-018", "Runs only in Snowflake", ("DECFLOAT, TO_DECFLOAT and TRY_TO_DECFLOAT: DuckDB has no decimal "
                "floating-point type.")),
    ("SFN-019", "Runs only in Snowflake", "MATCH_RECOGNIZE: DuckDB has no row pattern matching."),
    ("SFN-020", "Runs only in Snowflake", ("INSERT OVERWRITE: DuckDB has none, and a DELETE then INSERT wouldn't be "
                "one statement.")),
    ("SFN-021", "Runs only in Snowflake", ("UNIFORM with a generator other than RANDOM() (a number, a column or "
                "a seeded RANDOM): Snowflake's series from it can't be reproduced here.")),
    ("SFN-022", "Runs only in Snowflake", ("ALTER SESSION, other than SET TIMEZONE: other session parameters "
                "and UNSET change how Snowflake itself reads or prints values.")),
    ("SFN-023", "Runs only in Snowflake", ("IDENTIFIER($name) where the same cell doesn't set the variable to a "
                "string first: its value isn't known here.")),
    ("SFN-024", "Runs only in Snowflake", "PIVOT ... IN (ANY ...) or IN (subquery): DuckDB's PIVOT takes a list of values."),
    ("SFN-025", "Runs only in Snowflake", ("An array written with both text and numbers in it (ARRAY_CONSTRUCT, "
                "ARRAY_CONSTRUCT_COMPACT, ARRAY_INSERT, ARRAY_PREPEND, ARRAY_APPEND): a DuckDB list holds one type.")),
    ("SFN-026", "Runs only in Snowflake", ("INSERT ALL and INSERT FIRST: DuckDB has no multi-table INSERT, and "
                "separate INSERTs wouldn't be one statement.")),
    ("SFN-027", "Runs only in Snowflake", ("CREATE TABLE ... LIKE: Snowflake copies the other table's defaults and "
                "constraints, which the rewrite can't see.")),
    ("SFN-028", "Runs only in Snowflake", "TRUNCATE TABLE IF EXISTS: DuckDB's TRUNCATE has no IF EXISTS."),
    ("SFN-029", "Runs only in Snowflake", ("ALTER TABLE with more than one column, and ALTER TABLE ... SWAP WITH: "
                "DuckDB's ALTER TABLE changes one column at a time and has no SWAP.")),
    ("SFN-030", "Runs only in Snowflake", ("CREATE, ALTER or DROP after BEGIN in the same cell: Snowflake commits "
                "the open transaction first, and DuckDB doesn't.")),
    ("SFN-031", "Runs only in Snowflake", ("A regular expression written with a non-greedy quantifier ('*?', '+?', "
                "'??') or a backreference ('\\1') in the pattern, which Snowflake doesn't support, or with a "
                "lookahead or lookbehind, which RE2 can't read.")),
    ("SFN-070", "Runs only in Snowflake", ("JAROWINKLER_SIMILARITY: Snowflake doesn't document its score, and "
                "DuckDB's differs from it for pairs in Snowflake's own example. SFD-012 is retired.")),
    ("SFN-071", "Runs only in Snowflake", ("TIMESTAMP_TZ from text with a UTC offset other than the session zone's "
                "(the query stops at that row; also text written to a TIMESTAMP_TZ column, SFX-074), and TO_TIMESTAMP_TZ with TZH or TZM in its format, because DuckDB keeps the "
                "instant, not the offset.")),
    ("SFN-072", "Runs only in Snowflake", ("TYPEOF, IS_DECIMAL, AS_DECIMAL and AS_NUMBER of a JSON number with a "
                "fraction or exponent read from a column or a path (the query stops at that row), because Snowflake's answer "
                "depends on how the number was written, which DuckDB doesn't keep. Of PARSE_JSON(text) itself, of a "
                "path into PARSE_JSON of text in the query, or of a TO_VARIANT value, they run. SFD-008 is retired.")),
    ("SFN-073", "Runs only in Snowflake", ("TYPEOF of a timestamp with a time zone from a column or a computed value "
                "(the query stops at that row), because Snowflake names TIMESTAMP_TZ and TIMESTAMP_LTZ apart, and DuckDB keeps "
                "one type for both. TYPEOF of TO_TIMESTAMP_TZ, TO_TIMESTAMP_LTZ, a cast to either, or "
                "CURRENT_TIMESTAMP runs (SFX-009).")),
    ("SFN-099", "Runs only in Snowflake", "Any other Snowflake command sqlglot can't read as a statement."),
)


def docs_table() -> str:
    """The rule list as the Markdown table in docs/engines.md."""
    lines = [f"Rules {RULES_VERSION}.", "", "| Id | What | Applies to |", "| --- | --- | --- |"]
    lines += [f"| {id} | {what} | {text} |" for id, what, text in CATALOG]
    return "\n".join(lines)


# ---------------------------------------------------------------- translate


def _session_time_zone(statement: exp.Expression) -> str | None:
    """SFX-020: the zone of ALTER SESSION SET TIMEZONE = 'zone', which becomes DuckDB's SET TimeZone."""
    if not (isinstance(statement, exp.Alter) and (statement.args.get("kind") or "").upper() == "SESSION"):
        return None
    actions = statement.args.get("actions") or []
    items = [i for a in actions if isinstance(a, exp.AlterSession) and not a.args.get("unset")
             for i in a.expressions]
    if len(actions) == 1 and len(items) == 1 and isinstance(items[0], exp.SetItem) \
            and isinstance(items[0].this, exp.EQ) and items[0].this.this.name.upper() == "TIMEZONE" \
            and isinstance(items[0].this.expression, exp.Literal) and items[0].this.expression.is_string:
        return str(items[0].this.expression.name)
    return None


def _statement(n: int, statement: exp.Expression, tables: Tables | None = None) -> StatementReport:
    refusal = _not_translatable(statement)
    if refusal:
        return StatementReport(n, "not_translatable", None, (refusal,))
    zone = _session_time_zone(statement)
    if zone is not None:  # SFX-020
        return StatementReport(n, "translated", f"SET TimeZone = {exp.Literal.string(zone).sql('duckdb')}", ())
    tree = statement.copy()
    notes = _differences(tree)
    brackets = list(tree.find_all(exp.Bracket))  # held, so no new node can reuse one of these ids
    written = {id(b) for b in brackets}
    try:
        _mark_result_names(tree)
        if _rewrite_zoned_writes(tree, tables or {}) and SFD_TZ not in notes:
            notes.append(SFD_TZ)
        _mark_offsets_and_json(tree)
        _variant_text(tree)
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
        _rewrite_regexp_patterns(tree)
        _rewrite_documented_edges(tree)
        _rewrite_strings(tree)
        _rewrite_time(tree)
        _rewrite_timestamp_parts(tree)
        _rewrite_arrays(tree)
        _rewrite_variant_reads(tree)
        _rewrite_statements(tree)
        _merge_set_names(tree)
        before = _rewrite_tables(tree)
        _rewrite_names(tree)
        _rewrite_semi_structured_types(tree)
        _rewrite_regexp_groups(tree)
        _rewrite_string_epochs(tree)
        _rewrite_objects(tree)
        _rewrite_typeof(tree)
        _rewrite_variant_types(tree)
        unknown_brackets = _type_values(tree, written)
        _rewrite_json_text(tree)
        _rewrite_division(tree)
        _json_of_variant_lists(tree)
        _rewrite_result_names(tree)
        tree = _sort_json_keys(_check_offsets(tree))
        sql = ";\n".join([_duck(b) for b in before] + [_duck(tree)])
    except _Untranslatable as exc:
        return StatementReport(n, "not_translatable", None, (exc.note,))
    except SqlglotError as exc:  # UnsupportedError, or sqlglot stopping while it writes (TIME() with no argument)
        first = (str(exc).splitlines() or ["sqlglot can't rewrite it"])[0]
        return StatementReport(n, "not_translatable", None,
                               (_refuse("SFX-000", "Not rewritten for DuckDB", first + "."),))
    if unknown_brackets:
        notes.append(SFD_BRACKET)
    return StatementReport(n, "difference" if notes else "translated", sql, tuple(notes))


def translate(code: str, tables: Tables | None = None) -> Translation:
    """Every statement of ``code`` rewritten for DuckDB, with its verdict. Nothing runs here. ``tables`` is
    what the session holds (session_tables), for SFX-074; a table this cell creates is known after it.
    ValueError when the SQL can't be read at all."""
    reports, open_transaction = [], False
    known = dict(tables or {})
    for i, statement in enumerate(_with_identifiers(parse(code)), 1):
        statement = _start_transaction(statement)
        if open_transaction and isinstance(statement, (exp.Create, exp.Drop, exp.Alter)) \
                and _session_time_zone(statement) is None:
            reports.append(StatementReport(i, "not_translatable", None, (_DDL_IN_TRANSACTION,)))
            continue
        if isinstance(statement, exp.Transaction):
            open_transaction = True
        elif isinstance(statement, (exp.Commit, exp.Rollback)):
            open_transaction = False
        reports.append(_statement(i, statement, known))
        _track_tables(statement, known)
    return Translation(tuple(reports), tables=tuple(known.items()))


_DDL_IN_TRANSACTION = _refuse(
    "SFN-030", "CREATE, ALTER or DROP inside a transaction", "Snowflake commits the open transaction before "
    "a CREATE, ALTER or DROP and runs it as its own transaction, where DuckDB keeps it inside the open one, so a "
    "later ROLLBACK would undo different work here. Put COMMIT before the statement.", f"{_DOCS}/transactions")


def _start_transaction(statement: exp.Expression) -> exp.Expression:
    """SFX-027: START TRANSACTION, Snowflake's synonym for BEGIN, which sqlglot reads as a column named START."""
    if isinstance(statement, exp.Alias) and isinstance(statement.this, exp.Column) \
            and not statement.this.table and statement.this.name.upper() == "START" \
            and statement.alias.upper() == "TRANSACTION":
        return exp.Transaction()
    return statement


def _with_identifiers(statements: list[exp.Expression]) -> list[exp.Expression]:
    """SFX-025: IDENTIFIER($name) as the name a SET earlier in the same cell gives the variable (a string);
    any other stays, and is refused (SFN-023)."""
    names: dict[str, str] = {}
    for statement in statements:
        for dynamic in list(statement.find_all(exp.DynamicIdentifier)):
            parameter = dynamic.this
            if isinstance(parameter, exp.Parameter) and isinstance(parameter.this, exp.Var) \
                    and parameter.this.name.upper() in names:
                value = names[parameter.this.name.upper()]
                parts = value.split(".")
                if all(_FLATTEN_PATH.fullmatch(part) for part in parts):  # a plain (dotted) name only
                    named = exp.to_table(value, dialect="snowflake") if isinstance(dynamic.parent, exp.Table) \
                        else exp.to_column(value, dialect="snowflake")
                    target = dynamic.parent if isinstance(dynamic.parent, exp.Table) else dynamic
                    if isinstance(target, exp.Table) and target.alias:
                        named.set("alias", target.args.get("alias"))
                    target.replace(named)
        if isinstance(statement, exp.Set):
            for item in statement.expressions:
                assignment = item.this if isinstance(item, exp.SetItem) else None
                if isinstance(assignment, exp.EQ) and isinstance(assignment.this, exp.Column):
                    key = assignment.this.name.upper()
                    if isinstance(assignment.expression, exp.Literal) and assignment.expression.is_string:
                        names[key] = assignment.expression.name
                    else:
                        names.pop(key, None)
    return statements
