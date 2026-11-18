"""SQL over the attached lakehouse's tables, run on this PC with DuckDB.

Queries are written the way Fabric's SQL query tab takes them, in Spark SQL.
sqlglot translates each statement to DuckDB SQL, and every table it names is
read from OneLake as a Delta dataset, so DuckDB fetches only the columns and
files the query needs. Queries never change the lakehouse; writing a result
is ``lakehouse.write_table``.
"""

from __future__ import annotations

import functools
import itertools
import logging
import re
from typing import Any, Callable, Iterator, cast

import duckdb
import pyarrow as pa
import sqlglot
from sqlglot import exp
from sqlglot.dialects.duckdb import DuckDB
from sqlglot.errors import ParseError, TokenError, UnsupportedError
from sqlglot.tokens import TokenType

from . import (spark_aggregates, spark_builtins_sql, spark_complex, spark_datetime, spark_decimals, spark_hash,
               spark_sql_names,
               spark_strings, spark_windows, sql_dialects)
from .duckdb_rows import arrow_reader, for_arrow, keep_try_cast, run_through

# sqlglot logs a warning when it reads a statement as a bare command ('SHOW TABLES' ...). With no
# handler of its own, Python prints that to stderr, so it showed in a notebook cell's output.
logging.getLogger("sqlglot").addHandler(logging.NullHandler())

# View names stay unique on a connection even after superseded ones are dropped (superseded).
_VIEW_NAMES = itertools.count()


def parse(query: str, dialect: str = "spark") -> list[exp.Expression]:
    """Spark SQL first; DuckDB SQL is accepted too when it isn't valid Spark.

    ``dialect="duckdb"`` reads DuckDB SQL only. T-SQL dialects don't run here (sql_dialects.py)."""
    chosen = sql_dialects.get(dialect)
    if not chosen["runs_here"]:
        raise ValueError(f"{chosen['label']} can't run on this PC. KratosForge checks and formats "
                         "it here; run it in Fabric, or pick Spark SQL or DuckDB to run on this PC.")
    try:
        statements = _statements(sql_dialects.parse(diff_spellings(query) if chosen["id"] == "spark" else query,
                                                    read=chosen["sqlglot"]))
        if chosen["id"] == "spark":
            statements = [spark_legacy_times(spark_fixes(spark_legacy_casts(s))) for s in statements]
    except (ParseError, TokenError) as first_error:  # an unclosed quote is a TokenError
        if chosen["id"] == "spark" and (respelled := _spark_spellings(query)) != query:
            try:
                return parse(respelled, dialect)
            except ValueError:
                pass  # report the error in the query as written
        if chosen["id"] == "spark" and _only_empty_structs_fail(query):
            raise ValueError(_EMPTY_STRUCT) from None
        if chosen["id"] != "spark" or getattr(first_error, "uneven_pairs", False):  # DuckDB would drop an argument
            raise ValueError(f"Could not read the query: {sql_dialects.parse_problem(first_error)}") from None
        try:
            statements = _statements(sqlglot.parse(query, read="duckdb"))
        except (ParseError, TokenError):
            raise ValueError(f"Could not read the query: {sql_dialects.parse_problem(first_error)}") from None
    if not statements:
        raise ValueError("The query is empty.")
    return statements


def _statements(parsed: list[Any]) -> list[exp.Expression]:
    """What sqlglot.parse read, without the empty statements (a stray ';')."""
    return [cast(exp.Expression, s) for s in parsed if s is not None]


_HEX = re.compile(r"[0-9a-fA-F]*")


def _spark_spellings(query: str) -> str:
    """``query`` with two spellings Spark reads and sqlglot doesn't written the way sqlglot reads them:
    ``X '1010'`` as ``X'1010'`` (Spark reads X and a quoted hex string as a binary literal even with
    spaces between: only a lone X, not ``t.x``, before a quote of hex digits), and ``GROUP BY
    window(...)`` as ``GROUP BY `window`(...)`` (sqlglot reads a WINDOW right after GROUP BY as the
    start of a WINDOW clause)."""
    try:
        tokens = sqlglot.tokenize(query, read="spark")
    except TokenError:
        return query
    edits: list[tuple[int, int, str]] = []
    for before, token, after in zip([None, *tokens], tokens, tokens[1:], strict=False):
        if (token.token_type == TokenType.VAR and token.text in ("x", "X") and after.token_type == TokenType.STRING
                and (before is None or before.token_type != TokenType.DOT)
                and query[token.end + 1:after.start].isspace() and _HEX.fullmatch(after.text)):
            edits.append((token.end + 1, after.start, ""))
        elif (token.token_type == TokenType.WINDOW and after.token_type == TokenType.L_PAREN
              and before is not None and before.token_type == TokenType.GROUP_BY):
            edits.append((token.start, token.end + 1, "`" + token.text + "`"))
    for start, end, text in reversed(edits):
        query = query[:start] + text + query[end:]
    return query


_DIFF_UNITS = frozenset({"MICROSECOND", "MILLISECOND", "SECOND", "MINUTE", "HOUR", "DAY", "WEEK", "MONTH",
                         "QUARTER", "YEAR"})


def diff_spellings(query: str, databricks: bool = False) -> str:
    """``query`` with datediff(unit, start, end) and date_diff(unit, start, end), and in Databricks SQL
    timediff(unit, start, end), written as timestampdiff: Spark and Databricks read all of them as
    timestampdiff, which counts whole units elapsed. sqlglot reads the two-argument datediff(end, start)
    (days between the dates) into the same node as the three-argument DAY form, so they are told apart
    here, by their spelling."""
    names = ("datediff", "date_diff", "timediff") if databricks else ("datediff", "date_diff")
    try:
        tokens = sqlglot.tokenize(query, read="databricks" if databricks else "spark")
    except TokenError:
        return query
    edits = [(name.start, name.end + 1)
             for i, (name, paren, unit, comma) in enumerate(zip(tokens, tokens[1:], tokens[2:], tokens[3:], strict=False))
             if name.token_type == TokenType.VAR and name.text.lower() in names and paren.token_type == TokenType.L_PAREN
             and unit.text.upper() in _DIFF_UNITS and comma.token_type == TokenType.COMMA
             and _arguments(tokens, i + 1) == 3]
    for start, end in reversed(edits):
        query = query[:start] + "timestampdiff" + query[end:]
    return query


def _arguments(tokens: list[Any], paren: int) -> int:
    """How many arguments the call whose opening bracket is ``tokens[paren]`` has (commas at its own depth)."""
    depth, count = 0, 1
    for token in tokens[paren:]:
        if token.token_type in (TokenType.L_PAREN, TokenType.L_BRACKET, TokenType.L_BRACE):
            depth += 1
        elif token.token_type in (TokenType.R_PAREN, TokenType.R_BRACKET, TokenType.R_BRACE):
            depth -= 1
            if depth == 0:
                return count
        elif token.token_type == TokenType.COMMA and depth == 1:
            count += 1
    return count


_EMPTY_STRUCT = "An empty struct<> isn't supported on this PC. Give the struct at least one field, like struct<id: int>."


def _only_empty_structs_fail(query: str) -> bool:
    """Whether the query reads once each empty struct<> is given a field: only then is struct<> the
    error to name (not a 'struct<>' inside a string, nor a query with another mistake)."""
    try:
        tokens = sqlglot.tokenize(query, read="spark")
    except TokenError:  # a query the tokenizer refuses has another error to report
        return False
    spans = []
    for i, token in enumerate(tokens[:-1]):
        after = tokens[i + 1]
        if token.token_type == TokenType.STRUCT:
            if after.token_type == TokenType.NEQ:
                spans.append((after.start, after.end))
            elif after.token_type == TokenType.LT and i + 2 < len(tokens) and tokens[i + 2].token_type == TokenType.GT:
                spans.append((after.start, tokens[i + 2].end))
    if not spans:
        return False
    filled = query
    for start, end in reversed(spans):
        filled = filled[:start] + "<x: int>" + filled[end + 1:]
    try:
        sql_dialects.parse(filled, read="spark")
    except ParseError:
        return False
    return True


# Spark's date and time unit names that DuckDB doesn't know ('MM' is minutes to DuckDB if anything).
_SPARK_UNITS = {"YYYY": "YEAR", "YY": "YEAR", "MON": "MONTH", "MM": "MONTH", "DD": "DAY"}


def spark_fixes(statement: exp.Expression, databricks: bool = False) -> exp.Expression:
    """What a Spark SQL statement read by sqlglot needs to answer on DuckDB as it does on Spark:
    Spark's column names, its date functions and substring from 0. The Spark SQL path here and
    the DuckDB engine's Databricks SQL (engines/local_sql.py) both use it, so they can't drift.
    ``databricks``: schema_of_json writes every field name in backticks, as Databricks documents it;
    Apache Spark 3.5 (Fabric) quotes only names that need it. timestampdiff counts whole units
    (_spark_timestampdiff)."""
    statement = spark_builtins_sql.mark(statement)
    _no_reserved_names(statement)  # on the SQL as written, before any rewrite adds its own kf_ names
    if databricks:
        _mark_divisions(statement)
    statement = _try_divisions(statement)
    statement = _spark_date_results(statement, ansi=databricks)
    statement = _spark_timestampdiff(spark_builtins_sql.rewrite(
        _spark_functions(databricks, _spark_json(databricks, spark_substring(_spark_dates(spark_integer_casts(_spark_concat_text(_spark_cast_functions(spark_strings.spark_text_results(spark_aggregates.spark_aggregate_results(spark_complex.mark_calls(_spark_names(spark_windows.check_windows(statement)))))))))))),
        ansi=databricks))
    statement = spark_windows.spark_window_results(spark_complex.spark_complex_results(statement))
    if databricks:
        return _spark_range(_ansi_divisions(statement))
    return _spark_range(_spark_session_functions(_null_remainders(_not_in_spark_3_5(statement))))


def _spark_range(statement: exp.Expression) -> exp.Expression:
    """FROM range(...): Spark's column is ``id``, DuckDB's is ``range``. A fourth argument (Spark's number
    of partitions) changes no values, so it is left out. Written last, so the cast stays a plain cast. A
    query that reads a column named ``range`` (written for DuckDB's range, which Spark stops on) keeps it."""
    if any(c.name.lower() == "range" for c in statement.find_all(exp.Column)):
        return statement
    for table in list(statement.find_all(exp.Table)):
        call = table.this
        if not (isinstance(call, exp.Anonymous) and call.name.upper() == "RANGE" and 1 <= len(call.expressions) <= 4):
            continue
        args = ", ".join(duckdb_sql(a.copy()) for a in call.expressions[:3])
        alias = table.args.get("alias") or exp.TableAlias(this=exp.to_identifier("range"))
        query = sql_dialects.parse_one(f"SELECT CAST(range AS BIGINT) AS id FROM range({args})", read="duckdb")
        table.replace(exp.Subquery(this=query, alias=alias.copy()))
    return statement


# Databricks' own built-ins that Spark 3.5 (Fabric Runtime 1.3, which this path follows) doesn't have, so
# Fabric stops on them with UNRESOLVED_ROUTINE. They are refused by name here rather than left to DuckDB's
# "function does not exist" error, or answered where Fabric stops.
_DATABRICKS_ONLY = {"try_to_date": "to_date instead. With ANSI mode off, as Fabric runs it, to_date gives NULL "
                                   "for text that isn't a date"}


def _not_in_spark_3_5(statement: exp.Expression) -> exp.Expression:
    for node in statement.find_all(exp.Anonymous):
        name = node.name.lower()
        if name in _DATABRICKS_ONLY:
            raise ValueError(f"{name} isn't a Spark 3.5 function, so nothing ran. Fabric Runtime 1.3 stops on it "
                             f"too (UNRESOLVED_ROUTINE). Use {_DATABRICKS_ONLY[name]}.")
    return statement


def _null_remainders(statement: exp.Expression) -> exp.Expression:
    """a % 0 and a div 0 are NULL with ANSI mode off, as / is; DuckDB 2.0 stops on them instead."""
    # Deepest first: a template (pmod) puts copies of its arguments in several places, so an inner % or div
    # must be guarded before an outer one copies it, or the copies keep it unguarded (pmod(x, 0.0D) was NaN).
    for node in reversed(list(statement.find_all(exp.Mod, exp.IntDiv))):
        divisor = node.expression
        if not (isinstance(divisor, exp.Literal) and divisor.is_number and float(divisor.name) != 0):
            node.set("expression", exp.Nullif(this=divisor.copy(), expression=exp.Literal.number(0)))
    return statement


# Databricks SQL runs with ANSI mode on (a SQL warehouse's ANSI_MODE, and Databricks Runtime 17.0 and
# later), so a division by zero stops the query; Spark with ANSI off, as Fabric runs it, gives NULL.
_DIVIDE_BY_ZERO = ("[DIVIDE_BY_ZERO] Division by zero. Use `try_divide` to tolerate divisor being 0 and return NULL "
                   "instead. If necessary set ansi_mode to false to bypass this error.")
_DIVISIONS = (exp.Div, exp.IntDiv, exp.Mod)
_USER_DIVISION = "kf_user_division"


def _try_divisions(statement: exp.Expression) -> exp.Expression:
    """try_divide(a, b) as a division NULL for a zero divisor, typed as ``/`` is (spark_decimals reads through
    the CASE): a decimal for decimals, as in Spark. sqlglot's own form hides the division from that typing."""
    holder = exp.Tuple(expressions=[statement])  # so the statement itself can be replaced
    for node in list(statement.find_all(exp.SafeDivide)):
        guard = _with_args("CASE WHEN fj_arg0 = 0 THEN NULL END", [node.expression.copy()])
        new = exp.Case(ifs=guard.args["ifs"], default=exp.Div(this=node.this, expression=node.expression))
        new.meta.update(spark_sql_names.kept(node))
        new.meta[spark_decimals.ZERO_GUARD] = True
        node.replace(new)
    return cast(exp.Expression, holder.expressions[0])


def _mark_divisions(statement: exp.Expression) -> None:
    """Mark the query's own /, div and %, before other rewrites write theirs (an average's sum / count)."""
    for node in statement.find_all(*_DIVISIONS):
        node.meta[_USER_DIVISION] = True


def _ansi_divisions(statement: exp.Expression) -> exp.Expression:
    """The query's own /, div and % stop on a zero divisor, as with ANSI mode on (see _DIVIDE_BY_ZERO). The
    division itself is left as it is, inside a CASE, so spark_decimals can still type it; a divisor that
    can change between reads (rand()) is read once through a lambda argument instead."""
    stop = "ERROR(" + exp.Literal.string(_DIVIDE_BY_ZERO).sql("duckdb") + ")"
    holder = exp.Tuple(expressions=[statement])  # so the statement itself can be replaced
    for node in list(statement.find_all(*_DIVISIONS)):
        if not node.meta.pop(_USER_DIVISION, False):
            continue
        divisor = node.expression
        if divisor.find(exp.Rand, exp.Randn, exp.Uuid):
            text = "(LIST_TRANSFORM([fj_arg0], kf_divisor -> CASE WHEN kf_divisor = 0 AND fj_arg1 IS NOT NULL " \
                   "THEN " + stop + " ELSE kf_divisor END))[1]"
            node.set("expression", exp.paren(_with_args(text, [divisor, node.this.copy()])))
            continue
        # A NULL dividend gives NULL before the divisor is looked at (Spark's DivModLike), so an empty
        # group's sum(x) / count(*) is NULL, not a stop.
        guard = _with_args("CASE WHEN fj_arg0 = 0 AND fj_arg1 IS NOT NULL THEN " + stop + " END",
                           [divisor, node.this.copy()])
        new = exp.Case(ifs=guard.args["ifs"], default=node.copy())
        new.meta.update(spark_sql_names.kept(node))
        new.meta[spark_decimals.ZERO_GUARD] = True  # typed as the division it holds
        node.replace(new)
    return cast(exp.Expression, holder.expressions[0])


# Spark's date and time functions that return an int (sqlglot's DuckDB SQL gives a BIGINT).
_SPARK_INTS = (exp.Year, exp.Month, exp.Day, exp.DayOfMonth, exp.DayOfYear, exp.WeekOfYear, exp.DayOfWeek,
               exp.Quarter, exp.Hour, exp.Minute, exp.Second, exp.DateDiff, exp.UnixDate)
# The units Spark's trunc(date, unit) and date_trunc(unit, ts) take (DateTimeUtils.parseTruncLevel);
# any other is NULL with ANSI off.
_TRUNC_UNITS = {"YEAR", "YYYY", "YY", "QUARTER", "MONTH", "MM", "MON", "WEEK"}
_DATE_TRUNC_UNITS = _TRUNC_UNITS | {"DAY", "DD", "HOUR", "MINUTE", "SECOND", "MILLISECOND", "MICROSECOND"}
# next_day's day names, as Spark reads them (DateTimeUtils.getDayOfWeekFromString), ISO numbered.
_NEXT_DAY = ("CAST(TRY_CAST(fj_arg0 AS DATE) + TO_DAYS(CAST((((CASE UPPER(CAST(fj_arg1 AS VARCHAR)) "
             + " ".join(f"WHEN '{n[:2]}' THEN {i} WHEN '{n[:3]}' THEN {i} WHEN '{n}' THEN {i}" for i, n in enumerate(
                 ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"), 1))
             + " END) - ISODOW(TRY_CAST(fj_arg0 AS DATE)) + 6) % 7) + 1 AS INTEGER)) AS DATE)")


# make_date of a month or day that doesn't exist is NULL. The parts are checked rather than the call wrapped in
# TRY, which DuckDB 2.0 refuses around anything volatile, such as the ERROR() of a Spark cast; each part is
# read once, and the nested CASE keeps MAKE_DATE from seeing a month outside 1 to 12.
_MAKE_DATE = ("(LIST_TRANSFORM([{'y': CAST(fj_arg0 AS BIGINT), 'm': CAST(fj_arg1 AS BIGINT), 'd': CAST(fj_arg2 AS BIGINT)}], "
              "kf_md -> CASE WHEN kf_md.m BETWEEN 1 AND 12 AND kf_md.d >= 1 THEN CASE WHEN kf_md.d <= "
              "DAYOFMONTH(LAST_DAY(MAKE_DATE(kf_md.y, kf_md.m, 1))) THEN MAKE_DATE(kf_md.y, kf_md.m, kf_md.d) END END))[1]")


def _spark_date_results(statement: exp.Expression, ansi: bool) -> exp.Expression:
    """Spark's result types and NULLs for its date and time functions (WR-106). year, month, day,
    dayofyear, weekofyear, dayofweek, quarter, hour, minute, second, datediff and unix_date are an
    int, as in Spark (Databricks SQL too). With ANSI off, the Spark SQL path only: text that isn't a
    date gives NULL in date_add, date_sub, datediff, last_day and the functions above; a unit trunc or
    date_trunc doesn't take, and a day name next_day doesn't know, give NULL; make_date of a day
    that doesn't exist is NULL."""
    def wrap(node: exp.Expression, sql_text: str, args: list[exp.Expression]) -> exp.Expression:
        new = _with_args(sql_text, args)
        new.meta.update(spark_sql_names.kept(node))
        node.replace(new)
        return new
    if not ansi:
        # Deepest first: wrap() puts copies of a call's arguments in its new SQL, so a call inside another one
        # is rewritten before it is copied (next_day(make_date(1900, -1, -1), 'MON') stopped with "Date out of
        # range", since only the original, now unused, make_date was made NULL-safe).
        for found in reversed(list(statement.find_all(exp.TsOrDsToDate, exp.TsOrDsAdd, exp.LastDay, exp.UnixDate,
                                                      exp.NextDay, exp.DateTrunc, exp.TimestampTrunc,
                                                      exp.Anonymous))):
            node = cast(exp.Expression, found)  # sqlglot's Func isn't typed as an Expression
            if isinstance(node, exp.TsOrDsToDate):
                if node.args.get("format") is None:
                    node.set("safe", True)
            elif isinstance(node, (exp.TsOrDsAdd, exp.LastDay, exp.UnixDate)):
                if not isinstance(node.this, exp.Cast):
                    node.set("this", exp.TryCast(this=node.this.copy(), to=exp.DataType.build("DATE")))
            elif isinstance(node, exp.NextDay):
                if node is not statement:
                    wrap(node, _NEXT_DAY, [node.this, node.expression])
            elif isinstance(node, (exp.DateTrunc, exp.TimestampTrunc)):
                unit = node.args.get("unit")
                trunc = isinstance(unit, exp.Literal)  # trunc(date, 'MM'); date_trunc('MM', ts) has a Var
                allowed = _TRUNC_UNITS if trunc else _DATE_TRUNC_UNITS
                if isinstance(unit, (exp.Literal, exp.Var)) and unit.name.upper() not in allowed and node is not statement:
                    wrap(node, "CAST(NULL AS DATE)" if trunc else "CAST(NULL AS TIMESTAMPTZ)", [])
            elif node.name.upper() == "MAKE_DATE" and len(node.expressions) == 3 and node is not statement:
                wrap(node, _MAKE_DATE, list(node.expressions))
    ints = [cast(exp.Expression, n) for n in statement.find_all(*_SPARK_INTS, exp.Anonymous)
            if not isinstance(n, exp.Anonymous) or n.name.upper() == "WEEKDAY"]
    for node in ints:
        if node is statement or (isinstance(node, exp.DateDiff) and node.args.get("unit") is not None):
            continue
        if isinstance(node.parent, exp.Cast) and node.parent.to.is_type(exp.DataType.Type.INT):
            continue
        new = exp.Cast(to=exp.DataType.build("INT"))
        new.meta.update(spark_sql_names.kept(node))
        node.replace(new)
        new.set("this", node)
    return statement


_USER_REFUSED = ("in Fabric it names the identity running the notebook, and a SQL cell here would name DuckDB "
                 "instead.")
# Spark's session functions this PC refuses by name in a Spark SQL cell: the reason, after "so nothing ran: "
_SESSION_REFUSED = {
    "current_user": _USER_REFUSED, "user": _USER_REFUSED, "session_user": _USER_REFUSED,
    "version": ("in Fabric it gives its Spark build and git revision, and a SQL cell here would give DuckDB's "
                "version instead."),
}


def _spark_session_functions(statement: exp.Expression) -> exp.Expression:
    """Spark's session functions in a Fabric notebook, where DuckDB would describe its own session
    ('memory' for its catalog). current_catalog() is spark_catalog, as in Fabric; current_timezone()
    is the session's spark.sql.session.timeZone (DuckDB's TimeZone, which spark.conf.set changes).
    current_database() and current_schema() are refused: in Fabric they name the default lakehouse,
    which a SQL cell here can't read; spark.catalog.currentDatabase() can. current_user(), user(),
    session_user() and version() are refused too: Fabric names the identity running the notebook and its
    own Spark build, where DuckDB would give 'duckdb' and its own version. Databricks SQL keeps
    DuckDB's own answers (its session is the engine's, not a Unity Catalog one)."""
    for user in list(statement.find_all(exp.CurrentUser, exp.SessionUser, exp.Anonymous)):
        name = ("current_user" if isinstance(user, exp.CurrentUser) else "session_user"
                if isinstance(user, exp.SessionUser) else user.name.lower())
        if name in _SESSION_REFUSED:
            raise ValueError(f"{name}() can't run on this PC the way it runs in Fabric, so nothing ran: "
                             f"{_SESSION_REFUSED[name]} Run the cell in Fabric.")
    for node in list(statement.find_all(exp.CurrentCatalog, exp.CurrentTimezone, exp.CurrentDatabase,
                                        exp.CurrentSchema)):
        if isinstance(node, exp.CurrentCatalog):
            new: exp.Expression = exp.Literal.string("spark_catalog")
        elif isinstance(node, exp.CurrentTimezone):
            new = exp.Anonymous(this="CURRENT_SETTING", expressions=[exp.Literal.string("TimeZone")])
        else:
            name = "current_database" if isinstance(node, exp.CurrentDatabase) else "current_schema"
            raise ValueError(f"{name}() can't run on this PC the way it runs in Fabric, so nothing ran: in Fabric "
                             "it names the notebook's default lakehouse, and a SQL cell here would name DuckDB's "
                             "own database instead. Use spark.catalog.currentDatabase() in a Python cell, or run "
                             "the cell in Fabric.")
        if node is statement:
            return new
        node.replace(new)
    return statement


# The meta key naming the Spark function a template was written out from.
SPARK_FUNCTION = "kf_spark_function"
# Spark functions whose sqlglot DuckDB SQL answers differently or doesn't run, as DuckDB SQL that
# answers as Spark does ({0}, {1} are the arguments). Lambda names can't clash with a column.
_ANON_TEMPLATES = {
    # Spark's MathUtils.pmod: the remainder, moved by the divisor only when it's negative.
    "PMOD": "CASE WHEN ({0}) % ({1}) < 0 THEN ((({0}) % ({1})) + ({1})) % ({1}) ELSE ({0}) % ({1}) END",
    "ARRAYS_OVERLAP": "CASE WHEN ({0}) IS NULL OR ({1}) IS NULL THEN NULL WHEN LIST_HAS_ANY({0}, {1}) THEN TRUE "
                      "WHEN LEN({0}) > 0 AND LEN({1}) > 0 AND (LIST_POSITION({0}, NULL) IS NOT NULL "
                      "OR LIST_POSITION({1}, NULL) IS NOT NULL) THEN NULL ELSE FALSE END",
    "ARRAY_UNION": "CASE WHEN ({0}) IS NULL OR ({1}) IS NULL THEN NULL ELSE LIST_FILTER(LIST_CONCAT({0}, {1}), "
                   "(kf_v, kf_i) -> LIST_POSITION(LIST_CONCAT({0}, {1}), kf_v) = kf_i) END",
    # Half-even rounding (bround with one argument rounds to 0 places; see _spark_functions). DuckDB
    # returns a DOUBLE where Databricks keeps a decimal input decimal; the value is the same.
    "BROUND": "ROUND_EVEN({0}, {1})",
    # conv(num, from, to), as Spark's NumberConverter: spaces trimmed, the digits up to the first invalid one, read
    # as an unsigned 64-bit number (more saturates to all ones); a negative number with a positive
    # target base is its two's complement, and a negative target base prints a signed number.
    "CONV": "(LIST_TRANSFORM([{{'s': TRIM(CAST({0} AS VARCHAR), ' '), 'f': CAST({1} AS INTEGER), 't': CAST({2} AS INTEGER)}}], "
            "kf_a -> CASE WHEN kf_a.s IS NULL OR kf_a.f IS NULL OR kf_a.t IS NULL OR kf_a.s = '' "
            "OR kf_a.f NOT BETWEEN 2 AND 36 OR ABS(kf_a.t) NOT BETWEEN 2 AND 36 THEN NULL ELSE "
            "(LIST_TRANSFORM([{{'neg': LEFT(kf_a.s, 1) = '-', 't': kf_a.t, 'u': LEAST(LIST_REDUCE("
            "(LIST_TRANSFORM([LIST_TRANSFORM(STRING_SPLIT(UPPER(CASE WHEN LEFT(kf_a.s, 1) = '-' THEN SUBSTR(kf_a.s, 2) "
            "ELSE kf_a.s END), ''), kf_c -> INSTR('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ', kf_c) - 1)], "
            "kf_d -> kf_d[1:COALESCE(LIST_POSITION(LIST_TRANSFORM(kf_d, kf_x -> kf_x < 0 OR kf_x >= kf_a.f), TRUE), "
            "LEN(kf_d) + 1) - 1]))[1], (kf_n, kf_x) -> LEAST(kf_n * kf_a.f + kf_x, 18446744073709551616), "
            "0::HUGEINT), 18446744073709551615)}}], "
            "kf_b -> (LIST_TRANSFORM([CASE WHEN kf_b.t > 0 THEN {{'minus': FALSE, 'v': CASE WHEN NOT kf_b.neg THEN kf_b.u "
            "WHEN kf_b.u >= 9223372036854775808 THEN 18446744073709551615 "
            "ELSE (18446744073709551616 - kf_b.u) % 18446744073709551616 END}} "
            "ELSE {{'minus': kf_b.neg OR kf_b.u >= 9223372036854775808, 'v': CASE WHEN kf_b.u >= 9223372036854775808 "
            "THEN 18446744073709551616 - kf_b.u ELSE kf_b.u END}} END], "
            "kf_r -> CASE WHEN kf_r.minus THEN '-' ELSE '' END || CASE WHEN kf_r.v < 9223372036854775808 "
            "THEN TO_BASE(kf_r.v::BIGINT, ABS(kf_b.t)) ELSE TO_BASE((kf_r.v // ABS(kf_b.t))::BIGINT, ABS(kf_b.t)) || "
            "SUBSTR('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ', (kf_r.v % ABS(kf_b.t))::INTEGER + 1, 1) END))[1]))[1] END))[1]",
    # Spark's American Soundex: the first letter, then up to three codes, skipping vowels, H and W
    # (which don't break a run), and repeats; a non-letter breaks a run. Text that doesn't start
    # with a letter comes back as it is.
    "SOUNDEX": "(LIST_TRANSFORM([CAST({0} AS VARCHAR)], kf_s -> CASE WHEN kf_s IS NULL THEN NULL WHEN kf_s = '' THEN '' "
               "WHEN NOT REGEXP_MATCHES(LEFT(kf_s, 1), '^[A-Za-z]$') THEN kf_s ELSE UPPER(LEFT(kf_s, 1)) || "
               "(LIST_TRANSFORM([STRING_SPLIT(TRANSLATE(UPPER(LEFT(kf_s, 1)), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
               "'01230127022455012623017202') || REGEXP_REPLACE(REPLACE(TRANSLATE(UPPER(SUBSTR(kf_s, 2)), "
               "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', '01230127022455012623017202'), '7', ''), '[^0-9]', '0', 'g'), '')], "
               "kf_c -> RPAD(LEFT(ARRAY_TO_STRING(LIST_FILTER(kf_c[2:], (kf_v, kf_i) -> kf_v <> '0' AND kf_v <> kf_c[kf_i]), "
               "''), 3), 3, '0')))[1] END))[1]",
}
def _bround(places: int) -> str:
    """bround(x, places) with places written out: half-even, as Spark's, keeping a decimal a decimal
    (DuckDB's ROUND and TRUNC do; ROUND_EVEN returns a DOUBLE). A value exactly half way whose lower
    neighbour is even is truncated; everything else rounds half away from zero."""
    scaled = f"({{0}}) * {10 ** places}" if places >= 0 else f"({{0}}) / {10 ** -places}"
    return (f"CASE WHEN ABS({scaled} - TRUNC({scaled})) = 0.5 AND TRUNC({scaled}) % 2 = 0 "
            f"THEN TRUNC({{0}}, {places}) ELSE ROUND({{0}}, {places}) END")


# format_number(x, d) with a whole number of places: decimals round half-even first, as Spark's
# BigDecimal does; doubles are formatted as they are, as Java's DecimalFormat does.
_FORMAT_NUMBER = ("CASE WHEN TYPEOF({0}) LIKE 'DECIMAL%' THEN FORMAT('{{:,.{1}f}}', CAST(ROUND_EVEN({0}, {1}) AS DOUBLE)) "
                  "ELSE FORMAT('{{:,.{1}f}}', CAST({0} AS DOUBLE)) END")
# exists(array, x -> ...): true if any is true, else NULL if any is NULL, else false (Spark's
# three-valued exists).
_EXISTS = ("(LIST_TRANSFORM([LIST_TRANSFORM({0}, {1})], kf_t -> CASE WHEN kf_t IS NULL THEN NULL "
           "WHEN LIST_BOOL_OR(kf_t) THEN TRUE WHEN LIST_POSITION(kf_t, NULL) IS NOT NULL THEN NULL ELSE FALSE END))[1]")
_DISTINCT = "LIST_FILTER({0}, (kf_v, kf_i) -> LIST_POSITION({0}, kf_v) = kf_i)"
# array_intersect and array_except: first-seen order of the first array, each value once, NULL included.
_INTERSECT = ("CASE WHEN ({0}) IS NULL OR ({1}) IS NULL THEN NULL ELSE LIST_FILTER({0}, (kf_v, kf_i) -> "
              "LIST_POSITION({0}, kf_v) = kf_i AND LIST_POSITION({1}, kf_v) IS NOT NULL) END")
_EXCEPT = ("CASE WHEN ({0}) IS NULL OR ({1}) IS NULL THEN NULL ELSE LIST_FILTER({0}, (kf_v, kf_i) -> "
           "LIST_POSITION({0}, kf_v) = kf_i AND LIST_POSITION({1}, kf_v) IS NULL) END")


# zip_with, map_filter, transform_values and transform_keys (#1371). Each user lambda argument is bound
# to its value by a one-item LIST_TRANSFORM ({1} is the bound body), so the body keeps its own names.
# zip_with pads the shorter array with NULL and is NULL when either array is; transform_keys refuses a
# NULL or repeated key, as Databricks does (NULL_MAP_KEY, DUPLICATED_MAP_KEY).
_ZIP_WITH = ("CASE WHEN ({0}) IS NULL OR ({1}) IS NULL THEN NULL ELSE LIST_TRANSFORM(RANGE(1, GREATEST(LEN({0}), "
             "LEN({1})) + 1), kf_i -> (LIST_TRANSFORM([{{'a': ({0})[kf_i], 'b': ({1})[kf_i]}}], kf_p -> {2}))[1]) END")
_MAP_FILTER = "MAP_FROM_ENTRIES(LIST_FILTER(MAP_ENTRIES({0}), kf_p -> {1}))"
_TRANSFORM_VALUES = "MAP_FROM_ENTRIES(LIST_TRANSFORM(MAP_ENTRIES({0}), kf_p -> {{'key': kf_p.key, 'value': {1}}}))"
_TRANSFORM_KEYS = ("(LIST_TRANSFORM([LIST_TRANSFORM(MAP_ENTRIES({0}), kf_p -> {{'key': {1}, 'value': kf_p.value}})], "
                   "kf_l -> CASE WHEN kf_l IS NULL THEN NULL "
                   "WHEN LIST_POSITION(LIST_TRANSFORM(kf_l, kf_n -> kf_n.key), NULL) IS NOT NULL THEN "
                   "ERROR('[NULL_MAP_KEY] transform_keys made a NULL key. Databricks refuses a NULL map key.') "
                   "WHEN LIST_UNIQUE(LIST_TRANSFORM(kf_l, kf_n -> kf_n.key)) <> LEN(kf_l) THEN "
                   "ERROR('[DUPLICATED_MAP_KEY] transform_keys made the same key twice. Databricks refuses a "
                   "duplicate map key.') ELSE MAP_FROM_ENTRIES(kf_l) END))[1]")
_MAP_LAMBDAS = {"ZIP_WITH": (3, ("kf_p.a", "kf_p.b"), _ZIP_WITH),
                "MAP_FILTER": (2, ("kf_p.key", "kf_p.value"), _MAP_FILTER),
                "TRANSFORM_VALUES": (2, ("kf_p.key", "kf_p.value"), _TRANSFORM_VALUES),
                "TRANSFORM_KEYS": (2, ("kf_p.key", "kf_p.value"), _TRANSFORM_KEYS)}
# Calls whose rewrite wraps their arguments in kf_ lambdas (see _no_reserved_names).
_KF_TEMPLATED = ({name for name, template in _ANON_TEMPLATES.items() if "kf_" in template} | set(_MAP_LAMBDAS)
                 | {"FORALL"})


def _no_reserved_names(statement: exp.Expression) -> None:
    """The rewrites below wrap a person's SQL in lambdas whose arguments start with kf_. A name of
    theirs that starts with kf_ (a lambda argument or a column) inside a call that is rewritten that
    way would hide one of those, or be hidden by one, and fail or answer wrongly, so it is refused with
    a plain message instead (#1371). Checked once, on the statement as written, before any rewrite, so
    a rewrite's own kf_ names inside another rewritten call are never mistaken for the person's."""
    for call in statement.find_all(exp.ArrayDistinct, exp.ArrayIntersect, exp.ArrayExcept, exp.Soundex, exp.Exists,
                                   exp.Reduce, exp.ArraySort, exp.Anonymous):
        if isinstance(call, exp.Anonymous) and str(call.this).upper() not in _KF_TEMPLATED:
            continue
        if isinstance(call, exp.Exists) and not isinstance(call.expression, exp.Lambda):
            continue   # EXISTS (SELECT ...) is a subquery, not the rewritten exists(array, x -> ...)
        if isinstance(call, exp.ArraySort) and not isinstance(call.args.get("expression"), exp.Lambda):
            continue   # array_sort without a comparator isn't wrapped in lambdas
        if isinstance(call, exp.Reduce) and call.args.get("finish") is None:
            continue   # aggregate without finish is left as sqlglot writes it
        for ident in call.find_all(exp.Identifier):
            if ident.name.lower().startswith("kf_"):
                raise ValueError(f"The name {ident.name} can't be used inside this function on this PC: names "
                                 "that start with kf_ are reserved here. Rename it (AS ... or another lambda "
                                 "argument name) and run it again.")


def _bind(func: exp.Lambda, values: tuple[str, ...]) -> str:
    """DuckDB SQL for ``func``'s body with each of its arguments bound to the DuckDB SQL in ``values``.
    The values name only the rewrite's own kf_ arguments, and a person's kf_ names were refused first
    (_no_reserved_names), so neither can hide the other."""
    func = func.copy()
    names = {param.name.lower() for param in func.expressions}
    # Quoted, so an argument named like a DuckDB keyword (left, right) still reads as a name.
    for ident in [*func.expressions, *func.this.find_all(exp.Identifier)]:
        if isinstance(ident, exp.Identifier) and ident.name.lower() in names:
            ident.set("quoted", True)
    text = duckdb_sql(func.this)
    for param, value in reversed(list(zip(func.expressions, values, strict=True))):
        text = f"(LIST_TRANSFORM([{value}], {duckdb_sql(param)} -> {text}))[1]"
    return text


def _higher_order(node: exp.Anonymous) -> exp.Expression | None:
    """zip_with, map_filter, transform_values or transform_keys as DuckDB SQL; None when the call isn't
    one of them with a lambda of two arguments."""
    name, args = str(node.this).upper(), list(node.expressions)
    if name not in _MAP_LAMBDAS:
        return None
    count, values, template = _MAP_LAMBDAS[name]
    func = args[-1] if args else None
    if len(args) != count or not isinstance(func, exp.Lambda) or len(func.expressions) != 2:
        return None
    text = template.format(*(duckdb_sql(a) for a in args[:-1]), _bind(func, values))
    return cast(exp.Expression, sqlglot.parse_one(text, read="duckdb"))


# aggregate/reduce with a finish lambda. DuckDB's LIST_REDUCE keeps the accumulator in the elements'
# type, so the start rides in front of the elements as {s: start} and each step makes a new {s: ...};
# a merge that returns another type than start is refused, as Spark refuses it, rather than cast.
_REDUCE_FINISH = ("CASE WHEN ({0}) IS NULL THEN NULL ELSE (LIST_TRANSFORM([LIST_REDUCE(LIST_CONCAT("
                  "[{{'s': {1}, 'x': NULL}}], LIST_TRANSFORM({0}, kf_e -> {{'s': NULL, 'x': kf_e}})), "
                  "(kf_a, kf_b) -> {{'s': (LIST_TRANSFORM([{2}], kf_m -> CASE WHEN TYPEOF(kf_m) = TYPEOF(kf_a.s) "
                  "THEN kf_m ELSE ERROR('aggregate''s merge lambda returns another type than its start value, which "
                  "Databricks refuses. Cast the start value to the type merge returns.') END))[1], 'x': NULL}}).s], "
                  "kf_f -> {3}))[1] END")


def _reduce_finish(node: exp.Reduce) -> exp.Expression | None:
    """aggregate/reduce with a finish lambda, whose accumulator may be another type than the elements
    (a struct, say): NULL for a NULL array, as in Spark, which doesn't call finish then."""
    merge, finish = node.args.get("merge"), node.args.get("finish")
    if not (isinstance(finish, exp.Lambda) and len(finish.expressions) == 1
            and isinstance(merge, exp.Lambda) and len(merge.expressions) == 2):
        return None
    text = _REDUCE_FINISH.format(duckdb_sql(node.this), duckdb_sql(node.args["initial"]),
                                 _bind(merge, ("kf_a.s", "kf_b.x")), _bind(finish, ("kf_f",)))
    return cast(exp.Expression, sqlglot.parse_one(text, read="duckdb"))


# array_sort(a, (left, right) -> ...) as Spark's stable sort with that comparator: each element's place
# is the number of elements that compare less than it plus the earlier ones that compare equal ({1} is
# the bound comparator; an element is never compared with itself). A NULL comparison is refused, as
# Spark refuses it. Every pair is compared, so the work grows with the square of the array's length:
# fine for the arrays a row usually holds, slow past a few thousand elements (about 1.5 s for 2,000).
# DuckDB doesn't honour an interrupt inside one row's list, so an array of more than
# _ARRAY_SORT_BY_LIMIT (3,000) elements is refused, keeping a row to a few seconds (#1419).
_ARRAY_SORT_BY_LIMIT = 3_000
_ARRAY_SORT_BY = ("(LIST_TRANSFORM([{0}], kf_l -> CASE WHEN kf_l IS NULL THEN NULL WHEN LEN(kf_l) > "
                  f"{_ARRAY_SORT_BY_LIMIT} THEN ERROR('array_sort with a comparator sorts up to "
                  f"{_ARRAY_SORT_BY_LIMIT:,} elements on this PC, and this array has more, so nothing ran. Use "
                  "array_sort or sort_array without a comparator, or run it on Databricks.') "
                  "ELSE LIST_TRANSFORM(LIST_SORT("
                  "LIST_TRANSFORM(kf_l, (kf_x, kf_i) -> {{'r': LEN(LIST_FILTER(LIST_FILTER(RANGE(1, LEN(kf_l) + 1), "
                  "kf_k -> kf_k <> kf_i), kf_j -> "
                  "(LIST_TRANSFORM([{1}], kf_c -> CASE WHEN kf_c IS NULL THEN ERROR('array_sort''s comparator returned "
                  "NULL, which Databricks refuses. Make it return -1, 0 or 1 for every pair, NULLs included.') "
                  "ELSE kf_c < 0 OR (kf_c = 0 AND kf_j < kf_i) END))[1])), 'v': kf_x}})), kf_y -> kf_y.v) END))[1]")


def _array_sort_by(node: exp.Expression) -> exp.Expression | None:
    """array_sort with a comparator of two arguments as DuckDB SQL (#1413); None for any other sort."""
    func = node.args.get("expression") if isinstance(node, exp.ArraySort) else None
    if not isinstance(func, exp.Lambda) or len(func.expressions) != 2:
        return None
    text = _ARRAY_SORT_BY.format(duckdb_sql(node.this), _bind(func, ("kf_l[kf_j]", "kf_x")))
    return cast(exp.Expression, sqlglot.parse_one(text, read="duckdb"))


def _arity(template: str) -> int:
    return len(set(re.findall(r"(?<!\{)\{(\d)\}", template)))


def _from_template(template: str, args: list[exp.Expression]) -> exp.Expression:
    """``template`` ({0}, {1}, ... for the arguments) with the argument nodes themselves put in, not
    their text: a call inside one that spark_builtins_sql marked keeps its mark, so it is still written
    as Spark's (pmod(hash(x), 7) is Spark's hash, not DuckDB's)."""
    return _with_args(template.format(*(f"fj_arg{i}" for i in range(len(args)))), args)


# sqlglot 30.24 folded SortArray into ArraySort, which then carries asc and nulls_first itself.
_SORTS = tuple(c for c in (getattr(exp, "SortArray", None), exp.ArraySort) if c is not None)


def _sort_nulls_first(node: exp.Expression) -> bool | None:
    """Where Spark puts NULL for this sort: sort_array first when ascending and last when descending,
    array_sort last. None for an array_sort with a comparator, which keeps sqlglot's translation."""
    asc = node.args.get("asc")
    if asc is not None and not isinstance(asc, exp.Boolean):
        return None
    up = asc is None or asc.this
    if "nulls_first" in node.args:  # 30.24 and later: sqlglot already says which Spark function it was
        if isinstance(node, exp.ArraySort) and node.expression is not None:
            return None
        flag = node.args.get("nulls_first")
        return bool(flag.this) if isinstance(flag, exp.Boolean) else False
    if isinstance(node, exp.ArraySort):
        return None if node.expression is not None else False
    return up  # SortArray before 30.24


def _spark_functions(databricks: bool, statement: exp.Expression) -> exp.Expression:
    """Spark functions DuckDB answers differently, rewritten to answer as Spark does (#1105):
    dayofweek counts Sunday as 1, sort_array puts NULL first ascending and last descending,
    array_sort puts NULL last, percentile interpolates, array_distinct, array_union, array_intersect
    and array_except keep first-seen order and one NULL, arrays_overlap is NULL when only a NULL could match, last_day and
    next_day take text, pmod moves a negative remainder by the divisor, and bround, conv, soundex,
    format_number and exists (with a lambda) are written in DuckDB SQL (#1168), as are zip_with,
    map_filter, transform_keys, transform_values and aggregate/reduce with a finish lambda (#1371). Deepest first, so a rewritten argument is
    written out already rewritten."""
    _spark_json_paths(statement, databricks)
    _spark_lambdas(statement)
    found = list(statement.find_all(exp.DayOfWeek, *_SORTS, exp.Quantile, exp.ArrayDistinct,
                                    exp.ArrayIntersect, exp.ArrayExcept, exp.LastDay, exp.NextDay, exp.Soundex, exp.Exists,
                                    exp.MapContainsKey, exp.Reduce, exp.Anonymous))
    for node in reversed(found):
        if isinstance(node, exp.MapContainsKey):
            # Spark's map_contains_key is array_contains(map_keys(m), k), NULL key included; sqlglot
            # writes the map and the key the other way round (#1325).
            node.replace(_from_template("ARRAY_CONTAINS(MAP_KEYS({0}), {1})", [node.this, node.args["key"]]))
        elif isinstance(node, exp.DayOfWeek):
            node.replace(exp.paren(exp.Add(this=node.copy(), expression=exp.Literal.number(1))))
        elif isinstance(node, _SORTS) and (sorted_by := _array_sort_by(node)) is not None:
            node.replace(sorted_by)
        elif isinstance(node, _SORTS):
            nulls_first = _sort_nulls_first(node)
            if nulls_first is not None:
                asc = node.args.get("asc")
                up = asc is None or asc.this
                node.replace(exp.Anonymous(this="LIST_SORT", expressions=[
                    node.this, exp.Literal.string("ASC" if up else "DESC"),
                    exp.Literal.string("NULLS FIRST" if nulls_first else "NULLS LAST")]))
        elif isinstance(node, exp.Quantile) and not isinstance(node, exp.ApproxQuantile):
            node.replace(exp.Anonymous(this="QUANTILE_CONT", expressions=[node.this, node.args["quantile"]]))
        elif isinstance(node, exp.ArrayDistinct):
            node.replace(_from_template(_DISTINCT, [node.this]))
        elif isinstance(node, exp.ArrayIntersect) and len(node.expressions) == 2:
            node.replace(_from_template(_INTERSECT, list(node.expressions)))
        elif isinstance(node, exp.ArrayExcept):
            node.replace(_from_template(_EXCEPT, [node.this, node.expression]))
        elif isinstance(node, (exp.LastDay, exp.NextDay)):
            node.set("this", exp.cast(node.this, exp.DataType.Type.DATE))
        elif isinstance(node, exp.Soundex):
            node.replace(_from_template(_ANON_TEMPLATES["SOUNDEX"], [node.this]))
        elif isinstance(node, exp.Exists) and isinstance(node.expression, exp.Lambda):
            node.replace(_from_template(_EXISTS, [node.this, node.expression]))
        elif isinstance(node, exp.Reduce):
            finished = _reduce_finish(node)
            if finished is not None:
                node.replace(finished)
        elif isinstance(node, exp.Anonymous):
            name, args = str(node.this).upper(), list(node.expressions)
            rewritten = _higher_order(node)
            if rewritten is not None:
                node.replace(rewritten)
                continue
            if name == "BROUND" and len(args) == 1:
                args.append(exp.Literal.number(0))
            places = args[1] if name == "BROUND" and len(args) == 2 else None
            if places is not None and (places.is_int or (isinstance(places, exp.Neg) and places.this.is_int)):
                written = _from_template(_bround(int(places.sql())), [args[0]])
                written.meta[SPARK_FUNCTION] = name  # spark_decimals types it as Spark's round
                node.replace(written)
                continue
            if name == "FORMAT_NUMBER" and len(args) == 2:
                places = args[1]
                if isinstance(places, exp.Neg) and places.this.is_int:  # a negative count is NULL in Spark
                    node.replace(exp.Null())
                elif places.is_int:
                    node.replace(_from_template(_FORMAT_NUMBER.replace("{1}", str(int(places.name))), [args[0]]))
            elif name == "FORALL" and len(args) == 2 and isinstance(args[1], exp.Lambda):
                # forall(a, x -> p) is NOT exists(a, x -> NOT p): FALSE on any FALSE, else NULL on any NULL.
                negated = exp.Lambda(this=exp.Not(this=exp.paren(args[1].this.copy())),
                                     expressions=[e.copy() for e in args[1].expressions])
                node.replace(exp.Not(this=exp.paren(_from_template(_EXISTS, [args[0], negated]))))
            elif name in _ANON_TEMPLATES and len(args) == _arity(_ANON_TEMPLATES[name]):
                written = _from_template(_ANON_TEMPLATES[name], args)
                written.meta[SPARK_FUNCTION] = name  # spark_decimals types pmod by Spark's Remainder rule
                node.replace(written)
    return statement


# get_json_object's path as Spark's JsonPathParser reads it: $, then .name (up to the next . or [), [n],
# ['name'], or ["name"] in Databricks (Spark 3.5 answers NULL for it); [*] and .* are its wildcards (one [*] is read by _JSON_WILDCARD, the rest refused in
# _spark_lambdas). Any other path is NULL in Spark.
_SPARK_JSON_STEP = re.compile(r"(?P<wildcard>\[\*\]|\.\*(?=[.\[]|$))|\.(?P<name>[^.\[]+)|\[(?P<index>\d+)\]"
                              r"|\['(?P<quoted>[^']+)'\]|\[\"(?P<double_quoted>[^\"]+)\"\]")


def spark_json_path(path: str, databricks: bool = False) -> str | None:
    """Spark's get_json_object ``path`` as a DuckDB JSON path, or None where Spark answers NULL for it.
    A wildcard is kept as it is, for the refusal. ["name"] is read only for Databricks."""
    if not path.startswith("$"):
        return None
    out, at = "$", 1
    while at < len(path):
        step = _SPARK_JSON_STEP.match(path, at)
        if step is None:
            return None
        name, index, quoted, double_quoted, wildcard = (
            step.group(g) for g in ("name", "index", "quoted", "double_quoted", "wildcard"))
        if double_quoted is not None and not databricks:
            return None
        if wildcard:
            out += "[*]" if wildcard == "[*]" else ".*"
        elif index is not None:
            out += f"[{int(index)}]"
        else:
            key = next(k for k in (name, quoted, double_quoted) if k is not None)
            if '"' in key or "\\" in key:
                raise ValueError(f"get_json_object can't read the key {key!r} on this PC (a key with a quote or "
                                 "backslash), so nothing ran. Read the value with from_json instead.")
            out += f'."{key}"'
        at = step.end()
    return out


def _written_text(arg: str) -> str | None:
    """The value of a Spark string constant as written (adjacent constants run together, as Spark reads
    them), or None when ``arg`` isn't one."""
    try:
        node = sqlglot.parse_one(arg, read="spark")
    except (ParseError, TokenError):
        return None
    parts = node.expressions if isinstance(node, exp.Concat) else [node]
    if not all(isinstance(p, exp.Literal) and p.is_string for p in parts):
        return None
    return "".join(p.name for p in parts)


# Text that isn't one JSON document is read as Spark's Jackson reads it: its first value (an object or
# array ending at the earliest closing bracket that makes it valid), any text after that ignored; text with
# no such value is NULL.
_FIRST_JSON = ("CASE WHEN JSON_VALID(fj_arg0) THEN fj_arg0 ELSE (LIST_TRANSFORM(LIST_FILTER("
               "RANGE(1, LENGTH(fj_arg0) + 1), fj_i -> SUBSTRING(fj_arg0, fj_i, 1) IN ('}', ']') "
               "AND JSON_VALID(LEFT(fj_arg0, fj_i)) AND REGEXP_MATCHES(fj_arg0, '^\\s*[\\{\\[]')), "
               "fj_i -> LEFT(fj_arg0, fj_i)))[1] END")


# get_json_object with one [*] in its path, as Spark's JsonPathParser writes what it matches: nothing is NULL,
# one match is that value's JSON text, and more are a JSON array of them. Where Spark's answer for one string
# or a null among the matches isn't sure, it is refused.
_JSON_WILDCARD_STOP = ("'get_json_object can''t run on this PC the way it runs in Fabric, because its [*] path "
                       "matched a string on its own, or a null, which Spark writes its own way, so nothing ran. Read "
                       "the values with from_json, or use a path to one element.'")
_JSON_WILDCARD = ("(LIST_TRANSFORM([json_extract(fj_arg0, kf_path)], kf_m -> CASE WHEN LEN(kf_m) = 0 THEN NULL "
                  "WHEN LIST_CONTAINS(LIST_TRANSFORM(kf_m, kf_x -> json_type(kf_x)), 'NULL') "
                  f"OR (LEN(kf_m) = 1 AND json_type(kf_m[1]) = 'VARCHAR') THEN ERROR({_JSON_WILDCARD_STOP}) "
                  "WHEN LEN(kf_m) = 1 THEN CAST(kf_m[1] AS VARCHAR) "
                  "ELSE '[' || ARRAY_TO_STRING(LIST_TRANSFORM(kf_m, kf_x -> CAST(kf_x AS VARCHAR)), ',') || ']' END))[1]")


def _spark_json_paths(statement: exp.Expression, databricks: bool = False) -> None:
    """get_json_object(json, path) with a path written as text, as Spark reads both: the path by Spark's
    rules (one Spark doesn't read, such as 'a' or '', is NULL, where DuckDB reads its own way), and the
    text through _FIRST_JSON (DuckDB stops on text that isn't JSON; Spark gives NULL)."""
    for node in list(statement.find_all(exp.JSONExtractScalar)):
        args = node.meta.get("fd_spark_args")
        if node.meta.get("fd_spark_name") != "get_json_object" or not args or len(args) != 2:
            continue
        written = _written_text(args[1])
        if written is None:
            continue
        path = spark_json_path(written, databricks)
        if path is not None and (path.count("[*]") > 1 or ".*" in path):
            continue
        text = _with_args(_FIRST_JSON, [node.this])
        new: exp.Expression
        if path is not None and "[*]" in path:
            new = _with_args(_JSON_WILDCARD.replace("kf_path", _sql_string(path)), [text])
        elif path is None:
            new = exp.Cast(this=exp.Null(), to=exp.DataType.build("VARCHAR"))
        else:
            new = exp.JSONExtractScalar(this=text, expression=exp.Literal.string(path))
        new.meta.update(spark_sql_names.kept(node))
        node.replace(new)


# Where an Identifier named like the index is a name, not a use of the index.
_NOT_A_REFERENCE = {("expression", exp.Dot), ("expressions", exp.Lambda), ("alias", exp.Alias),
                    ("this", exp.TableAlias), ("columns", exp.TableAlias), ("this", exp.PropertyEQ)}


def _spark_lambdas(statement: exp.Expression) -> None:
    """transform and filter with an index lambda count the index from 0 in Spark and from 1 in DuckDB,
    so the index becomes (i - 1) in the lambda's body; a nested lambda that reuses the name is refused
    rather than guessed. get_json_object with a [*] path returns JSON text in Spark and a list here,
    so it is refused (#1325)."""
    for node in list(statement.find_all(exp.JSONExtractScalar)):
        path = node.expression
        if isinstance(path, exp.JSONPath) and any(
                isinstance(p, exp.JSONPathWildcard) or isinstance(p.args.get("this"), exp.JSONPathWildcard)
                for p in path.expressions):
            raise ValueError("get_json_object with a [*] or .* path can't run on this PC the way it runs in "
                             "Databricks (it returns JSON text there), so nothing ran. Run it on the warehouse, or "
                             "read the values with from_json or a path to one element.")
    for call in list(statement.find_all(exp.Transform, exp.ArrayFilter)):
        func = call.expression
        if not isinstance(func, exp.Lambda) or len(func.expressions) != 2:
            continue
        index = func.expressions[1].name.lower()
        body = func.this
        if any(isinstance(inner, exp.Lambda) and any(e.name.lower() == index for e in inner.expressions)
               for inner in body.find_all(exp.Lambda)):
            raise ValueError("A lambda inside transform or filter reuses the index name, so the index can't be "
                             "counted from 0 here as Databricks counts it. Give the inner lambda's argument another "
                             "name.")
        for ident in list(body.find_all(exp.Identifier)):
            parent = ident.parent
            if ident.name.lower() != index or (isinstance(parent, exp.Column) and parent.table):
                continue
            if isinstance(parent, exp.Column):
                if parent.this is not ident:
                    continue
                target = parent
            elif (ident.arg_key, type(parent)) in _NOT_A_REFERENCE:
                continue   # a struct field (x.i), a lambda's own argument, an alias or a struct key
            else:
                target = ident
            target.replace(exp.paren(exp.Sub(this=ident.copy(), expression=exp.Literal.number(1))))


_T = exp.DataType.Type
_JSON_INTS = {_T.TINYINT: "TINYINT", _T.SMALLINT: "SMALLINT", _T.INT: "INTEGER", _T.BIGINT: "BIGINT"}
_JSON_NUMBER = "json_type({v}) IN ('BIGINT', 'UBIGINT', 'DOUBLE')"


def _from_json_value(v: str, dtype: exp.DataType, depth: int) -> str:
    """DuckDB SQL reading the JSON value ``v`` into ``dtype`` as Spark's JSON reader does: a whole
    number for an integer type, any number for DOUBLE and DECIMAL, true/false for BOOLEAN, a string
    for DATE and TIMESTAMP, anything for STRING (an object or array as its JSON text); a value
    that doesn't fit is NULL. A type it can't read is refused."""
    t = dtype.this
    if t in _JSON_INTS:
        return f"CASE WHEN json_type({v}) IN ('BIGINT', 'UBIGINT') THEN TRY_CAST(({v})::VARCHAR AS {_JSON_INTS[t]}) END"
    if t in (_T.DOUBLE, _T.FLOAT):
        # Spark's allowNonNumericNumbers (on by default) also reads these strings.
        name = "DOUBLE" if t == _T.DOUBLE else "FLOAT"
        return (f"CASE WHEN {_JSON_NUMBER.format(v=v)} THEN TRY_CAST(({v})::VARCHAR AS {name}) "
                f"WHEN json_type({v}) = 'VARCHAR' THEN CASE ({v}) ->> '$' WHEN 'NaN' THEN 'NaN'::{name} "
                f"WHEN '+INF' THEN 'Infinity'::{name} WHEN '+Infinity' THEN 'Infinity'::{name} "
                f"WHEN 'Infinity' THEN 'Infinity'::{name} WHEN '-INF' THEN '-Infinity'::{name} "
                f"WHEN '-Infinity' THEN '-Infinity'::{name} END END")
    if t == _T.DECIMAL:
        # A number, or a string read as Spark's en-US decimal parser reads it (commas dropped);
        # either rounds half-up to the scale, and too many digits is NULL.
        params = [int(p.name) for p in dtype.expressions] or [10]
        precision, scale = params[0], (params[1] if len(params) > 1 else 0)
        return (f"CASE WHEN {_JSON_NUMBER.format(v=v)} THEN "
                f"TRY_CAST(({v})::VARCHAR AS DECIMAL({precision}, {scale})) "
                f"WHEN json_type({v}) = 'VARCHAR' AND ({v}) ->> '$' <> '' THEN "
                f"TRY_CAST(REPLACE(({v}) ->> '$', ',', '') AS DECIMAL({precision}, {scale})) END")
    if t in (_T.TEXT, _T.VARCHAR):
        return f"CASE json_type({v}) WHEN 'VARCHAR' THEN ({v}) ->> '$' WHEN 'NULL' THEN NULL ELSE ({v})::VARCHAR END"
    if t == _T.BOOLEAN:
        return f"CASE WHEN json_type({v}) = 'BOOLEAN' THEN ({v})::VARCHAR = 'true' END"
    if t == _T.DATE:
        return f"CASE WHEN json_type({v}) = 'VARCHAR' THEN TRY_CAST(({v}) ->> '$' AS DATE) END"
    if t == _T.TIMESTAMPTZ:  # Databricks' TIMESTAMP
        # An offset in the text is honoured, as Spark does; text without one is read in the
        # session time zone.
        # A whole number is seconds since 1970-01-01 UTC, as Spark reads it.
        return (f"CASE WHEN json_type({v}) = 'VARCHAR' THEN TRY_CAST(({v}) ->> '$' AS TIMESTAMPTZ) "
                f"WHEN json_type({v}) IN ('BIGINT', 'UBIGINT') THEN TRY(TO_TIMESTAMP(({v})::VARCHAR::BIGINT)) END")
    if t == _T.ARRAY and len(dtype.expressions) == 1:
        element = f"fj_e{depth}"
        inner = _from_json_value(element, dtype.expressions[0], depth + 1)
        return f"CASE WHEN json_type({v}) = 'ARRAY' THEN LIST_TRANSFORM(CAST({v} AS JSON[]), {element} -> {inner}) END"
    if t == _T.STRUCT:
        return f"CASE WHEN json_type({v}) = 'OBJECT' THEN {_from_json_struct(v, dtype, depth)} END"
    raise ValueError(f"from_json can't read a {dtype.sql('databricks')} field on this PC, so nothing ran. "
                     "Read it as STRING and convert it, or run the query on Databricks.")


def _json_fields(dtype: exp.DataType) -> list[tuple[str, exp.DataType]]:
    fields = []
    for field in dtype.expressions:
        if not isinstance(field, exp.ColumnDef) or not isinstance(field.args.get("kind"), exp.DataType):
            raise ValueError("from_json's schema here must name each field with its type, so nothing ran.")
        name = field.name
        if not name or '"' in name or "\\" in name:
            raise ValueError(f"from_json can't read the field {name!r} on this PC (a field name with a quote "
                             "or backslash), so nothing ran.")
        fields.append((name, field.args["kind"]))
    if not fields:
        raise ValueError("from_json's schema has no fields, so nothing ran.")
    return fields


def _sql_string(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _from_json_struct(v: str, dtype: exp.DataType, depth: int) -> str:
    """A struct with Spark's field names; each key is matched case-sensitively, as Spark does. Spark
    keeps the last of a repeated key and DuckDB's json_extract the first, so an object that repeats
    one of the schema's keys stops with a message rather than answering differently."""
    names = "[" + ", ".join(_sql_string(name) for name, _ in _json_fields(dtype)) + "]"
    keys = f"LIST_FILTER(json_keys({v}), fj_k{depth} -> LIST_CONTAINS({names}, fj_k{depth}))"
    return (f"CASE WHEN LEN({keys}) <> LIST_UNIQUE({keys}) THEN ERROR('from_json was given a JSON object that "
            "repeats a key of its schema; Databricks keeps the last value and this PC can''t, so nothing ran. "
            f"Remove the repeated key.') ELSE " + _from_json_fields(v, dtype, depth) + " END")


def _from_json_fields(v: str, dtype: exp.DataType, depth: int) -> str:
    return "{" + ", ".join(
        f"{_sql_string(name)}: " + _from_json_value(f"json_extract({v}, {_sql_string(chr(36) + '.' + chr(34) + name + chr(34))})",
                                                    kind, depth)
        for name, kind in _json_fields(dtype)) + "}"


def _from_json_schema(schema: exp.Expression) -> exp.DataType:
    """from_json's schema, written out as text: a type, or a struct's fields ('a INT, b STRING')."""
    if not (isinstance(schema, exp.Literal) and schema.is_string):
        raise ValueError("from_json's schema must be written out as text (such as 'a INT, b STRING') to run on "
                         "this PC, so nothing ran.")
    ddl = schema.name.strip()
    try:
        try:
            return exp.DataType.build(ddl, dialect="databricks")
        except (ParseError, TokenError, ValueError):
            return exp.DataType.build(f"STRUCT<{ddl}>", dialect="databricks")
    except (ParseError, TokenError, ValueError) as exc:
        raise ValueError(f"from_json's schema {ddl!r} couldn't be read here, so nothing ran.") from exc


def _from_json_template(schema: exp.Expression) -> str:
    """DuckDB SQL for Spark's from_json(text, schema) with ``fj_arg0`` standing for the text. NULL or
    blank text gives NULL; text that isn't JSON gives a struct of NULLs (Spark's PERMISSIVE mode)."""
    dtype = _from_json_schema(schema)
    if dtype.this == _T.STRUCT:
        nulls = "{" + ", ".join(f"{_sql_string(name)}: CAST(NULL AS {duckdb_sql(kind)})"
                                for name, kind in _json_fields(dtype)) + "}"
        body = (f"CASE WHEN fj_j IS NULL THEN {nulls} "
                "WHEN json_type(fj_j) = 'ARRAY' THEN error('from_json with a struct schema was given a JSON array, "
                "which this PC does not read the way Databricks does. Use an ARRAY<STRUCT<...>> schema.') "
                f"WHEN json_type(fj_j) = 'OBJECT' THEN {_from_json_struct('fj_j', dtype, 0)} ELSE {nulls} END")
    elif dtype.this == _T.ARRAY:
        body = _from_json_value("fj_j", dtype, 0)
    else:
        raise ValueError(f"from_json reads a STRUCT or ARRAY schema on this PC, not {dtype.sql('databricks')}, "
                         "so nothing ran.")
    return ("(LIST_TRANSFORM([CAST(fj_arg0 AS VARCHAR)], fj_t -> CASE WHEN fj_t IS NULL OR TRIM(fj_t) = '' THEN NULL "
            f"ELSE (LIST_TRANSFORM([TRY_CAST(fj_t AS JSON)], fj_j -> {body}))[1] END))[1]")


# Spark 3.5's from_json (JsonToStructs over JacksonParser and FailureSafeParser), for spark.sql and %%sql
# cells in Spark SQL. Databricks SQL keeps _from_json_template, which refuses options.
_FROM_JSON_MODES = ("PERMISSIVE", "FAILFAST")
# Options that change how text is read: taken only at Spark's default, refused by name otherwise
_FROM_JSON_DEFAULTS = {"allowComments": "false", "allowUnquotedFieldNames": "false",
                       "allowNumericLeadingZeros": "false", "allowBackslashEscapingAnyCharacter": "false",
                       "allowNonNumericNumbers": "true", "allowUnquotedControlChars": "false"}
# Options that only steer schema inference, which from_json doesn't do: read, and change nothing
_FROM_JSON_INFERENCE = ("prefersDecimal", "primitivesAsString", "dropFieldIfAllNull", "samplingRatio")
# Text in single quotes isn't JSON here, whichever way allowSingleQuotes is set (WR-118: listed in the docs)
_FROM_JSON_IGNORED = ("allowSingleQuotes",)
_FROM_JSON_TAKEN = ("mode", "columnNameOfCorruptRecord", "dateFormat", "timestampFormat")
_FAILFAST = ("[MALFORMED_RECORD_IN_PARSING] from_json found a record it couldn't read, and with mode FAILFAST "
             "Spark stops on it too. Use mode PERMISSIVE to get NULLs instead.")


def _from_json_options(node: exp.Expression) -> dict[str, str]:
    """from_json's options map, as Spark reads it: names in any case, the mode in any case, and an unknown
    mode read as PERMISSIVE. An option that changes how text is read away from Spark's default is refused."""
    given = node.args.get("options")
    if given is None:
        return {"mode": "PERMISSIVE"}
    keys, values = given.args.get("keys"), given.args.get("values")
    pairs = list(zip(keys.expressions, values.expressions, strict=True)) \
        if isinstance(keys, exp.Array) and isinstance(values, exp.Array) else None
    if pairs is None or not all(isinstance(p, exp.Literal) and p.is_string for pair in pairs for p in pair):
        raise ValueError("from_json's options must be written out as map('name', 'value', ...) to run on this PC, "
                         "so nothing ran. Write each option as text, such as map('mode', 'FAILFAST').")
    out: dict[str, str] = {}
    known = (*_FROM_JSON_TAKEN, *_FROM_JSON_INFERENCE, *_FROM_JSON_IGNORED, *_FROM_JSON_DEFAULTS)
    for key, value in pairs:
        name = next((o for o in known if o.lower() == key.name.lower()), None)
        if name is None or (name in _FROM_JSON_DEFAULTS and value.name.strip().lower() != _FROM_JSON_DEFAULTS[name]):
            raise ValueError(f"from_json's {key.name!r} option isn't read on this PC, so nothing ran. Leave it out, "
                             "or run the cell in Fabric.")
        out[name] = value.name
    mode = out.get("mode", "PERMISSIVE").upper()
    if mode == "DROPMALFORMED":
        raise ValueError("from_json doesn't take the DROPMALFORMED mode, as in Spark (it takes PERMISSIVE and "
                         "FAILFAST), so nothing ran. Use mode PERMISSIVE or FAILFAST.")
    out["mode"] = mode if mode in _FROM_JSON_MODES else "PERMISSIVE"
    return out


def _failed(j: str, value: str) -> str:
    """Whether the JSON value ``j`` failed to read: it isn't null, and what it read as is."""
    return f"(json_type({j}) <> 'NULL' AND {value} IS NULL)"


_NEW_PARSER = ("'[INCONSISTENT_BEHAVIOR_CROSS_VERSION.PARSE_DATETIME_BY_NEW_PARSER] You may get a different "
               "result due to the upgrading to Spark >= 3.0: Fail to parse ''' || {s} || ''' in the new parser. "
               "You can set \"spark.sql.legacy.timeParserPolicy\" to \"LEGACY\" to restore the behavior before "
               "Spark 3.0, or set to \"CORRECTED\" and treat it as an invalid datetime string.'")


def _spark_json_date(v: str, options: dict[str, str], depth: int, timestamp: bool) -> str:
    """A date or timestamp as Spark reads one from JSON text. With no dateFormat, text Spark's own date
    reading takes (spark_text_date); text that isn't a date but that Spark 2's parser reads as one (a day or
    month past its range, as 2024-02-30) stops with Spark's INCONSISTENT_BEHAVIOR error. With a format, text
    that doesn't match is NULL when it has no digit and the pattern only numbers, and otherwise refused, since
    whether Spark gives NULL or stops depends on Spark 2's parser."""
    s = f"fj_s{depth}"
    option = "timestampFormat" if timestamp else "dateFormat"
    pattern = options.get(option)
    if pattern is None and timestamp:
        return _from_json_value(v, exp.DataType.build("TIMESTAMPTZ"), depth)
    if pattern is None:
        read = spark_builtins_sql.spark_text_date(s)
        legacy = f"REGEXP_MATCHES({s}, '^[+-]?[0-9]+-[+-]?[0-9]+-[+-]?[0-9]+')"
        body = f"CASE WHEN {read} IS NOT NULL THEN {read} WHEN {legacy} THEN ERROR({_NEW_PARSER.format(s=s)}) END"
    else:
        fn = "to_timestamp" if timestamp else "to_date"
        read = spark_datetime.parse_sql(pattern, fn).replace("fj_arg0", s)
        if not timestamp:
            read = f"CAST({read} AS DATE)"
        stop = exp.Literal.string(f"from_json was given text that its {option} {pattern!r} doesn't match. Spark "
                                  "gives NULL or stops on such text depending on how its older parser reads it, and "
                                  "this PC can't tell which, so nothing ran. Make the text match the format, or run "
                                  "the cell in Fabric.").sql("duckdb")
        # Spark 2's parser reads no text without a digit when every field of the pattern is a number, so
        # Spark gives NULL for that text; other text that doesn't match is refused.
        letters = re.sub(r"'[^']*'", "", pattern)
        numeric = re.fullmatch(r"(?:[yuMdHhKkmsSD]|[^A-Za-z])*", letters) is not None and "MMM" not in letters
        null = f"NOT REGEXP_MATCHES({s}, '[0-9]') OR " if numeric else ""
        body = f"CASE WHEN {read} IS NOT NULL THEN {read} WHEN {null}{s} = '' THEN NULL ELSE ERROR({stop}) END"
    return f"CASE WHEN json_type({v}) = 'VARCHAR' THEN (LIST_TRANSFORM([({v}) ->> '$'], {s} -> {body}))[1] END"


def _spark_json_value(v: str, dtype: exp.DataType, options: dict[str, str], depth: int) -> str:
    """DuckDB SQL reading the JSON value ``v`` into ``dtype`` as Spark 3.5 does: NULL for null, and for a
    value that fails to read. An array or a map with an element that fails is NULL as a whole; a struct
    keeps its other fields (Spark's partial results)."""
    t = dtype.this
    if t == _T.DATE:
        return _spark_json_date(v, options, depth, timestamp=False)
    if t == _T.TIMESTAMPTZ:
        return _spark_json_date(v, options, depth, timestamp=True)
    if t == _T.ARRAY and len(dtype.expressions) == 1:
        e, x, items = f"fj_e{depth}", f"fj_x{depth}", f"fj_l{depth}"
        inner = _spark_json_value(e, dtype.expressions[0], options, depth + 1)
        read = f"LIST_TRANSFORM(CAST({v} AS JSON[]), {e} -> {{'j': {e}, 'v': {inner}}})"
        return (f"CASE WHEN json_type({v}) = 'ARRAY' THEN (LIST_TRANSFORM([{read}], {items} -> CASE WHEN "
                f"LIST_BOOL_OR(LIST_TRANSFORM({items}, {x} -> {_failed(f'{x}.j', f'{x}.v')})) THEN NULL "
                f"ELSE LIST_TRANSFORM({items}, {x} -> {x}.v) END))[1] END")
    if t == _T.MAP and len(dtype.expressions) == 2:
        key, value = dtype.expressions
        if key.this not in (_T.TEXT, _T.VARCHAR):
            raise ValueError(f"from_json reads a MAP with STRING keys, as Spark does, not {dtype.sql('spark')}, "
                             "so nothing ran. Use MAP<STRING, ...>.")
        m, e, x, items = f"fj_m{depth}", f"fj_e{depth}", f"fj_x{depth}", f"fj_l{depth}"
        inner = _spark_json_value(e, value, options, depth + 1)
        read = f"LIST_TRANSFORM({m}.j, {e} -> {{'j': {e}, 'v': {inner}}})"
        # A key the object repeats keeps its last value, as Spark's map builder does: entry i is kept when
        # no later entry has its key.
        i = f"fj_i{depth}"
        last = (f"LIST_FILTER(RANGE(1, LEN({m}.k) + 1), {i} -> NOT LIST_CONTAINS("
                f"LIST_SLICE({m}.k, {i} + 1, LEN({m}.k)), LIST_EXTRACT({m}.k, {i})))")
        body = (f"(LIST_TRANSFORM([{read}], {items} -> CASE WHEN LIST_BOOL_OR(LIST_TRANSFORM({items}, "
                f"{x} -> {_failed(f'{x}.j', f'{x}.v')})) THEN NULL ELSE MAP("
                f"LIST_TRANSFORM({last}, {i} -> LIST_EXTRACT({m}.k, {i})), "
                f"LIST_TRANSFORM({last}, {i} -> LIST_EXTRACT({items}, {i}).v)) END))[1]")
        return (f"CASE WHEN json_type({v}) = 'OBJECT' THEN (LIST_TRANSFORM([{{'k': json_keys({v}), "
                f"'j': json_extract({v}, '$.*')}}], {m} -> {body}))[1] END")
    if t == _T.STRUCT:
        return f"CASE WHEN json_type({v}) = 'OBJECT' THEN {_spark_json_struct(v, dtype, options, depth)} END"
    return _from_json_value(v, dtype, depth)


def _field(v: str, name: str) -> str:
    return f"json_extract({v}, {_sql_string(chr(36) + '.' + chr(34) + name + chr(34))})"


def _spark_json_struct(v: str, dtype: exp.DataType, options: dict[str, str], depth: int,
                       corrupt: tuple[str, str] | None = None) -> str:
    """A struct read from the object ``v``, keys matched case-sensitively; one that repeats a key of the
    schema stops (_from_json_struct). ``corrupt``: (the corrupt-record field, the SQL it holds)."""
    fields = _json_fields(dtype)
    names = "[" + ", ".join(_sql_string(name) for name, _ in fields) + "]"
    keys = f"LIST_FILTER(json_keys({v}), fj_k{depth} -> LIST_CONTAINS({names}, fj_k{depth}))"
    parts = [f"{_sql_string(name)}: " + (corrupt[1] if corrupt and name == corrupt[0]
                                         else _spark_json_value(_field(v, name), kind, options, depth))
             for name, kind in fields]
    return (f"CASE WHEN LEN({keys}) <> LIST_UNIQUE({keys}) THEN ERROR('from_json was given a JSON object that "
            "repeats a key of its schema, which this PC can''t read as Spark does, so nothing ran. Remove the "
            "repeated key.') ELSE {" + ", ".join(parts) + "} END")


def _spark_json_clean(v: str, dtype: exp.DataType, options: dict[str, str], depth: int, skip: str = "") -> str:
    """Whether every part of the JSON value ``v`` reads into ``dtype``: what makes a record corrupt in Spark,
    and what FAILFAST stops on. ``skip``: the corrupt-record field, which isn't read."""
    t = dtype.this
    e = f"fj_c{depth}"
    if t == _T.ARRAY and len(dtype.expressions) == 1:
        each = _spark_json_clean(e, dtype.expressions[0], options, depth + 1)
        whole = f"json_type({v}) = 'ARRAY' AND COALESCE(LIST_BOOL_AND(LIST_TRANSFORM(CAST({v} AS JSON[]), {e} -> {each})), TRUE)"
    elif t == _T.MAP and len(dtype.expressions) == 2:
        each = _spark_json_clean(e, dtype.expressions[1], options, depth + 1)
        whole = (f"json_type({v}) = 'OBJECT' AND COALESCE(LIST_BOOL_AND(LIST_TRANSFORM(CAST(json_extract({v}, '$.*') AS JSON[]), "
                 f"{e} -> {each})), TRUE)")
    elif t == _T.STRUCT:
        whole = " AND ".join([f"json_type({v}) = 'OBJECT'"] + [
            _spark_json_clean(_field(v, name), kind, options, depth + 1)
            for name, kind in _json_fields(dtype) if name != skip])
    else:
        whole = f"NOT {_failed(v, _spark_json_value(v, dtype, options, depth))}"
    return f"({v} IS NULL OR json_type({v}) = 'NULL' OR ({whole}))"


def _spark_from_json_template(schema: exp.Expression, node: exp.Expression) -> tuple[str, exp.DataType]:
    """DuckDB SQL for Spark 3.5's from_json(text, schema, options), ``fj_arg0`` standing for the text, and
    its type. NULL or blank text gives NULL. In PERMISSIVE mode a record that doesn't read (text that isn't
    JSON, or not the schema's kind, or a part that fails) gives a struct of NULLs, or what parts read, with the
    whole text in the corrupt-record field when the schema has one; for an array or map schema it gives NULL.
    FAILFAST stops on such a record."""
    options = _from_json_options(node)
    dtype = _from_json_schema(schema)
    failfast = options["mode"] == "FAILFAST"
    j = "fj_j"
    if dtype.this == _T.STRUCT:
        fields = _json_fields(dtype)
        name = options.get("columnNameOfCorruptRecord", "_corrupt_record")
        corrupt = next((kind for field, kind in fields if field == name), None)
        if corrupt is not None and corrupt.this not in (_T.TEXT, _T.VARCHAR):
            raise ValueError(f"from_json's {name} field holds the records it can't read, and Spark requires it to be "
                             f"a STRING, so nothing ran. Declare {name} as STRING.")
        clean = _spark_json_clean(j, dtype, options, 0, skip=name) if corrupt is not None or failfast else "TRUE"
        nulls = "{" + ", ".join(f"{_sql_string(field)}: " + ("fj_t" if field == name else f"CAST(NULL AS {duckdb_sql(kind)})")
                                for field, kind in fields) + "}"
        record = _spark_json_struct(j, dtype, options, 0,
                                    (name, f"CASE WHEN {clean} THEN NULL ELSE fj_t END") if corrupt is not None else None)
        body = f"CASE WHEN json_type({j}) = 'OBJECT' THEN {record} ELSE {nulls} END"
        bad = f"{j} IS NULL OR json_type({j}) <> 'OBJECT' OR NOT {clean}"
    elif dtype.this in (_T.ARRAY, _T.MAP):
        if dtype.this == _T.ARRAY and dtype.expressions and dtype.expressions[0].this == _T.STRUCT:
            # Spark reads an object on its own as an array of that one struct
            j = "(CASE WHEN json_type(fj_j) = 'OBJECT' THEN CAST('[' || CAST(fj_j AS VARCHAR) || ']' AS JSON) ELSE fj_j END)"
        body = _spark_json_value(j, dtype, options, 0)
        kind = "ARRAY" if dtype.this == _T.ARRAY else "OBJECT"
        bad = f"{j} IS NULL OR json_type({j}) <> '{kind}' OR NOT {_spark_json_clean(j, dtype, options, 0)}"
    else:
        raise ValueError(f"from_json reads a STRUCT, ARRAY or MAP schema, as Spark does, not {dtype.sql('spark')}, "
                         "so nothing ran. Use a STRUCT, ARRAY or MAP schema, such as STRUCT<v: INT>.")
    if failfast:
        body = f"CASE WHEN {bad} THEN ERROR({_sql_string(_FAILFAST)}) ELSE {body} END"
    template = ("(LIST_TRANSFORM([CAST(fj_arg0 AS VARCHAR)], fj_t -> CASE WHEN fj_t IS NULL OR TRIM(fj_t) = '' THEN NULL "
                f"ELSE (LIST_TRANSFORM([TRY_CAST(fj_t AS JSON)], fj_j -> {body}))[1] END))[1]")
    return f"CAST({template} AS {duckdb_sql(dtype)})", dtype


# str_to_map(text, pairDelim, keyValueDelim): both delimiters are regular expressions, as in Spark;
# each pair splits at its first key-value delimiter, and a pair without one is a key with NULL.
_STR_TO_MAP = ("(LIST_TRANSFORM([{'s': CAST(fj_arg0 AS VARCHAR), 'p': CAST(fj_arg1 AS VARCHAR), "
               "'k': CAST(fj_arg2 AS VARCHAR)}], fj_m -> CASE WHEN fj_m.s IS NULL OR fj_m.p IS NULL OR fj_m.k IS NULL "
               "THEN NULL ELSE MAP_FROM_ENTRIES(LIST_TRANSFORM(STRING_SPLIT_REGEX(fj_m.s, fj_m.p), fj_q -> "
               "{'k': CASE WHEN REGEXP_MATCHES(fj_q, fj_m.k) THEN REGEXP_EXTRACT(fj_q, '(?s)^(.*?)(?:' || fj_m.k || ')', 1) "
               "ELSE fj_q END, 'v': CASE WHEN REGEXP_MATCHES(fj_q, fj_m.k) "
               "THEN REGEXP_REPLACE(fj_q, '(?s)^.*?(?:' || fj_m.k || ')', '') END})) END))[1]")


def _with_args(template: str, args: list[exp.Expression]) -> exp.Expression:
    """``template`` read as DuckDB SQL, with the person's own argument nodes put in place of fj_arg0,
    fj_arg1... Each argument sits outside every lambda in the template, so no name in it can be taken
    for one of the template's, and it is still rewritten by the later passes."""
    tree = cast(exp.Expression, sqlglot.parse_one(template, read="duckdb"))
    for column in list(tree.find_all(exp.Column)):
        if column.name.startswith("fj_arg") and not column.table:
            column.replace(args[int(column.name[6:])].copy())
    return tree


# Casts to a whole-number type (EA-003). DuckDB rounds a fraction (3.7 -> 4) where Spark truncates (3), and
# reads text such as '1.9' and '1e2' that Spark reads differently, so these casts are written out:
# whole-number values cast as they are; a DOUBLE or FLOAT is truncated; text is read as Spark reads it (spaces trimmed, then digits, and
# with Spark's CAST with ANSI off a fraction that is dropped; anything else is NULL, or an error for
# Databricks SQL's ANSI CAST); a number DuckDB writes in plain digits keeps its whole part; anything else
# (a number in exponent form, which is whole or below 0.0001, or a boolean) is DuckDB's own cast.
_INT_CASTS = {exp.DataType.Type.TINYINT: ("TINYINT", "TINYINT"), exp.DataType.Type.SMALLINT: ("SMALLINT", "SMALLINT"),
              exp.DataType.Type.INT: ("INTEGER", "INT"), exp.DataType.Type.BIGINT: ("BIGINT", "BIGINT")}
_WHOLE_TYPES = ("'TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'HUGEINT', 'UTINYINT', 'USMALLINT', 'UINTEGER', "
                "'UBIGINT', 'UHUGEINT'")
_TIMESTAMP_TYPES = "'TIMESTAMP', 'TIMESTAMP WITH TIME ZONE', 'TIMESTAMP_S', 'TIMESTAMP_MS', 'TIMESTAMP_NS'"
_EPOCH_LIMIT = "9.2e12"  # about the seconds a DuckDB timestamp reaches either side of 1970 (year 294247)
_TIMESTAMP_CASTS = (exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPTZ, exp.DataType.Type.TIMESTAMPLTZ,
                    exp.DataType.Type.TIMESTAMPNTZ)
_TEXT_CASTS = (exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR)
_PLAIN_NUMBER = r"^\s*([+-]?[0-9]+)(\.[0-9]*)?\s*$"
_WHOLE_NUMBER = r"^\s*([+-]?[0-9]+)\s*$"
_WHOLE_PART = r"^\s*([+-]?[0-9]+)"  # a decimal's digits before its point, as DuckDB writes it


def spark_integer_cast(value: str, duck_type: str, spark_type: str, mode: str) -> str:
    """DuckDB SQL casting ``value`` (DuckDB SQL) to a whole-number type as Spark does. ``mode`` is
    "legacy" (Spark's CAST with ANSI off), "try" (try_cast: ANSI rules, NULL when it can't) or "ansi"
    (Databricks SQL's CAST, which stops on text that isn't a whole number)."""
    text = f"CAST({value} AS VARCHAR)"
    cast_ = "CAST" if mode == "ansi" else "TRY_CAST"
    if mode == "ansi":
        message = (f"'[CAST_INVALID_INPUT] The value ''' || left({text}, 200) || ''' of the type \"STRING\" cannot be cast to "
                   f"\"{spark_type}\" because it is malformed. Use try_cast to tolerate malformed input and return "
                   "NULL instead.'")
        string = (f"CASE WHEN regexp_matches({text}, '{_WHOLE_NUMBER}') THEN CAST(regexp_extract({text}, "
                  f"'{_WHOLE_NUMBER}', 1) AS {duck_type}) ELSE ERROR({message}) END")
    else:
        pattern = _PLAIN_NUMBER if mode == "legacy" else _WHOLE_NUMBER
        string = f"TRY_CAST(NULLIF(regexp_extract({text}, '{pattern}', 1), '') AS {duck_type})"
    # A timestamp is its seconds since 1970 (UTC), rounded down, as in Spark (EA-014); DuckDB has no such
    # cast. It is read through text, so the CASE binds whatever the value's type.
    seconds = f"floor(epoch(TRY_CAST({text} AS TIMESTAMPTZ)))"
    if mode == "legacy":  # a number that doesn't fit wraps around or saturates, as Java's casts do (_java_narrowed)
        whole = _java_narrowed(f"TRY_CAST({value} AS HUGEINT)", duck_type)
        stamp = _java_narrowed(f"TRY_CAST({seconds} AS HUGEINT)", duck_type)
        double = _java_narrowed(f"TRY_CAST({value} AS DOUBLE)", duck_type, from_double=True)
        whole_part = f"TRY_CAST(regexp_extract({text}, '{_WHOLE_PART}', 1) AS HUGEINT)"
        decimal = (f"WHEN typeof({value}) LIKE 'DECIMAL%' THEN "
                   f"{_java_narrowed(whole_part, duck_type)} ")
    else:
        whole = f"{cast_}({value} AS {duck_type})"
        stamp = f"{cast_}({seconds} AS {duck_type})"
        double = f"{cast_}(trunc(TRY_CAST({value} AS DOUBLE)) AS {duck_type})"
        decimal = ""
    return (f"CASE WHEN typeof({value}) IN ({_WHOLE_TYPES}) THEN {whole} "
            f"WHEN typeof({value}) IN ({_TIMESTAMP_TYPES}) THEN {stamp} "
            f"WHEN typeof({value}) IN ('DOUBLE', 'FLOAT') THEN {double} "
            f"{decimal}"
            f"WHEN typeof({value}) = 'VARCHAR' THEN {string} "
            f"WHEN regexp_matches({text}, '{_PLAIN_NUMBER}') THEN {cast_}(regexp_extract({text}, '{_PLAIN_NUMBER}', 1) "
            f"AS {duck_type}) ELSE {cast_}({value} AS {duck_type}) END")


_WIDTHS = {"TINYINT": 8, "SMALLINT": 16, "INTEGER": 32, "BIGINT": 64}


def _java_narrowed(number: str, duck_type: str, from_double: bool = False) -> str:
    """DuckDB SQL for ``number`` (a HUGEINT, or a DOUBLE with ``from_double``) cast to ``duck_type`` as
    Spark's CAST does with ANSI mode off, which is Java's: a whole number keeps its low bits, so
    CAST(2147483648L AS INT) is -2147483648 (Spark's ANSI compliance page); a double is cut to a whole
    number, NaN is 0, and one past the range is the nearest end (Java's d2i and d2l), and for a
    tinyint or smallint that int then keeps its low bits."""
    bits = _WIDTHS[duck_type]
    if from_double:
        top = 64 if bits == 64 else 32
        high, low = f"{2 ** (top - 1) - 1}", f"-{2 ** (top - 1)}"
        number = (f"CASE WHEN isnan({number}) THEN 0 WHEN {number} >= {high}.0 THEN {high} WHEN {number} <= {low}.0 "
                  f"THEN {low} ELSE CAST(trunc({number}) AS HUGEINT) END")
    half, size = 2 ** (bits - 1), 2 ** bits
    return f"CAST(((CAST({number} AS HUGEINT) + {half}) % {size} + {size}) % {size} - {half} AS {duck_type})"


def spark_timestamp_cast(value: str, duck_type: str, mode: str) -> str:
    """DuckDB SQL casting ``value`` to a timestamp as Spark does (EA-014): a number is seconds since 1970
    (UTC), fractions kept; anything else is DuckDB's own cast. DuckDB can't cast a number to a timestamp,
    and its TRY_CAST gives NULL. ``mode`` as in spark_integer_cast."""
    cast_ = "CAST" if mode == "ansi" else "TRY_CAST"
    seconds = f"TRY_CAST(CAST({value} AS VARCHAR) AS DOUBLE)"
    if mode == "ansi":  # out of a timestamp's range stops the query, as Spark's ANSI cast does
        number = f"to_timestamp({seconds})"
    else:  # out of range, NaN or infinite is NULL
        number = (f"CASE WHEN {seconds} BETWEEN -{_EPOCH_LIMIT} AND {_EPOCH_LIMIT} THEN to_timestamp({seconds}) END")
    return (f"CASE WHEN typeof({value}) IN ({_WHOLE_TYPES}, 'DOUBLE', 'FLOAT') OR typeof({value}) LIKE 'DECIMAL%' "
            f"THEN CAST({number} AS {duck_type}) ELSE {cast_}({value} AS {duck_type}) END")


def _whole_number(node: exp.Expression) -> bool:
    """Plainly a whole number already: an integer literal or a cast to a whole-number type."""
    if isinstance(node, exp.Paren):
        return _whole_number(node.this)
    if isinstance(node, exp.Literal):
        return node.is_int
    if isinstance(node, exp.Window):  # row_number, rank, dense_rank and ntile count whole rows
        return isinstance(node.this, (exp.RowNumber, exp.Rank, exp.DenseRank, exp.Ntile))
    return isinstance(node, (exp.Cast, exp.TryCast)) and node.to.this in _INT_CASTS


# Spark's cast functions (int(x), bigint(x), double(x), ...) are CAST(x AS type) in Spark. sqlglot reads
# int(), double() and most others as a Cast, and bigint, smallint, tinyint, decimal and binary as plain calls,
# which spark_fixes makes casts so the casts below give Spark's value. binary(text) is its UTF-8 bytes
# (DuckDB's cast reads escapes and refuses non-ASCII). decimal(x) is DECIMAL(10, 0).
_CAST_FUNCTIONS = {"BIGINT": "BIGINT", "SMALLINT": "SMALLINT", "TINYINT": "TINYINT", "DECIMAL": "DECIMAL(10, 0)"}
_BINARY_FUNCTION = ("CASE WHEN typeof(fj_arg0) = 'VARCHAR' THEN ENCODE(CAST(fj_arg0 AS VARCHAR)) "
                    "WHEN typeof(fj_arg0) IN ('BLOB', '\"NULL\"') THEN TRY_CAST(fj_arg0 AS BLOB) "
                    "ELSE ERROR('binary() of type ' || typeof(fj_arg0) || ' can''t run on this PC the way it runs in "
                    "Fabric, so nothing ran: Spark writes its bytes. Run the cell in Fabric, or pass text.') END")


def _spark_cast_functions(statement: exp.Expression, legacy: bool = False) -> exp.Expression:
    """The cast functions as casts: Databricks SQL's CAST (ANSI, a Cast), or with ``legacy`` Spark's CAST
    with ANSI off (a TryCast, as sqlglot reads Spark's CAST), as spark_legacy_casts uses it. binary() is
    written inside spark_fixes only, after spark_builtins_sql.mark(), so its typeof stays DuckDB's."""
    kind = exp.TryCast if legacy else exp.Cast
    for call in list(statement.find_all(exp.Anonymous)):
        name = call.name.upper()
        if name == "BINARY" and len(call.expressions) == 1 and not legacy:  # inside spark_fixes, after mark()
            call.replace(exp.paren(_with_args(_BINARY_FUNCTION, [call.expressions[0]])))
        elif name in _CAST_FUNCTIONS and len(call.expressions) == 1:
            call.replace(kind(this=call.expressions[0].copy(),
                              to=exp.DataType.build(_CAST_FUNCTIONS[name], dialect="spark")))
    return statement


def spark_legacy_casts(statement: exp.Expression) -> exp.Expression:
    """Spark SQL and the DataFrame API only (ANSI off, as Fabric runs): int('1.9') is Spark's CAST, which
    gives 1. sqlglot reads Spark's CAST and :: as a TryCast, so a plain Cast came from a cast function
    (int(x), double(x), ...) or is a typed literal; a cast function's becomes a TryCast before spark_fixes. Databricks SQL is ANSI and
    doesn't use this. Casts to a timestamp are left to spark_legacy_times."""
    for node in list(statement.find_all(exp.Cast)):
        # A typed literal (DATE '2026-13-01') is a Cast too, but Spark refuses a bad one even with ANSI off,
        # so it stays a Cast. sqlglot tells them apart only by position: a cast function's Cast carries
        # its name's place in the query (tests/test_spark_builtins_sql.py pins it).
        if type(node) is exp.Cast and node.to.this not in _TIMESTAMPS and "start" in node.meta:
            replacement = exp.TryCast(this=node.this, to=node.to)
            replacement.meta.update(spark_sql_names.kept(node))  # double(x) keeps the name Spark gives it
            if node is statement:
                return _mark_date_casts(replacement)
            node.replace(replacement)
    return _mark_date_casts(_spark_cast_functions(statement, legacy=True))


def _mark_date_casts(statement: exp.Expression) -> exp.Expression:
    """Mark the casts to DATE the person wrote (CAST, ::, date()), so spark_legacy_times reads their text as
    Spark does; the rewrites in between write casts to DATE of their own, which keep DuckDB's cast."""
    for date_cast in statement.find_all(exp.TryCast):
        if date_cast.to.this == exp.DataType.Type.DATE:
            date_cast.meta[_SPARK_DATE_CAST] = True
    return statement


def spark_timestamp_text(value: str) -> str:
    """CAST(value AS VARCHAR), with a timestamp written as Spark writes it: the session zone's wall time
    with no offset. No lambdas, so the SQL reads back through sqlglot (Column.cast); ``value`` is
    evaluated once, since typeof is settled when DuckDB binds the query."""
    return (f"CASE WHEN typeof({value}) = 'TIMESTAMP WITH TIME ZONE' THEN "
            f"regexp_replace(CAST({value} AS VARCHAR), '[+-][0-9][0-9](:[0-9][0-9])?$', '') "
            f"ELSE CAST({value} AS VARCHAR) END")


_NOT_UTF8 = ("'A cast of binary to string can''t run on this PC the way it runs in Fabric, because the bytes "
             "aren''t UTF-8 text, which Spark reads with replacement characters, so nothing ran. Use hex() or "
             "base64() instead.'")


def spark_text_cast(value: str) -> str:
    """CAST(value AS STRING) as Spark writes it: a timestamp as the session zone's wall time with no
    offset ('2026-01-02 03:04:05', where DuckDB writes '+00' after it), a double or float as Java
    writes it ('1.0E20', where DuckDB writes '1e+20'), and binary as its UTF-8 text (bytes that aren't UTF-8
    are refused). Every other type casts as DuckDB casts it."""
    java = spark_builtins_sql.java_double_text
    return (f"CASE WHEN typeof({value}) = 'TIMESTAMP WITH TIME ZONE' THEN "
            f"regexp_replace(CAST({value} AS VARCHAR), '[+-][0-9][0-9](:[0-9][0-9])?$', '') "
            f"WHEN typeof({value}) = 'DOUBLE' THEN {java(f'TRY_CAST({value} AS DOUBLE)')} "
            f"WHEN typeof({value}) = 'FLOAT' THEN {java(f'TRY_CAST({value} AS FLOAT)')} "
            f"WHEN typeof({value}) = 'BLOB' THEN (LIST_TRANSFORM([TRY_CAST({value} AS BLOB)], lambda kf_bin: CASE WHEN kf_bin "
            f"IS NULL THEN NULL ELSE COALESCE(TRY(DECODE(kf_bin)), ERROR({_NOT_UTF8})) END))[1] "
            f"ELSE CAST({value} AS VARCHAR) END")


def _spark_concat_text(statement: exp.Expression) -> exp.Expression:
    """concat(...) and ||, when a part is a string literal, with each other part cast to STRING as
    Spark casts it (spark_text_cast): 'at ' || ts is 'at 2026-01-02 03:04:05', where DuckDB writes
    '+00' after the timestamp, and a double is '1.0E20', not '1e+20'. Only a concat with a string
    literal in it is surely of strings; one of columns alone may join arrays, and concat_ws may take
    an array, so those are left as they are."""
    def parts(node: exp.Expression, joins: set[int]) -> list[exp.Expression]:
        """The parts joined, through nested || and concat and their parentheses: 'a' || (b || t). The
        joins it reads through go in ``joins``; a join inside a function call is a part of its own."""
        if isinstance(node, (exp.DPipe, exp.Concat)):
            joins.add(id(node))
            inner = [node.this, node.expression] if isinstance(node, exp.DPipe) else node.expressions
            return [p for e in inner for p in parts(e, joins)]
        if isinstance(node, exp.Paren) and isinstance(node.this, (exp.DPipe, exp.Concat, exp.Paren)):
            return parts(node.this, joins)
        return [node]

    def literal(node: exp.Expression) -> bool:
        return isinstance(node, exp.Literal) and node.is_string

    done: set[int] = set()
    for node in list(statement.find_all(exp.Concat, exp.DPipe)):
        if id(node) in done:
            continue
        joins: set[int] = set()
        found = parts(node, joins)
        if not any(literal(p) for p in found):
            continue
        done |= joins  # the joins read through here are handled here
        for part in found:
            already = isinstance(part, (exp.Cast, exp.TryCast)) and isinstance(part.to, exp.DataType) \
                and part.to.this in _TEXT_CASTS
            if not literal(part) and not already and not isinstance(part, exp.Null):
                cast = exp.Cast(to=exp.DataType.build("TEXT"))
                part.replace(cast)
                cast.set("this", part)  # the part itself, so a join inside it is still found and rewritten
    return statement


def _to_timestamp_call(node: exp.Expression) -> bool:
    """``node`` is sqlglot's reading of to_timestamp(x), a Cast to TIMESTAMP: Spark's timestamp has a
    time zone (text is read in the session's zone), where DuckDB's TIMESTAMP has none."""
    return (type(node) is exp.Cast and node.to.this == exp.DataType.Type.TIMESTAMP
            and node.meta.get(spark_sql_names._NAME) == "to_timestamp")


def _past(source: exp.Expression, kind: Any) -> bool:
    """Whether the whole number ``source`` (see _whole_number) can be past the range of the integer type
    ``kind``: a literal that is, or a cast to a wider type."""
    bits = _WIDTHS[_INT_CASTS[kind][0]]
    while isinstance(source, exp.Paren):
        source = source.this
    if isinstance(source, exp.Literal):
        return not -(2 ** (bits - 1)) <= int(source.this) < 2 ** (bits - 1)
    if isinstance(source, (exp.Cast, exp.TryCast)) and source.to.this in _INT_CASTS:
        return _WIDTHS[_INT_CASTS[source.to.this][0]] > bits
    return False


_BINARY_CASTS = (exp.DataType.Type.BINARY, exp.DataType.Type.VARBINARY)
_BINARY_WIDTHS = {"TINYINT": 1, "SMALLINT": 2, "INTEGER": 4, "BIGINT": 8}


def spark_binary_cast(value: str) -> str:
    """CAST(value AS BINARY) as Spark casts it: a whole number as its big-endian bytes (4 for an int, 8 for a
    long), text as its UTF-8 bytes."""
    whole = " ".join(f"WHEN '{t}' THEN UNHEX(RIGHT(LPAD(HEX(TRY_CAST({value} AS BIGINT)), 16, '0'), {2 * w}))"
                     for t, w in _BINARY_WIDTHS.items())
    return (f"CASE typeof({value}) {whole} WHEN 'VARCHAR' THEN ENCODE(TRY_CAST({value} AS VARCHAR)) "
            f"ELSE TRY_CAST({value} AS BLOB) END")


def _negative_literals(statement: exp.Expression) -> None:
    """-9223372036854775808L, -2147483648 and the like: Spark reads the minus as part of the literal, so the
    literal's type holds it, where negating 9223372036854775808 would be past a long. Negating a whole number wraps in
    Spark with ANSI off, as the cast of the negative literal does, so every such literal is read this way."""
    for neg in list(statement.find_all(exp.Neg)):
        cast_ = neg.this
        if isinstance(cast_, exp.Literal) and not cast_.is_string and cast_.name.isdigit():
            negative: exp.Expression = exp.Literal.number(f"-{cast_.name}")
            if int(cast_.name) == 2 ** 31:  # an int, as in Spark: DuckDB reads it as minus a bigint
                negative = exp.Cast(this=negative, to=exp.DataType.build("INT"))
            neg.replace(negative)
            continue
        if isinstance(cast_, (exp.Cast, exp.TryCast)) and isinstance(cast_.to, exp.DataType) \
                and cast_.to.this in _INT_CASTS and isinstance(cast_.this, exp.Literal) \
                and not cast_.this.is_string and cast_.this.name.isdigit():
            cast_.set("this", exp.Literal.number(f"-{cast_.this.name}"))
            neg.replace(cast_)


def spark_integer_casts(statement: exp.Expression) -> exp.Expression:
    """Casts to TINYINT, SMALLINT, INT or BIGINT written out as Spark casts (EA-003, and EA-014 for a
    timestamp): see spark_integer_cast. Casts to a timestamp read a number as seconds (spark_timestamp_cast).
    sqlglot reads Spark's CAST as a TryCast, try_cast as a TryCast marked safe, and Databricks SQL's
    CAST as a Cast."""
    done: set[int] = set()
    _negative_literals(statement)
    for node in reversed(list(statement.find_all(exp.Cast, exp.TryCast))):
        kind = node.to.this if isinstance(node.to, exp.DataType) else None
        source = node.this
        if (id(source) in done and kind not in _BINARY_CASTS) or isinstance(source, exp.Null):
            continue  # a cast already written here is a value of its type; only a cast to binary reads it again
        if isinstance(node, exp.TryCast):
            mode = "try" if node.args.get("safe") else "legacy"
        else:
            mode = "ansi"
        if kind in _INT_CASTS and mode == "legacy" and _whole_number(source) and _past(source, kind):
            write = functools.partial(_java_narrowed, duck_type=_INT_CASTS[kind][0])
        elif kind in _INT_CASTS and not _whole_number(source):
            write = functools.partial(spark_integer_cast, duck_type=_INT_CASTS[kind][0],
                                      spark_type=_INT_CASTS[kind][1], mode=mode)
        elif kind in _TIMESTAMP_CASTS and not (isinstance(source, exp.Literal) and source.is_string):
            to = "TIMESTAMPTZ" if _to_timestamp_call(node) else duckdb_sql(node.to)
            write = functools.partial(spark_timestamp_cast, duck_type=to, mode=mode)
        elif kind in _TEXT_CASTS and not (isinstance(source, exp.Literal) and source.is_string) and not node.to.expressions:
            write = functools.partial(spark_text_cast)
        elif kind in _BINARY_CASTS and not (isinstance(source, exp.Literal) and source.is_string):
            write = functools.partial(spark_binary_cast)
        else:
            continue
        if isinstance(source, (exp.Column, exp.Literal)):
            text = write("fj_arg0")
        else:
            # Read once through a lambda argument, so nested casts don't multiply the SQL with each level
            # and a source such as rand() is evaluated once. The source sits outside the lambda.
            text = f"(LIST_TRANSFORM([fj_arg0], kf_cast -> {write('kf_cast')}))[1]"
        replacement = exp.paren(_with_args(text, [source]))
        replacement.type = exp.DataType.build("TIMESTAMPTZ") if _to_timestamp_call(node) else node.to.copy()
        replacement.meta[spark_complex.TYPED] = True  # for spark_complex_types: the cast's own type
        done.add(id(replacement))
        if node is statement:
            return replacement
        node.replace(replacement)
    return statement

class _JsonNull:
    """Spark's NullType while inferring: merges into anything, and is STRING when it stays alone."""


def _json_decimal(digits: int, scale: int) -> Any:
    """Spark's type for a number it reads as a decimal: DECIMAL(max(precision, scale), max(scale, 0)), or
    DOUBLE past 38 digits."""
    precision = max(digits, scale)
    return ("DECIMAL", precision, max(scale, 0)) if precision <= 38 else "DOUBLE"


def _infer_json_type(value: Any, prefers_decimal: bool = False, primitives_as_string: bool = False) -> Any:
    """Spark's JsonInferSchema.inferField over a value json.loads gave (floats as Decimal): "BIGINT",
    "DOUBLE", ("DECIMAL", p, s), "BOOLEAN", "STRING", _JsonNull, ("ARRAY", element) or ("STRUCT", {name: type}).
    A whole number past BIGINT is a DECIMAL of its digits; with prefersDecimal a decimal number is one too."""
    import decimal
    if value is None or value == "":
        return _JsonNull   # Spark infers an empty string as NullType too
    if isinstance(value, (bool, int, decimal.Decimal)) and primitives_as_string:
        return "STRING"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "BIGINT" if -2**63 <= value < 2**63 else _json_decimal(len(str(abs(value))), 0)
    if isinstance(value, float):  # NaN and Infinity, which Spark reads as numbers (allowNonNumericNumbers)
        return "STRING" if primitives_as_string else "DOUBLE"
    if isinstance(value, decimal.Decimal):
        if not prefers_decimal or not value.is_finite():
            return "DOUBLE"
        sign, digits, exponent = value.as_tuple()
        if int(exponent) > 0:  # Spark's DecimalType stops on the negative scale of 1e5 (DECIMAL(1,-5))
            raise ValueError(f"schema_of_json with prefersDecimal stops in Spark on {value}, a number whose "
                             "exponent leaves it no decimal places, so nothing ran. Leave out prefersDecimal, or "
                             "write the number without an exponent.")
        return _json_decimal(len(digits), -int(exponent))
    if isinstance(value, str):
        return "STRING"
    if isinstance(value, list):
        element: Any = _JsonNull
        for item in value:
            element = _merge_json_types(element, _infer_json_type(item, prefers_decimal, primitives_as_string))
        return ("ARRAY", element)
    return ("STRUCT", {name: _infer_json_type(item, prefers_decimal, primitives_as_string)
                       for name, item in sorted(value.items())})


def _as_json_decimal(t: Any) -> tuple[str, int, int] | None:
    """A DECIMAL as it is, BIGINT as DECIMAL(20,0) (Spark's DecimalType.forType), anything else None."""
    if isinstance(t, tuple) and t[0] == "DECIMAL":
        return t
    return ("DECIMAL", 20, 0) if t == "BIGINT" else None


def _merge_json_types(a: Any, b: Any) -> Any:
    """Spark's JsonInferSchema.compatibleType: BIGINT with DOUBLE is DOUBLE, a DECIMAL with a DOUBLE is
    DOUBLE, a DECIMAL with BIGINT (as DECIMAL(20,0)) or another DECIMAL widens to hold both (DOUBLE past 38
    digits), structs merge field by field, arrays merge their elements, NullType takes the other type, and any
    other mix is STRING."""
    if a == b:
        return a
    if a is _JsonNull:
        return b
    if b is _JsonNull:
        return a
    if (a, b) in (("BIGINT", "DOUBLE"), ("DOUBLE", "BIGINT")):
        return "DOUBLE"
    da, db = _as_json_decimal(a), _as_json_decimal(b)
    if da is not None and db is not None:  # not both BIGINT: a == b took that
        (_, p1, s1), (_, p2, s2) = da, db
        scale, whole = max(s1, s2), max(p1 - s1, p2 - s2)
        return ("DECIMAL", whole + scale, scale) if whole + scale <= 38 else "DOUBLE"
    if "DOUBLE" in (a, b) and any(isinstance(t, tuple) and t[0] == "DECIMAL" for t in (a, b)):
        return "DOUBLE"
    if isinstance(a, tuple) and isinstance(b, tuple) and a[0] == b[0] == "ARRAY":
        return ("ARRAY", _merge_json_types(a[1], b[1]))
    if isinstance(a, tuple) and isinstance(b, tuple) and a[0] == b[0] == "STRUCT":
        names = sorted(set(a[1]) | set(b[1]))
        return ("STRUCT", {n: _merge_json_types(a[1].get(n, _JsonNull), b[1].get(n, _JsonNull)) for n in names})
    return "STRING"


def _canonical_json_type(t: Any) -> Any:
    """Spark's canonicalizeType: NullType becomes STRING and a struct with no fields is dropped (None)."""
    if t is _JsonNull:
        return "STRING"
    if isinstance(t, tuple) and t[0] == "DECIMAL":
        return t
    if isinstance(t, tuple) and t[0] == "ARRAY":
        element = _canonical_json_type(t[1])
        return None if element is None else ("ARRAY", element)
    if isinstance(t, tuple):
        fields = {n: c for n, f in t[1].items() if n and (c := _canonical_json_type(f)) is not None}
        return ("STRUCT", fields) if fields else None
    return t


def _json_type_sql(t: Any, quote_all: bool = False) -> str:
    """DataType.sql as Spark writes it: STRUCT<name: TYPE, ...>, a name in backticks (doubled inside)
    only when it needs them (QuotingUtils.quoteIfNeeded: anything but letters, digits and _, or all digits),
    or always with ``quote_all`` (Databricks)."""
    if isinstance(t, str):
        return t
    if t[0] == "DECIMAL":
        return f"DECIMAL({t[1]},{t[2]})"
    if t[0] == "ARRAY":
        return f"ARRAY<{_json_type_sql(t[1], quote_all)}>"
    return "STRUCT<" + ", ".join(_quote_if_needed(n, quote_all) + ": " + _json_type_sql(f, quote_all)
                                 for n, f in t[1].items()) + ">"


def _quote_if_needed(name: str, quote_all: bool) -> str:
    if not quote_all and re.fullmatch(r"[a-zA-Z0-9_]+", name) and not name.isdigit():
        return name
    return "`" + name.replace("`", "``") + "`"


def schema_of_json(text: str, quote_all: bool = False, options: dict[str, str] | None = None) -> str:
    """Spark's schema_of_json for ``text``, worked out here (#1447): its SchemaOfJson keeps a top-level
    struct (or an array of structs) whose fields all drop as STRUCT<>, and anything else that drops as
    STRING."""
    import json

    def no_repeats(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        if len({k for k, _ in pairs}) != len(pairs):
            raise ValueError("schema_of_json was given a JSON object that repeats a key, so nothing ran. Remove "
                             "the repeated key.")
        return dict(pairs)

    import decimal
    try:
        value = json.loads(text, object_pairs_hook=no_repeats, parse_float=decimal.Decimal)
    except json.JSONDecodeError as exc:
        raise ValueError(f"schema_of_json was given text that isn't JSON ({exc.msg}), so nothing ran.") from exc
    given = options or {}
    inferred = _infer_json_type(value, given.get("prefersDecimal", "").strip().lower() == "true",
                                given.get("primitivesAsString", "").strip().lower() == "true")
    result: Any
    if isinstance(inferred, tuple) and inferred[0] == "STRUCT":
        result = _canonical_json_type(inferred) or ("STRUCT", {})
    elif isinstance(inferred, tuple) and inferred[0] == "ARRAY" and isinstance(inferred[1], tuple) \
            and inferred[1][0] == "STRUCT":
        result = ("ARRAY", _canonical_json_type(inferred[1]) or ("STRUCT", {}))
    else:
        result = _canonical_json_type(inferred) or "STRING"
    return _json_type_sql(result, quote_all)


def _schema_of_json_options(given: exp.Expression) -> dict[str, str]:
    """schema_of_json's options in Spark SQL: prefersDecimal and primitivesAsString are read; another option
    is taken only at Spark's default (one that reads text another way is refused by name)."""
    keys, values = given.args.get("keys"), given.args.get("values")
    pairs = list(zip(keys.expressions, values.expressions, strict=True)) \
        if isinstance(given, exp.VarMap) and isinstance(keys, exp.Array) and isinstance(values, exp.Array) else None
    if pairs is None or not all(isinstance(p, exp.Literal) and p.is_string for pair in pairs for p in pair):
        raise ValueError("schema_of_json's options must be written out as map('name', 'value', ...) to run on this "
                         "PC, so nothing ran. Write each option as text, such as map('prefersDecimal', 'true').")
    out: dict[str, str] = {}
    for key, value in pairs:
        name = next((o for o in ("prefersDecimal", "primitivesAsString", *_FROM_JSON_DEFAULTS)
                     if o.lower() == key.name.lower()), None)
        if name is None or (name in _FROM_JSON_DEFAULTS and value.name.strip().lower() != _FROM_JSON_DEFAULTS[name]):
            raise ValueError(f"schema_of_json's {key.name!r} option isn't read on this PC, so nothing ran. Leave it "
                             "out, or run the cell in Fabric.")
        out[name] = value.name
    return out


def _spark_json(databricks: bool, statement: exp.Expression) -> exp.Expression:
    """from_json with a written-out schema and str_to_map, which DuckDB doesn't have, written in DuckDB
    SQL that answers as Spark does (#1404), and schema_of_json of a literal, worked out here as its
    schema text (#1447) before from_json reads it. Anything this can't read exactly is refused."""
    for call in list(statement.find_all(exp.Anonymous)):
        if str(call.this).upper() != "SCHEMA_OF_JSON":
            continue
        args = list(call.expressions)
        options: dict[str, str] = {}
        if len(args) == 2 and not databricks:
            options = _schema_of_json_options(args.pop())
        if len(args) != 1:
            raise ValueError("schema_of_json options (the second argument) aren't read on this PC, so nothing ran. "
                             "Run the query on Databricks, or leave the options out.")
        if not (isinstance(args[0], exp.Literal) and args[0].is_string):
            raise ValueError("schema_of_json takes JSON written out as text, as Databricks requires, so nothing ran.")
        literal = exp.Literal.string(schema_of_json(args[0].name, quote_all=databricks, options=options))
        if call is statement:
            return literal
        call.replace(literal)
    for node in reversed(list(statement.find_all(exp.FromJson, exp.StrToMap))):
        if isinstance(node, exp.FromJson) and not databricks:
            text, dtype = _spark_from_json_template(node.expression, node)
            replacement = _with_args(text, [node.this])
        elif isinstance(node, exp.FromJson):
            if node.args.get("options") is not None:
                raise ValueError("from_json options (the third argument) aren't read on this PC, so nothing ran. "
                                 "Run the query on Databricks, or leave the options out.")
            replacement = _with_args(_from_json_template(node.expression), [node.this])
        else:
            replacement = _with_args(_STR_TO_MAP, [node.this, node.args.get("pair_delim") or exp.Literal.string(","),
                                                   node.args.get("key_value_delim") or exp.Literal.string(":")])
        if cast(Any, node) is statement:  # sqlglot's Func isn't typed as an Expression
            return replacement
        node.replace(replacement)
    return statement


_TIMESTAMPS = (exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPTZ, exp.DataType.Type.TIMESTAMPLTZ,
               exp.DataType.Type.TIMESTAMPNTZ)


# Marks a CAST to DATE the person wrote (spark_legacy_casts), for spark_legacy_times.
_SPARK_DATE_CAST = "kf_spark_date_cast"


def spark_legacy_times(statement: exp.Expression) -> exp.Expression:
    """Spark's to_timestamp with ANSI off, as Fabric runs it: text that isn't a timestamp gives NULL
    (EA-015). sqlglot reads to_timestamp(x) as a CAST and to_timestamp(x, format) as a strptime,
    and both stop the query on DuckDB. For Spark SQL and the DataFrame API only: there a CAST the
    person wrote is read as a TryCast, so a plain Cast to a timestamp came from to_timestamp.
    Databricks SQL is ANSI, so its path (engines/local_sql.py) doesn't use this."""
    for node in list(statement.find_all(exp.StrToTime, exp.Cast)):
        if isinstance(node, exp.TryCast) and node.meta.get(_SPARK_DATE_CAST):
            # Spark reads text as a date by its own rules: '24-02-29' and '-7-7-7-7-7' are NULL, where DuckDB
            # reads the year 24, or a date before year 1 that can't be fetched.
            read: exp.Expression = exp.paren(spark_builtins_sql.text_date(node.this))
            if node is statement:
                return read
            node.replace(read)
        elif isinstance(node, exp.StrToTime):
            node.set("safe", True)
        elif type(node) is exp.Cast and node.to.this in _TIMESTAMPS:
            # to_timestamp gives Spark's timestamp: with time zone, text read in the session's zone
            to = exp.DataType.build("TIMESTAMPTZ") if _to_timestamp_call(node) else node.to
            replacement = exp.TryCast(this=node.this, to=to)
            replacement.meta.update(spark_sql_names.kept(node))  # double(x) keeps the name Spark gives it
            if node is statement:
                return replacement
            node.replace(replacement)
    return statement


def _spark_dates(statement: exp.Expression) -> exp.Expression:
    """Keep Spark's date results where sqlglot's DuckDB SQL differs: date_add and date_sub return
    a date (DuckDB's date + INTERVAL is a timestamp), trunc(date, 'MM') returns a date, and Spark's
    unit spellings ('MM', 'YYYY') become ones DuckDB reads."""
    # Deepest first: a node is replaced by a cast of its copy, so a trunc inside a trunc must have its unit
    # spelled for DuckDB before the outer one copies it (trunc(trunc(d, 'MM'), 'MONTH') kept 'MM' and stopped).
    for node in reversed(list(statement.find_all(exp.TsOrDsAdd, exp.DateTrunc, exp.TimestampTrunc))):
        if isinstance(node, (exp.DateTrunc, exp.TimestampTrunc)):
            unit = node.args.get("unit")
            spark_trunc = isinstance(unit, exp.Literal)  # trunc(date, 'MM'); date_trunc('MM', ts) has a Var
            name = unit.name.upper() if unit is not None else ""
            if name in _SPARK_UNITS:
                node.set("unit", exp.Literal.string(_SPARK_UNITS[name]) if spark_trunc else exp.var(_SPARK_UNITS[name]))
            if not spark_trunc:
                continue
        node.replace(exp.cast(node.copy(), exp.DataType.Type.DATE))
    return statement


# timestampdiff(unit, start, end) counts whole units elapsed, as Java's ChronoUnit.between does for Spark:
# up to WEEK by the microseconds between the two (a DAY is 86400 seconds, in the session's UTC), rounded
# toward zero; MONTH, QUARTER and YEAR by whole months, where a month has elapsed once the calendar day
# and time reach the start's. sqlglot writes DuckDB's date_diff, which counts the boundaries crossed
# instead: timestampdiff(MONTH, '2021-02-28 12:00:00', '2021-03-28 11:59:59') is 1 there, 0 in Spark.
_DIFF_MICROS = {"MICROSECOND": 1, "MILLISECOND": 1_000, "SECOND": 1_000_000, "MINUTE": 60_000_000,
                "HOUR": 3_600_000_000, "DAY": 86_400_000_000, "WEEK": 604_800_000_000}
_DIFF_MONTHS = {"MONTH": 1, "QUARTER": 3, "YEAR": 12}
_MICROS_BETWEEN = "CAST((epoch_us(CAST(_e AS TIMESTAMP)) - epoch_us(CAST(_s AS TIMESTAMP))) // {n} AS BIGINT)"
# Java's LocalDateTime.until: the end date moves a day toward the start when its time of day hasn't
# reached the start's, then whole months are (month * 32 + day) apart, divided by 32 rounding toward zero
_END_DAY = ("CASE WHEN CAST(_e AS DATE) > CAST(_s AS DATE) AND CAST(CAST(_e AS TIMESTAMP) AS TIME) < "
            "CAST(CAST(_s AS TIMESTAMP) AS TIME) THEN CAST(_e AS DATE) - 1 WHEN CAST(_e AS DATE) < CAST(_s AS DATE) "
            "AND CAST(CAST(_e AS TIMESTAMP) AS TIME) > CAST(CAST(_s AS TIMESTAMP) AS TIME) THEN CAST(_e AS DATE) + 1 "
            "ELSE CAST(_e AS DATE) END")
_PACKED = "((year({d}) * 12 + month({d})) * 32 + day({d}))"
_MONTHS_BETWEEN = f"CAST((({_PACKED.format(d='_end_day')} - {_PACKED.format(d='CAST(_s AS DATE)')}) // 32) // {{n}} AS BIGINT)"


def _diff_template(sql_text: str, **args: exp.Expression) -> exp.Expression:
    def put(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and not node.table and node.name.startswith("_") and node.name[1:] in args:
            return exp.paren(args[node.name[1:]].copy(), copy=False)
        return node
    return sqlglot.parse_one(sql_text, read="duckdb").transform(put)


def _spark_timestampdiff(statement: exp.Expression) -> exp.Expression:
    """timestampdiff (and the datediff, date_diff and timediff spellings of it) counting whole units, as Spark does."""
    # innermost first, so one inside another's arguments is written before the outer one copies them
    for node in reversed(list(statement.find_all(exp.TimestampDiff, bfs=False))):
        unit = node.text("unit").upper()
        start, end = node.expression, node.this
        if unit in _DIFF_MICROS:
            new = _diff_template(_MICROS_BETWEEN.format(n=_DIFF_MICROS[unit]), s=start, e=end)
        elif unit in _DIFF_MONTHS:
            end_day = _diff_template(_END_DAY, s=start, e=end)
            new = _diff_template(_MONTHS_BETWEEN.format(n=_DIFF_MONTHS[unit]), s=start, end_day=end_day)
        else:
            continue
        if node is statement:
            return new
        node.replace(new)
    return statement


_TIME_TYPES = "'DATE', 'TIMESTAMP', 'TIMESTAMP WITH TIME ZONE', 'TIMESTAMP_S', 'TIMESTAMP_MS', 'TIMESTAMP_NS'"


def _strptime_format(node: exp.Expression) -> tuple[str, bool]:
    """The DuckDB strptime format, as SQL, for a Spark datetime pattern sqlglot has read into ``node``,
    and whether the pattern has a year. S, SS and SSS (fractions of a second) become %g, and quoted
    text ('T', 'Z') stays literal; anything else sqlglot leaves untranslated is refused, since
    strptime would quietly match nothing."""
    from sqlglot.dialects.duckdb import DuckDB
    from sqlglot.dialects.spark import Spark
    # sqlglot has already turned pattern letters into its own %-codes, inside quotes too: turn
    # those back into the letters they came from (the shortest spelling of each).
    letters: dict[str, str] = {}
    for spark, code in sorted(Spark.TIME_MAPPING.items(), key=lambda kv: -len(kv[0])):
        letters[code] = spark
    codes = re.compile("|".join(re.escape(c) for c in sorted(letters, key=len, reverse=True)))
    generator = DuckDB().generator()
    out = []
    for i, piece in enumerate(re.split(r"('(?:[^']|'')*')", node.args["format"].name)):
        if i % 2:  # 'quoted text', as Spark writes a literal ('' is a quote)
            text = "'" if piece == "''" else codes.sub(lambda m: letters[m.group()], piece[1:-1]).replace("''", "'")
            out.append(text.replace("%", "%%"))
            continue
        piece = generator.format_time(exp.StrToUnix(format=exp.Literal.string(piece))) or "''"
        piece = re.sub(r"(?<![%S])S{1,3}(?!S)", "%g", sqlglot.parse_one(piece, read="duckdb").name)
        if re.search(r"[A-Za-z']", re.sub(r"%-?[A-Za-z%]", "", piece)):
            raise ValueError(f"unix_timestamp can't read part of its format ({piece}) on this PC. Use pattern "
                             "letters such as yyyy, MM, dd, HH, mm, ss and SSS.")
        out.append(piece)
    fmt = "".join(out)
    return exp.Literal.string(fmt).sql("duckdb"), bool(re.search(r"%-?[Yy]", fmt.replace("%%", "")))


def _as_timestamp(value: exp.Expression) -> str:
    """A date, timestamp or text as a timestamp, as Spark reads one (text that isn't one is NULL)."""
    return f"TRY_CAST({duckdb_sql(value)} AS TIMESTAMP)"


# Spark's hash is Murmur3 (32-bit, seed 42) and xxhash64 is XXH64 (seed 42); DuckDB's hash is
# neither. spark_fixes gives Spark's value (spark_hash.py); paths without it refuse them (EA-017).
_SPARK_HASHES = ("HASH", "XXHASH64")
_SPARK_HASH = ("{name}() can't run on this PC the way it runs in Fabric, so nothing ran: Spark's {name} gives "
               "a different number from the hash this PC has. Run it in Fabric, or use md5() or sha2(x, 256) "
               "if any stable hash will do.")
_SLICE_INDEX_0 = ("The index 0 is invalid. An index shall be either < 0 or > 0 "
                  "(the first element has index 1).")
_SLICE_LENGTH = "Unexpected value for length in function slice: length must be greater than or equal to 0."


# Functions this module rewrites into longer DuckDB SQL; a select of one keeps the name Spark gives it.
_REWRITTEN = (exp.IgnoreNulls, exp.AddMonths, exp.StrToUnix, exp.Substring, exp.ArraySlice, exp.MonthsBetween,
              exp.TsOrDsAdd, exp.DateTrunc, exp.TimestampTrunc, exp.DayOfWeek, *_SORTS,
              exp.Quantile, exp.ArrayDistinct, exp.ArrayIntersect, exp.ArrayExcept, exp.LastDay, exp.NextDay)


# Operators Spark writes in parentheses in a column's name: "(a + 1)", "(a = b)".
_SPARK_PARENTHESISED = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.IntDiv, exp.EQ, exp.NEQ, exp.NullSafeEQ,
                        exp.GT, exp.GTE, exp.LT, exp.LTE, exp.And, exp.Or, exp.Not, exp.Is)


def spark_name(column: exp.Expression) -> str:
    """The name Spark gives an unnamed selected expression: functions in lower case, each operator in
    parentheses ("(a + 1)", "(NOT a)", "(a IS NULL)", "(- a)") and a decimal's parameters without a space ("DECIMAL(10,2)")."""
    copy = column.copy()
    for call in list(copy.find_all(exp.ToChar)):   # sqlglot writes to_char(n, fmt) as a CAST for Spark
        named = exp.Anonymous(this="to_char", expressions=[a for a in (call.this, call.args.get("format")) if a])
        if call is copy:
            copy = named
        else:
            call.replace(named)
    # Spark writes these its own way: UnaryMinus as "(- x)" and IsNotNull as "(x IS NOT NULL)", which
    # sqlglot reads as NOT (x IS NULL). Innermost first, each operand formatted the same way.
    for unary in reversed(list(copy.find_all(exp.Neg, exp.Not))):
        if unary.find_ancestor(exp.Lambda):
            continue
        inner = unary.this.unnest() if isinstance(unary, exp.Not) else unary.this
        if isinstance(unary, exp.Neg) and not isinstance(unary.this, exp.Literal):  # -1 is the literal -1
            text = f"(- {spark_name(unary.this)})"
        elif isinstance(unary, exp.Not) and isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null):
            text = f"({spark_name(inner.this)} IS NOT NULL)"
        else:
            continue
        if unary is copy:
            return text
        unary.replace(exp.Var(this=text))
    for node in reversed(list(copy.find_all(*_SPARK_PARENTHESISED))):
        if isinstance(node.parent, exp.Paren) or node.find_ancestor(exp.Lambda):
            continue  # a lambda's body keeps the form it was written in (Spark names it lambdafunction(...))
        wrapper = exp.Paren()
        if cast(Any, node) is copy:  # sqlglot's Condition isn't typed as an Expression
            wrapper.set("this", node)
            copy = wrapper
        else:
            node.replace(wrapper)
            wrapper.set("this", node)
    for kind in list(copy.find_all(exp.DataType)):
        if kind.this == exp.DataType.Type.DECIMAL and kind.expressions:  # Spark's DecimalType.sql
            kind.replace(exp.Var(this=kind.sql("spark").replace(" ", "")))
    return copy.sql("spark", normalize_functions="lower")


_BUILTIN_NAMES = spark_builtins_sql.names()


def _spark_names(statement: exp.Expression) -> exp.Expression:
    """Name each selected expression without AS as Spark names it ("upper(a)", "locate(b, s, 1)"), not after
    the DuckDB SQL it becomes (spark_sql_names); one that module can't name keeps the older rule: named
    by spark_name when this module rewrites it."""
    for select in statement.find_all(exp.Select):
        for i, column in enumerate(select.expressions):
            if isinstance(column, (exp.Alias, exp.Column, exp.Star)):
                continue
            named = spark_sql_names.column_name(column, statement)
            if named is not None:
                select.expressions[i] = exp.alias_(column, named, quoted=True)
                select.expressions[i].parent = select
                continue
            if not (
                    column.find(*_REWRITTEN, *spark_builtins_sql.typed_classes())
                    or any(str(a.this).upper() in _ANON_TEMPLATES or str(a.this).upper() in _MAP_LAMBDAS
                           or str(a.this).upper() in _BUILTIN_NAMES
                                                    for a in column.find_all(exp.Anonymous))
                    or any(r.args.get("finish") is not None for r in column.find_all(exp.Reduce))
                    # Spark's CAST is DuckDB's TRY_CAST, and casts to whole numbers and timestamps are written out.
                    or column.find(exp.Cast, exp.TryCast)):
                continue
            name = spark_name(column)
            select.expressions[i] = exp.alias_(column, name, quoted=True)
            select.expressions[i].parent = select
    return statement


def refuse_spark_hashes(node: exp.Expr) -> None:
    """ValueError if Spark SQL ``node`` calls hash or xxhash64 (_SPARK_HASH). The Local data engine's
    Spark SQL calls it: it doesn't go through spark_fixes, where spark_builtins_sql gives Spark's value."""
    for call in node.find_all(exp.Anonymous):
        if str(call.this).upper() in _SPARK_HASHES:
            raise ValueError(_SPARK_HASH.format(name=str(call.this).lower()))


def spark_substring(node: exp.Expression) -> exp.Expression:
    """Spark's substring counts position 0 as 1 (substring('hello', 0, 2) is 'he'); DuckDB starts
    before the first character and returns 'h'.

    unix_timestamp also takes a date or timestamp (DuckDB's strptime takes only text), reads its
    value in the session time zone, is NULL for text that doesn't match the format, and is a whole
    number of seconds, as in Spark.

    add_months is a date, as in Spark, also for text or a timestamp (text that isn't a date is NULL).

    slice refuses index 0 and a negative length, and element_at an array's index 0, as in Spark.

    months_between counts the time of day and rounds to 8 digits, as in Spark.

    first(x, true) and last(x, true) skip NULLs as aggregates too (DuckDB takes IGNORE NULLS only
    in a window).

    hash and xxhash64 aren't DuckDB's hash: spark_builtins_sql writes them as Spark computes them."""
    for ignore in list(node.find_all(exp.IgnoreNulls)):
        agg = ignore.this
        if not isinstance(agg, (exp.First, exp.Last, exp.FirstValue, exp.LastValue)):
            continue
        name = "first" if isinstance(agg, (exp.First, exp.FirstValue)) else "last"
        value = duckdb_sql(agg.this)
        if isinstance(ignore.parent, exp.Window):  # sqlglot drops the IGNORE NULLS DuckDB's windows take
            window = ignore.parent.copy()
            window.set("this", exp.column("__fd_ignoring__"))
            text = duckdb_sql(window).replace("__fd_ignoring__", f"{name}_value({value} IGNORE NULLS)", 1)
            target: exp.Expression = ignore.parent
        else:
            text, target = f"{name}({value}) FILTER (WHERE {value} IS NOT NULL)", ignore
        swapped = target.replace(cast(exp.Expression, sqlglot.parse_one(text, read="duckdb")))
        node = swapped if target is node else node
    for months in list(node.find_all(exp.AddMonths)):  # Spark's add_months is a date, whatever it's given
        months.set("this", exp.TryCast(this=exp.TryCast(this=months.this, to=exp.DataType.build("TIMESTAMP")),
                                       to=exp.DataType.build("DATE")))
        swapped = months.replace(exp.cast(months.copy(), exp.DataType.Type.DATE))
        node = swapped if months is node else node
    for kind in node.find_all(exp.DataType):  # struct< > reads as a struct with no fields
        if kind.this == exp.DataType.Type.STRUCT and not kind.expressions:
            raise ValueError(_EMPTY_STRUCT)
    for constraint in list(node.find_all(exp.NotNullColumnConstraint)):  # DuckDB has no NOT NULL in a struct
        holder = constraint.parent
        column = holder.parent if holder else None
        if holder is not None and isinstance(column, exp.ColumnDef) and isinstance(column.parent, exp.DataType):
            holder.pop()
    for part in list(node.find_all(exp.ArraySlice)):  # Spark refuses index 0 and a negative length
        if not part.args.get("is_length"):
            continue
        for key, refused, message in (("start", lambda n: n == 0, _SLICE_INDEX_0),
                                      ("end", lambda n: n < 0, _SLICE_LENGTH)):
            bound = part.args.get(key)
            if bound is None:
                continue
            v = duckdb_sql(bound)
            try:
                if not refused(int(v)):
                    continue  # a whole number Spark takes
            except ValueError:
                pass
            test = "= 0" if key == "start" else "< 0"
            part.set(key, sqlglot.parse_one(
                f"CASE WHEN {v} {test} THEN error({exp.Literal.string(message).sql('duckdb')}) ELSE {v} END",
                read="duckdb"))
    for item in list(node.find_all(exp.Bracket)):  # element_at: an array's index 0 is refused
        if item.args.get("offset") != 1 or item.args.get("safe") or len(item.expressions) != 1:
            continue
        v = duckdb_sql(item.expressions[0])
        try:
            if int(v) != 0:
                continue
        except ValueError:
            pass
        item.set("expressions", [sqlglot.parse_one(
            f"CASE WHEN TRY_CAST({v} AS BIGINT) = 0 AND typeof({duckdb_sql(item.this)}) LIKE '%[]' "
            f"THEN error({exp.Literal.string(_SLICE_INDEX_0).sql('duckdb')}) ELSE {v} END", read="duckdb")])
    for between in list(node.find_all(exp.MonthsBetween)):
        later, earlier = (_as_timestamp(between.this), _as_timestamp(between.expression))
        month_count = (f"((year({later}) * 12 + month({later})) - (year({earlier}) * 12 + month({earlier})))")
        same = (f"day({later}) = day({earlier}) OR (day({later}) = day(last_day({later})) "
                f"AND day({earlier}) = day(last_day({earlier})))")
        seconds = f"(epoch({later}) - epoch(date_trunc('day', {later})) - epoch({earlier}) + epoch(date_trunc('day', {earlier})))"
        value = (f"CASE WHEN {same} THEN CAST({month_count} AS DOUBLE) ELSE {month_count} + "
                 f"((day({later}) - day({earlier})) * 86400 + {seconds}) / 2678400.0 END")
        roundoff = between.args.get("roundoff")
        if not (isinstance(roundoff, exp.Boolean) and roundoff.this is False):
            value = f"round({value}, 8)"
        swapped = between.replace(cast(exp.Expression, sqlglot.parse_one(value, read="duckdb")))
        node = swapped if between is node else node
    for unix in list(node.find_all(exp.StrToUnix)):
        value = duckdb_sql(unix.this)
        pattern = spark_builtins_sql.written_pattern(unix) if unix.args.get("format") else None
        if pattern is not None:  # read as Spark reads its pattern (spark_datetime)
            name = unix.meta.get(spark_sql_names._NAME) or "unix_timestamp"
            parsed = spark_datetime.parse_sql(pattern, name).replace("fj_arg0", f"({value})")
        else:
            fmt, has_year = _strptime_format(unix) if unix.args.get("format") else ("'%Y-%m-%d %H:%M:%S'", True)
            # Without a year Spark reads the date as 1970-01-01; strptime starts from 1900-01-01.
            parsed = f"try_strptime(CAST({value} AS VARCHAR), {fmt})" + ("" if has_year else " + INTERVAL 70 YEAR")
        swapped = unix.replace(cast(exp.Expression, sqlglot.parse_one(
            f"CAST(floor(epoch(CASE WHEN typeof({value}) IN ({_TIME_TYPES}) THEN TRY_CAST({value} AS TIMESTAMPTZ) "
            f"ELSE CAST({parsed} AS TIMESTAMPTZ) END)) AS BIGINT)", read="duckdb")))
        node = swapped if unix is node else node
    for sub in list(node.find_all(exp.Substring)):
        start = sub.args.get("start")
        if start is None:
            continue
        if isinstance(start, exp.Literal) and not start.is_string:
            if start.this == "0":
                sub.set("start", exp.Literal.number(1))
        elif not isinstance(start, (exp.Literal, exp.Neg)):  # -3 counts from the end in both
            sub.set("start", exp.Case(ifs=[exp.If(this=exp.EQ(this=start.copy(), expression=exp.Literal.number(0)),
                                                  true=exp.Literal.number(1))], default=start.copy()))
        _spark_negative_length(sub)
    return node


def _spark_negative_length(sub: exp.Substring) -> None:
    """substring(s, pos, len) with a len below 0 is empty in Spark (UTF8String.substringSQL: the end is pos + len,
    before the start), and NULL when s or pos is NULL. DuckDB counts len back from pos instead:
    substring('Hello World', -1, -6) was 'o Worl'. A len written as 0 or more needs nothing."""
    length = sub.args.get("length")
    if length is None or sub.meta.get("kf_negative_length") or (
            isinstance(length, exp.Literal) and not length.is_string):
        return
    empty = exp.Substring(this=sub.this.copy(), start=exp.Add(
        this=exp.Mul(this=exp.paren(sub.args["start"].copy()), expression=exp.Literal.number(0)),
        expression=exp.Literal.number(1)), length=exp.Literal.number(0))  # '' of s's type, NULL for a NULL s or pos
    kept = sub.copy()
    kept.meta["kf_negative_length"] = True
    sub.replace(exp.Case(ifs=[exp.If(this=exp.LT(this=exp.paren(length.copy()), expression=exp.Literal.number(0)),
                                     true=empty)], default=kept))


def _lakehouse() -> Any:
    """The lakehouse module, imported only when a statement reads a lakehouse table, so this module's
    translation functions load without it (Academy's learner process has no lakehouse; DBA-003)."""
    from . import lakehouse
    return lakehouse


def bind_tables(statement: exp.Expression, con: duckdb.DuckDBPyConnection, views: dict[str, str],
                is_local: Callable[[str], bool] = lambda _name: False) -> None:
    """Point every lakehouse table ``statement`` names at a Delta dataset registered on ``con``.

    ``is_local(name)`` says a name is already a view or table on ``con`` (like a Spark temp
    view), so it is left alone."""
    ctes = {c.alias_or_name.lower() for c in statement.find_all(exp.CTE)}
    # CREATE ... v AS ...: v is what the statement makes, not a lakehouse table to read.
    made = statement.this if isinstance(statement, exp.Create) else None
    made = made.this if isinstance(made, exp.Schema) else made
    for table in list(statement.find_all(exp.Table)):
        if not isinstance(table.this, exp.Identifier):
            continue  # a table function such as range(10)
        if table is made:
            continue
        if not table.db and (table.name.lower() in ctes or is_local(table.name) or table.name in views.values()):
            continue
        # lakehouse.table_ref checks the parts: a catalog is only allowed as the lakehouse's own name.
        name = table_name(table)
        point = _time_travel(table)  # VERSION AS OF n, TIMESTAMP AS OF '...', or Databricks' sales@v3
        opened, at = None, {}
        if point is None:
            # The latest version is looked up each time, so a table written since an earlier statement
            # on a long-lived connection (spark on this PC) is read as it is now, as Spark does. Its
            # location is part of the key: in a proof, a table it wrote is read from the proof's copy.
            opened = _lakehouse().open_table(name)
            key = f"{name.lower()}@{opened.table_uri}#{opened.version()}"
        else:
            key, at = f"{name.lower()}@{point}", point_args(point)
        if key not in views:
            views[key] = f"__lakehouse_table_{next(_VIEW_NAMES)}"
            opened = opened or _lakehouse().open_table(name, **at)
            con.register(views[key], _lakehouse().table_dataset(opened))
        # Keep the name the query used as the alias, so sales.Country still resolves.
        alias = table.args.get("alias") or exp.TableAlias(this=exp.to_identifier(table.name))
        table.replace(exp.Table(this=exp.to_identifier(views[key]), alias=alias))


def table_name(table: exp.Table) -> str:
    """``table``'s name as lakehouse.table_ref reads it: catalog, schema and name, each in backticks when
    it holds a dot or a backtick (`` `dbo.sales` `` stays one name), so no part is dropped or split."""
    def part(p: str) -> str:
        return "`" + p.replace("`", "``") + "`" if "." in p or "`" in p else p
    return ".".join(part(p) for p in (table.catalog, table.db, table.name) if p)


def is_local(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """Whether ``name`` is a view or table already on ``con`` (a temp view an earlier statement made),
    for bind_tables. Case is ignored, as identifiers are in Spark SQL and DuckDB."""
    return bool(con.execute("SELECT 1 FROM duckdb_views() WHERE lower(view_name) = lower(?) "
                            "UNION ALL SELECT 1 FROM duckdb_tables() WHERE lower(table_name) = lower(?)",
                            [name, name]).fetchall())


def superseded(views: dict[str, str]) -> list[str]:
    """Keys in ``views`` (from bind_tables) for a table's latest version that a newer version of the
    same table, registered later, replaced. A time travel point is never superseded."""
    newest: dict[str, str] = {}
    old = []
    for key in views:  # in the order they were registered
        if "#" not in key:
            continue
        table = key.rpartition("#")[0]
        if table in newest:
            old.append(newest[table])
        newest[table] = key
    return old


def _time_travel(table: exp.Table) -> str | None:
    """A table's time travel point as "v<n>" or "t<time>", taken off the table so DuckDB never
    sees it; None to read the latest version."""
    version = table.args.get("version")
    if isinstance(version, exp.Version):
        if str(version.args.get("kind", "")).upper() != "AS OF":
            raise ValueError("Only VERSION AS OF and TIMESTAMP AS OF can be read on this PC.")
        value = version.expression.name if isinstance(version.expression, exp.Literal) else version.expression.sql("spark")
        table.set("version", None)
        return f"v{value}" if str(version.this).upper() == "VERSION" else f"t{value}"
    alias = table.args.get("alias")
    if isinstance(alias, exp.TableAlias) and isinstance(alias.this, exp.Parameter):  # sales@v3 (Databricks)
        value = alias.this.name
        table.set("alias", None)
        if re.fullmatch(r"[vV]\d+", value):
            return f"v{value[1:]}"
        if re.fullmatch(r"\d{14,17}", value):  # yyyyMMddHHmmssSSS
            return f"t{value[:4]}-{value[4:6]}-{value[6:8]} {value[8:10]}:{value[10:12]}:{value[12:14]}"
        raise ValueError(f"{table.name}@{value} isn't a version (@v3) or a time (@20261020130000000).")
    return None


def point_args(point: str) -> dict[str, str]:
    return {"version": point[1:]} if point.startswith("v") else {"timestamp": point[1:]}


def history_query(statement: exp.Expression, con: duckdb.DuckDBPyConnection,
                  views: dict[str, str]) -> exp.Expression:
    """DESCRIBE HISTORY t becomes a query over the table's Delta log; any other statement is
    returned as it is."""
    if not (isinstance(statement, exp.Describe) and str(statement.args.get("style") or "").upper() == "HISTORY"):
        return statement
    table = statement.this
    name = table_name(table)  # `dbo.sales` stays one name, so it's refused rather than read as dbo.sales
    view = f"__lakehouse_history_{len(views)}"
    views[f"history:{name.lower()}:{len(views)}"] = view
    con.register(view, _lakehouse().table_history(name).to_arrow())
    return cast(exp.Expression, sqlglot.parse_one(f"SELECT * FROM {view} ORDER BY version DESC", read="duckdb"))


READ_ONLY = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)


def check_read_only(statements: list[exp.Expression]) -> exp.Expression:
    """The one statement of a sandboxed query, if it only reads (SELECT, WITH ... SELECT, UNION)."""
    if len(statements) != 1:
        raise ValueError("Send one statement at a time.")
    statement = statements[0]
    if not isinstance(statement, READ_ONLY) or any(
            isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
                              exp.Command, exp.Copy)) for node in statement.walk()):
        raise PermissionError("Only queries that read (SELECT) can run here.")
    return statement


class KfDuckDB(DuckDB):
    """DuckDB's dialect, with every lambda written as ``LAMBDA x : body``. DuckDB 2.0 refuses the old
    ``x -> body`` ("Deprecated lambda arrow"); DuckDB 1.3 and later read both. sqlglot before 30.25
    writes the arrow, including in lambdas it makes itself (initcap), unless a lambda is marked
    ``colon`` (#1341)."""

    class Generator(DuckDB.Generator):  # type: ignore[misc,valid-type]  # sqlglot sets Generator per dialect at run time
        def lambda_sql(self, expression: exp.Lambda, arrow_sep: str = "->", wrap: bool = True) -> str:
            if not expression.args.get("colon"):
                expression = expression.copy()
                expression.set("colon", True)
            return super().lambda_sql(expression, arrow_sep=arrow_sep, wrap=wrap)

        def ordered_sql(self, expression: exp.Ordered) -> str:
            """sqlglot leaves out an ascending sort's NULLS LAST, as DuckDB's own default; the Spark
            session's default puts NULLs first, so ``ORDER BY g NULLS LAST`` would sort them first."""
            sql = super().ordered_sql(expression)
            if expression.args.get("desc") or expression.args.get("nulls_first") is not False:
                return sql
            head = self.sql(expression, "this") + (" ASC" if expression.args.get("desc") is False else "")
            if sql.startswith(head) and not sql[len(head):].startswith(" NULLS"):
                sql = f"{head} NULLS LAST{sql[len(head):]}"
            return sql


def duckdb_sql(statement: exp.Expr) -> str:
    """``statement`` as DuckDB SQL. sqlglot leaves out what DuckDB has no way to say (such as
    array_sort's comparator) and only logs it, so the query would answer differently from Fabric;
    here that is a ValueError instead, and nothing runs (#1253)."""
    try:
        return statement.sql(KfDuckDB, unsupported_level=sqlglot.ErrorLevel.RAISE)
    except UnsupportedError as exc:
        reason = str(exc).splitlines()[0] if str(exc) else "sqlglot can't write part of it in DuckDB SQL."
        raise ValueError(f"Part of this query can't run on this PC the way it runs in Fabric, so nothing ran: "
                         f"{reason} Run it in Fabric, or rewrite that part.") from None


def _connect() -> duckdb.DuckDBPyConnection:
    # UTC like Fabric's Spark sessions; otherwise year() and date_format() follow this PC's time zone.
    # SET, not connect's config: config makes DuckDB try to download ICU, which fails offline.
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    spark_hash.register(con)  # sha2 at 224, 384 and 512 bits
    return keep_try_cast(con)


def _arrow(result: duckdb.DuckDBPyConnection | None) -> pa.Table:
    if result is None or not result.description:
        return pa.table({})
    return (getattr(result, "to_arrow_table", None) or result.fetch_arrow_table)()


def prepare_sandboxed(query: str, dialect: str = "spark", memory_mb: int | None = None,
                      threads: int | None = None) -> tuple[duckdb.DuckDBPyConnection, str]:
    """A connection with ``query``'s lakehouse tables registered and its configuration locked, and
    the one read-only statement to run on it (``execute_sandboxed``).

    The tables need the attached lakehouse only while they are registered here (each dataset keeps
    its own storage token), so a caller can run the statement after letting the lakehouse go.
    ``memory_mb`` and ``threads`` are set before the configuration is locked."""
    con = _connect()
    try:
        views: dict[str, str] = {}
        statement = check_read_only([history_query(s, con, views) for s in parse(query, dialect)])
        bind_tables(statement, con, views)
        if memory_mb:
            con.execute(f"SET memory_limit = '{int(memory_mb)}MB'")
        if threads:
            con.execute(f"SET threads = {int(threads)}")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        statement = _decimal_types(statement, con, dialect)  # only once the connection is locked
    except BaseException:
        con.close()
        raise
    return con, duckdb_sql(statement)


def execute_sandboxed(con: duckdb.DuckDBPyConnection, statement: str) -> pa.Table:
    """Run a statement from ``prepare_sandboxed``. ``con.interrupt()`` from another thread stops it
    (duckdb.InterruptException); an interrupt sent before DuckDB starts the statement is lost."""
    return _arrow(for_arrow(con, statement))


def _decimal_types(statement: exp.Expression, con: duckdb.DuckDBPyConnection, dialect: str) -> exp.Expression:
    """Spark SQL's decimal result types and div (spark_decimals), as a notebook's spark.sql gives them: without
    them floor(-1.25) div 7 was DuckDB's double division, -0.2857..., where Spark gives 0. And to_json,
written from its argument's type as Spark writes it (spark_json)."""
    if sql_dialects.get(dialect)["id"] != "spark":
        return statement
    statement = cast(exp.Expression, spark_decimals.spark_decimal_types(statement, con))
    return cast(exp.Expression, spark_complex.spark_complex_types(statement, con, kinds=("to_json",)))


def _execute(query: str, dialect: str, sandbox: bool) -> tuple[duckdb.DuckDBPyConnection, Any]:
    """Run every statement in ``query``; the connection and the last one's result (None for none).

    ``sandbox``: one read-only statement, and DuckDB's external access is turned off (and locked)
    once the lakehouse tables are registered, so the query can't read local files, load
    extensions or reach the network (external AI tools, integrations/executions.py)."""
    if sandbox:
        con, text = prepare_sandboxed(query, dialect)
        return con, for_arrow(con, text)
    con = _connect()
    views: dict[str, str] = {}
    result = None
    statements = parse(query, dialect)
    for i, statement in enumerate(statements):
        statement = history_query(statement, con, views)
        bind_tables(statement, con, views, lambda name: is_local(con, name))
        text = duckdb_sql(_decimal_types(statement, con, dialect))
        if i == len(statements) - 1:
            result = for_arrow(con, text)
        else:
            run_through(con, text)
    return con, result


def run(query: str, dialect: str = "spark", sandbox: bool = False) -> pa.Table:
    """Run every statement in ``query`` and return the last one's whole result (see _execute)."""
    _con, result = _execute(query, dialect, sandbox)
    return _arrow(result)


BATCH_ROWS = 50_000  # rows held in memory at a time when a result is streamed


class Stream:
    """The last statement's result, read in batches of at most ``batch_rows`` rows so a large
    result is never held whole on this side (see _execute). ``schema`` is None when the last
    statement returns no result set. Use it as a context manager so the connection is closed."""

    def __init__(self, query: str, dialect: str = "spark", sandbox: bool = False, batch_rows: int = BATCH_ROWS):
        self._con, result = _execute(query, dialect, sandbox)
        reader = None
        if result is not None and result.description:
            reader = arrow_reader(result, batch_rows)
        self._reader = reader
        self.schema: pa.Schema | None = self._reader.schema if self._reader is not None else None

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        return iter(self._reader) if self._reader is not None else iter(())

    def __enter__(self) -> "Stream":
        return self

    def __exit__(self, *exc: Any) -> None:
        self._con.close()


def head(query: str, dialect: str = "spark", sandbox: bool = False, limit: int = 1000) -> tuple[pa.Table, int]:
    """The first ``limit`` rows and the result's full row count. Later rows are counted as they
    stream past and dropped, so memory holds the first rows and one batch."""
    with Stream(query, dialect, sandbox) as result:
        if result.schema is None:
            return pa.table({}), 0
        kept: list[pa.RecordBatch] = []
        have = total = 0
        for batch in result:
            total += batch.num_rows
            if have < limit:
                kept.append(batch.slice(0, limit - have))
                have += kept[-1].num_rows
        return pa.Table.from_batches(kept, schema=result.schema), total
