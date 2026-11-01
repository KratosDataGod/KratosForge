"""SQL over the attached lakehouse's tables, run on this PC with DuckDB.

Queries are written the way Fabric's SQL query tab takes them, in Spark SQL.
sqlglot translates each statement to DuckDB SQL, and every table it names is
read from OneLake as a Delta dataset, so DuckDB fetches only the columns and
files the query needs. Queries never change the lakehouse; writing a result
is ``lakehouse.write_table``.
"""

from __future__ import annotations

import itertools
import re
from typing import Any, Callable, Iterator, cast

import duckdb
import pyarrow as pa
import sqlglot
from sqlglot import exp
from sqlglot.dialects.duckdb import DuckDB
from sqlglot.errors import ParseError, TokenError, UnsupportedError
from sqlglot.tokens import TokenType

from . import lakehouse, sql_dialects
from .duckdb_rows import keep_try_cast

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
        statements = sqlglot.parse(query, read=chosen["sqlglot"])
        if chosen["id"] == "spark":
            statements = [spark_fixes(s) for s in statements if s is not None]
    except ParseError as first_error:
        if chosen["id"] == "spark" and _only_empty_structs_fail(query):
            raise ValueError(_EMPTY_STRUCT) from None
        if chosen["id"] != "spark":
            raise ValueError(f"Could not read the query: {sql_dialects.parse_problem(first_error)}") from None
        try:
            statements = sqlglot.parse(query, read="duckdb")
        except ParseError:
            raise ValueError(f"Could not read the query: {sql_dialects.parse_problem(first_error)}") from None
    statements = [s for s in statements if s is not None]
    if not statements:
        raise ValueError("The query is empty.")
    return statements


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
        sqlglot.parse(filled, read="spark")
    except ParseError:
        return False
    return True


# Spark's date and time unit names that DuckDB doesn't know ('MM' is minutes to DuckDB if anything).
_SPARK_UNITS = {"YYYY": "YEAR", "YY": "YEAR", "MON": "MONTH", "MM": "MONTH", "DD": "DAY"}


def spark_fixes(statement: exp.Expression) -> exp.Expression:
    """What a Spark SQL statement read by sqlglot needs to answer on DuckDB as it does on Spark:
    Spark's column names, its date functions and substring from 0. The Spark SQL path here and
    the DuckDB engine's Databricks SQL (engines/local_sql.py) both use it, so they can't drift."""
    return _spark_functions(spark_substring(_spark_dates(_spark_names(statement))))


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
    # conv(num, from, to), as Spark's NumberConverter: the digits up to the first invalid one, read
    # as an unsigned 64-bit number (more saturates to all ones); a negative number with a positive
    # target base is its two's complement, and a negative target base prints a signed number.
    "CONV": "(LIST_TRANSFORM([{{'s': CAST({0} AS VARCHAR), 'f': CAST({1} AS INTEGER), 't': CAST({2} AS INTEGER)}}], "
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
    for param, value in reversed(list(zip(func.expressions, values))):
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
# DuckDB doesn't honour an interrupt inside one row's list, so a longer array is refused (#1419).
_ARRAY_SORT_BY_LIMIT = 10_000
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
    return cast(exp.Expression, sqlglot.parse_one(template.format(*(duckdb_sql(a) for a in args)), read="duckdb"))


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


def _spark_functions(statement: exp.Expression) -> exp.Expression:
    """Spark functions DuckDB answers differently, rewritten to answer as Spark does (#1105):
    dayofweek counts Sunday as 1, sort_array puts NULL first ascending and last descending,
    array_sort puts NULL last, percentile interpolates, array_distinct, array_union, array_intersect
    and array_except keep first-seen order and one NULL, arrays_overlap is NULL when only a NULL could match, last_day and
    next_day take text, pmod moves a negative remainder by the divisor, and bround, conv, soundex,
    format_number and exists (with a lambda) are written in DuckDB SQL (#1168), as are zip_with,
    map_filter, transform_keys, transform_values and aggregate/reduce with a finish lambda (#1371). Deepest first, so a rewritten argument is
    written out already rewritten."""
    _spark_lambdas(statement)
    _no_reserved_names(statement)
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
        elif isinstance(node, exp.Quantile):
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
                node.replace(_from_template(_ANON_TEMPLATES[name], args))
    return statement


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


def _spark_dates(statement: exp.Expression) -> exp.Expression:
    """Keep Spark's date results where sqlglot's DuckDB SQL differs: date_add and date_sub return
    a date (DuckDB's date + INTERVAL is a timestamp), trunc(date, 'MM') returns a date, and Spark's
    unit spellings ('MM', 'YYYY') become ones DuckDB reads."""
    for node in list(statement.find_all(exp.TsOrDsAdd, exp.DateTrunc, exp.TimestampTrunc)):
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


_SLICE_INDEX_0 = ("The index 0 is invalid. An index shall be either < 0 or > 0 "
                  "(the first element has index 1).")
_SLICE_LENGTH = "Unexpected value for length in function slice: length must be greater than or equal to 0."


# Functions this module rewrites into longer DuckDB SQL; a select of one keeps the name Spark gives it.
_REWRITTEN = (exp.IgnoreNulls, exp.AddMonths, exp.StrToUnix, exp.Substring, exp.ArraySlice, exp.MonthsBetween,
              exp.TsOrDsAdd, exp.DateTrunc, exp.TimestampTrunc, exp.DayOfWeek, *_SORTS,
              exp.Quantile, exp.ArrayDistinct, exp.ArrayIntersect, exp.ArrayExcept, exp.LastDay, exp.NextDay)


def _spark_names(statement: exp.Expression) -> exp.Expression:
    """Name a selected expression this module rewrites as Spark names it ("add_months(d, 1)"), not after
    the DuckDB SQL it becomes."""
    for select in statement.find_all(exp.Select):
        for i, column in enumerate(select.expressions):
            if isinstance(column, (exp.Alias, exp.Column, exp.Star)) or not (
                    column.find(*_REWRITTEN) or any(str(a.this).upper() in _ANON_TEMPLATES
                                                    or str(a.this).upper() in _MAP_LAMBDAS
                                                    for a in column.find_all(exp.Anonymous))
                    or any(r.args.get("finish") is not None for r in column.find_all(exp.Reduce))):
                continue
            name = column.sql("spark", normalize_functions="lower")
            select.expressions[i] = exp.alias_(column, name, quoted=True)
            select.expressions[i].parent = select
    return statement


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
    in a window)."""
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
            target = ignore.parent
        else:
            text, target = f"{name}({value}) FILTER (WHERE {value} IS NOT NULL)", ignore
        swapped = target.replace(sqlglot.parse_one(text, read="duckdb"))
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
        column = constraint.parent.parent if constraint.parent else None
        if isinstance(column, exp.ColumnDef) and isinstance(column.parent, exp.DataType):
            constraint.parent.pop()
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
            f"CASE WHEN {v} = 0 AND typeof({duckdb_sql(item.this)}) LIKE '%[]' "
            f"THEN error({exp.Literal.string(_SLICE_INDEX_0).sql('duckdb')}) ELSE {v} END", read="duckdb")])
    for between in list(node.find_all(exp.MonthsBetween)):
        later, earlier = (_as_timestamp(between.this), _as_timestamp(between.expression))
        months = (f"((year({later}) * 12 + month({later})) - (year({earlier}) * 12 + month({earlier})))")
        same = (f"day({later}) = day({earlier}) OR (day({later}) = day(last_day({later})) "
                f"AND day({earlier}) = day(last_day({earlier})))")
        seconds = f"(epoch({later}) - epoch(date_trunc('day', {later})) - epoch({earlier}) + epoch(date_trunc('day', {earlier})))"
        value = (f"CASE WHEN {same} THEN CAST({months} AS DOUBLE) ELSE {months} + "
                 f"((day({later}) - day({earlier})) * 86400 + {seconds}) / 2678400.0 END")
        roundoff = between.args.get("roundoff")
        if not (isinstance(roundoff, exp.Boolean) and roundoff.this is False):
            value = f"round({value}, 8)"
        swapped = between.replace(sqlglot.parse_one(value, read="duckdb"))
        node = swapped if between is node else node
    for unix in list(node.find_all(exp.StrToUnix)):
        value = duckdb_sql(unix.this)
        fmt, has_year = _strptime_format(unix) if unix.args.get("format") else ("'%Y-%m-%d %H:%M:%S'", True)
        # Without a year Spark reads the date as 1970-01-01; strptime starts from 1900-01-01.
        parsed = f"try_strptime(CAST({value} AS VARCHAR), {fmt})" + ("" if has_year else " + INTERVAL 70 YEAR")
        swapped = unix.replace(sqlglot.parse_one(
            f"CAST(floor(epoch(CASE WHEN typeof({value}) IN ({_TIME_TYPES}) THEN TRY_CAST({value} AS TIMESTAMPTZ) "
            f"ELSE CAST({parsed} AS TIMESTAMPTZ) END)) AS BIGINT)", read="duckdb"))
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
    return node


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
        name = ".".join(p for p in (table.catalog, table.db, table.name) if p)
        point = _time_travel(table)  # VERSION AS OF n, TIMESTAMP AS OF '...', or Databricks' sales@v3
        opened = None
        if point is None:
            # The latest version is looked up each time, so a table written since an earlier statement
            # on a long-lived connection (spark on this PC) is read as it is now, as Spark does. Its
            # location is part of the key: in a proof, a table it wrote is read from the proof's copy.
            opened = lakehouse.open_table(name)
            key = f"{name.lower()}@{opened.table_uri}#{opened.version()}"
        else:
            key = f"{name.lower()}@{point}"
        if key not in views:
            views[key] = f"__lakehouse_table_{next(_VIEW_NAMES)}"
            opened = opened or lakehouse.open_table(name, **point_args(point))
            con.register(views[key], opened.to_pyarrow_dataset())
        # Keep the name the query used as the alias, so sales.Country still resolves.
        alias = table.args.get("alias") or exp.TableAlias(this=exp.to_identifier(table.name))
        table.replace(exp.Table(this=exp.to_identifier(views[key]), alias=alias))


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
    name = ".".join(p for p in (table.catalog, table.db, table.name) if p)
    view = f"__lakehouse_history_{len(views)}"
    views[f"history:{name.lower()}:{len(views)}"] = view
    con.register(view, lakehouse.table_history(name).to_arrow())
    return sqlglot.parse_one(f"SELECT * FROM {view} ORDER BY version DESC", read="duckdb")


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
    except BaseException:
        con.close()
        raise
    return con, duckdb_sql(statement)


def execute_sandboxed(con: duckdb.DuckDBPyConnection, statement: str) -> pa.Table:
    """Run a statement from ``prepare_sandboxed``. ``con.interrupt()`` from another thread stops it
    (duckdb.InterruptException); an interrupt sent before DuckDB starts the statement is lost."""
    return _arrow(con.execute(statement))


def _execute(query: str, dialect: str, sandbox: bool) -> tuple[duckdb.DuckDBPyConnection, Any]:
    """Run every statement in ``query``; the connection and the last one's result (None for none).

    ``sandbox``: one read-only statement, and DuckDB's external access is turned off (and locked)
    once the lakehouse tables are registered, so the query can't read local files, load
    extensions or reach the network (external AI tools, integrations/executions.py)."""
    if sandbox:
        con, text = prepare_sandboxed(query, dialect)
        return con, con.execute(text)
    con = _connect()
    views: dict[str, str] = {}
    result = None
    for statement in parse(query, dialect):
        statement = history_query(statement, con, views)
        bind_tables(statement, con, views, lambda name: is_local(con, name))
        result = con.execute(duckdb_sql(statement))
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
            reader = (getattr(result, "to_arrow_reader", None) or result.fetch_record_batch)(batch_rows)
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
