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

import re
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, UnsupportedError

RULES_VERSION = "2026.10.6"
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
             "DuckDB returns DOUBLE for /, where Snowflake returns NUMBER with its own scale rules. Dividing by "
             "zero gives infinity or NULL here, where Snowflake stops with an error.",
             "Round or cast the result where scale matters; use DIV0 where a zero divisor is expected.",
             f"{_DOCS}/operators-arithmetic")
SFD_LTZ = _n("SFD-004", "Local time zone is this PC's",
             "TIMESTAMP_LTZ and CURRENT_TIMESTAMP use this PC's time zone here, not Snowflake's TIMEZONE parameter.",
             "Set the same time zone, or compare in UTC.", f"{_DOCS}/parameters#timezone")
SFD_FLATTEN = _n("SFD-005", "FLATTEN rows and keys",
                 "FLATTEN runs as DuckDB's json_each: rows come back in no set order, KEY is the position for "
                 "array elements (Snowflake gives NULL) and PATH is written differently.",
                 "Add ORDER BY INDEX where order matters.", f"{_DOCS}/functions/flatten")

SFD_DATE = _n("SFD-006", "Date arithmetic on a DATE returns a TIMESTAMP",
              "DATEADD and TIMESTAMPADD by a date unit (day and longer), and DATE_TRUNC, of a value whose type "
              "isn't known here keep a DATE as a DATE in Snowflake; here the result is a TIMESTAMP at "
              "midnight. The date is the same. Raised when the value may be a DATE (a column's type isn't "
              "known here); a value that is plainly a DATE (a cast, TO_DATE, CURRENT_DATE) is cast back to "
              "DATE instead, and a TIMESTAMP cast or function raises nothing.",
              "Cast the result to DATE where its type matters, such as a join or a written table.",
              f"{_DOCS}/functions/date_trunc")
SFD_TYPEOF = _n("SFD-007", "TYPEOF can't always name Snowflake's type",
                "TYPEOF gives Snowflake's type names here (SFX-009), with two exceptions. A JSON number with a "
                "fraction or an exponent that wasn't parsed in the same call (a column, a path such as v:a) "
                "comes back as 'DECIMAL or DOUBLE': Snowflake tells them apart by how the number was written "
                "(1.912e2 is DOUBLE, 191.2 is DECIMAL) and DuckDB doesn't keep that. A type with no single "
                "Snowflake name keeps DuckDB's name (TIMESTAMP WITH TIME ZONE, which is TIMESTAMP_TZ or "
                "TIMESTAMP_LTZ in Snowflake; INTERVAL, UUID, STRUCT, MAP). Raised when the value's type isn't "
                "known here: a column, a path or a computed value.",
                "Don't branch on TYPEOF's text for those values in code meant for both; TYPEOF(PARSE_JSON(...)) "
                "and TYPEOF of a cast are named as in Snowflake.", f"{_DOCS}/functions/typeof")

_ONLY_IN_SNOWFLAKE = "Runs only in Snowflake"


def _refuse(id: str, title: str, reason: str, source: str = "") -> Note:
    return _n(id, title, reason, _ONLY_IN_SNOWFLAKE + ".", source)


# ---------------------------------------------------------------- reading


def parse(code: str) -> list[exp.Expression]:
    """Snowflake SQL statements as sqlglot reads them; ValueError when it can't be read."""
    try:
        statements = [s for s in sqlglot.parse(code, read="snowflake") if s is not None]
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


def _flatten_parts(node: exp.Expression) -> tuple[exp.Expression, bool] | None:
    """The input and OUTER of a FLATTEN call sqlglot read as Explode, or None for another table."""
    explode = node.this if isinstance(node, (exp.Lateral, exp.TableFromRows)) else None
    if not isinstance(explode, exp.Explode):
        return None
    source, outer = None, bool(node.args.get("outer"))
    for arg in [explode.this, *explode.expressions]:
        if isinstance(arg, exp.Kwarg):
            key = arg.this.name.lower()
            if key == "input":
                source = arg.expression
            elif key == "outer":
                outer = isinstance(arg.expression, exp.Boolean) and bool(arg.expression.this)
            else:
                raise _Untranslatable(_refuse("SFX-001", f"FLATTEN with {key.upper()}",
                                              f"FLATTEN's {key.upper()} argument has no DuckDB equivalent.",
                                              f"{_DOCS}/functions/flatten"))
        elif source is None:
            source = arg
    return (source, outer) if source is not None else None


def _rewrite_flatten(statement: exp.Expression) -> bool:
    """SFX-001: LATERAL FLATTEN and TABLE(FLATTEN(...)) become DuckDB's json_each. True when it did."""
    did = False
    for select in list(statement.find_all(exp.Select)):
        joins = select.args.get("joins") or []
        aliases: list[str] = []
        for i, join in enumerate(joins):
            parts = _flatten_parts(join.this)
            if parts is None:
                continue
            source, outer = parts
            alias_node = join.this.args.get("alias")
            alias = alias_node.name if alias_node is not None and alias_node.name else "flatten"
            _duck(source)  # refused here when DuckDB can't take the input
            how = f"LEFT JOIN json_each(_kf_src) AS {alias} ON TRUE" if outer else f"CROSS JOIN json_each(_kf_src) AS {alias}"
            joins[i] = sqlglot.parse_one(f"SELECT 1 FROM _x {how}", read="duckdb").args["joins"][0]
            # The input goes in as a tree, not as text read again: sqlglot 30.25 reads DuckDB's
            # "v -> '$.arr'" back as a lambda (#1252).
            next(c for c in joins[i].find_all(exp.Column) if c.name == "_kf_src").replace(source.copy())
            aliases.append(alias.lower())
            did = True
        if not aliases:
            continue
        for col in list(select.find_all(exp.Column)):
            table = col.table.lower()
            if table and table not in aliases:
                continue
            name = col.name.lower()
            if not table and name not in ("index", "seq", "this", "path"):
                continue
            if name == "index":
                col.replace(exp.cast(exp.column("key", table=col.table or None), exp.DataType.Type.INT))
            elif name in ("seq", "this"):
                raise _Untranslatable(_refuse("SFX-001", f"FLATTEN's {name.upper()} column",
                                              f"json_each has no {name.upper()} column.",
                                              f"{_DOCS}/functions/flatten"))
            elif name == "path":
                col.replace(exp.column("fullkey", table=col.table or None))
    return did


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
    for node in list(statement.find_all(exp.TimeToStr)):
        fmt = node.args.get("format")
        if any(node is mine for mine in ours):
            continue
        if isinstance(fmt, exp.Literal) and re.search(r"(?<!%)[A-Za-z]", re.sub(r"%[A-Za-z%]", "", fmt.name)):
            raise _Untranslatable(_refuse("SFX-002", "Date or time format",
                                          "Part of the format has no DuckDB equivalent.",
                                          f"{_DOCS}/functions-conversion#date-and-time-formats-in-conversion-functions"))


def _rewrite_object_construct(statement: exp.Expression) -> None:
    """SFX-004: OBJECT_CONSTRUCT drops pairs whose value is SQL NULL and keeps JSON nulls."""
    for node in list(statement.find_all(exp.StarMap)):
        raise _Untranslatable(_refuse("SFX-004", "OBJECT_CONSTRUCT(*)", "Building an object from every column "
                                      "isn't rewritten.", f"{_DOCS}/functions/object_construct"))
    for node in list(statement.find_all(exp.Struct)):
        pairs = node.expressions
        if not pairs:
            node.replace(sqlglot.parse_one("'{}'::JSON", read="duckdb"))
            continue
        if not all(isinstance(p, exp.PropertyEQ) for p in pairs):
            continue
        entries = ", ".join(f"{{'k': {_duck(p.this)}, 'v': to_json({_duck(p.expression)})}}" for p in pairs)
        node.replace(sqlglot.parse_one(
            f"to_json(map_from_entries(list_filter([{entries}], x -> x.v IS NOT NULL)))", read="duckdb"))


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


def _typeof_known(node: exp.Typeof) -> bool:
    """True when SFX-009 names the argument's type exactly whatever its value: PARSE_JSON in the same
    call, a literal, or a cast to a type Snowflake names one way."""
    value = _typeof_value(node)
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
                    f"WHEN typeof(_kf_v) = 'JSON' THEN CASE json_type(CAST(_kf_v AS JSON)) {json_case} "
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
    return notes


# ---------------------------------------------------------------- the rule list for docs/engines.md

# Every rule and note id the code above uses, with what it covers. docs/engines.md holds docs_table()
# word for word (a test checks), so the page can't drift from the code.
CATALOG = (
    ("SFX-001", "Rewritten", "LATERAL FLATTEN and TABLE(FLATTEN(...)) become DuckDB's json_each (INDEX, VALUE, KEY, "
                "PATH; OUTER as a left join). SEQ, THIS, RECURSIVE and MODE aren't rewritten."),
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
    ("SFX-000", "Runs only in Snowflake", "Anything else sqlglot can't write for DuckDB."),
    ("SFD-001", "Runs, with a difference: " + SFD_CASE.title, SFD_CASE.reason),
    ("SFD-002", "Runs, with a difference: " + SFD_TZ.title, SFD_TZ.reason),
    ("SFD-003", "Runs, with a difference: " + SFD_DIV.title, SFD_DIV.reason),
    ("SFD-004", "Runs, with a difference: " + SFD_LTZ.title, SFD_LTZ.reason),
    ("SFD-005", "Runs, with a difference: " + SFD_FLATTEN.title, SFD_FLATTEN.reason),
    ("SFD-006", "Runs, with a difference: " + SFD_DATE.title, SFD_DATE.reason),
    ("SFD-007", "Runs, with a difference: " + SFD_TYPEOF.title, SFD_TYPEOF.reason),
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
    try:
        flattened = _rewrite_flatten(tree)
        _rewrite_to_char(tree)
        _rewrite_date_trunc(tree)
        _rewrite_object_construct(tree)
        _rewrite_values(tree)
        _rewrite_soundex(tree)
        _rewrite_ratio_to_report(tree)
        _rewrite_haversine(tree)
        _rewrite_typeof(tree)
        sql = _duck(tree)
    except _Untranslatable as exc:
        return StatementReport(n, "not_translatable", None, (exc.note,))
    except UnsupportedError as exc:
        first = (str(exc).splitlines() or ["sqlglot can't rewrite it"])[0]
        return StatementReport(n, "not_translatable", None,
                               (_refuse("SFX-000", "Not rewritten for DuckDB", first + "."),))
    if flattened:
        notes.append(SFD_FLATTEN)
    return StatementReport(n, "difference" if notes else "translated", sql, tuple(notes))


def translate(code: str) -> Translation:
    """Every statement of ``code`` rewritten for DuckDB, with its verdict. Nothing runs here.
    ValueError when the SQL can't be read at all."""
    return Translation(tuple(_statement(i, s) for i, s in enumerate(parse(code), 1)))
