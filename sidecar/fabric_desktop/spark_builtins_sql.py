"""Spark SQL built-in functions that DuckDB lacks or answers differently, written as DuckDB SQL that
gives Spark's value (the Fails and Differs rows of docs/spark-functions.md).

``rewrite(statement)`` is the last step of sql.spark_fixes, so the Spark SQL a notebook runs on
this PC and the DuckDB engine's Databricks SQL both get it. It runs after every other rewrite there,
so nothing it writes is rewritten again; each argument is the person's own node, already rewritten.

Functions that can't give Spark's value here (sketches, XPath, AES, session windows) are refused by
name with a message, so a cell stops before anything runs with another answer.
"""

from __future__ import annotations

import functools
import re
from typing import Callable, cast

import sqlglot
from sqlglot import exp

from .spark_hash import hash_sql

# ---- helpers


def _with_args(template: str | exp.Expression, args: list[exp.Expression]) -> exp.Expression:
    """``template`` read as DuckDB SQL (or already read: a copy is used), with the arguments in place
    of fj_arg0, fj_arg1, ... (as sql._with_args). Lambda names in templates start with kf_, which a
    query can't use."""
    tree = (template.copy() if isinstance(template, exp.Expression)
            else cast(exp.Expression, sqlglot.parse_one(template, read="duckdb")))
    for column in list(tree.find_all(exp.Column)):
        if column.name.startswith("fj_arg") and not column.table:
            column.replace(args[int(column.name[6:])].copy())
    return tree


def _refuse(name: str, why: str, instead: str = "") -> Callable[..., exp.Expression]:
    def refuse(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression:
        raise ValueError(f"{name}() can't run on this PC the way it runs in Fabric, so nothing ran: {why} "
                         f"Run the cell in Fabric{', or ' + instead if instead else ''}.")
    return refuse


def _template(text: str, count: int | tuple[int, ...]) -> Callable[..., exp.Expression | None]:
    counts = (count,) if isinstance(count, int) else count

    def build(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
        return _with_args(text, args) if len(args) in counts else None
    return build


def _args(node: exp.Expression) -> list[exp.Expression]:
    """A typed node's arguments in call order (this, then the rest as the function lists them)."""
    out: list[exp.Expression] = []
    for key in type(node).arg_types:
        value = node.args.get(key)
        if isinstance(value, list):
            out.extend(value)
        elif isinstance(value, exp.Expression):
            out.append(value)
    return out


_TS = "TRY_CAST(fj_arg0 AS TIMESTAMP)"
_DATE = "TRY_CAST(fj_arg0 AS DATE)"

# ---- each function, as DuckDB SQL

# Aggregates. Spark's any/some/every are bool_or/bool_and; std is the sample standard deviation;
# first_value/last_value without OVER are first/last; try_avg and try_sum give NULL where avg and
# sum would overflow, which a DuckDB sum (HUGEINT, DECIMAL(38)) doesn't reach for Spark's ranges.
_AGGREGATES: dict[str, Callable[..., exp.Expression | None]] = {
    "SOME": _template("BOOL_OR(fj_arg0)", 1),
    "BOOL_OR": _template("BOOL_OR(fj_arg0)", 1),
    "EVERY": _template("BOOL_AND(fj_arg0)", 1),
    "STD": _template("STDDEV_SAMP(fj_arg0)", 1),
    "TRY_AVG": _template("AVG(fj_arg0)", 1),
    "TRY_SUM": _template("SUM(fj_arg0)", 1),
}

# Spark's skewness and kurtosis are the population ones (no small-sample correction). DuckDB's
# skewness is the sample one, so it is scaled back; kurtosis_pop is Spark's excess kurtosis. With
# no spread (m2 = 0) Spark gives NULL.
_SKEWNESS = ("CASE WHEN COUNT(fj_arg0) < 2 OR VAR_POP(fj_arg0) = 0 THEN NULL WHEN COUNT(fj_arg0) = 2 THEN 0.0 "
             "ELSE SKEWNESS(fj_arg0) * (COUNT(fj_arg0) - 2) / SQRT(COUNT(fj_arg0) * (COUNT(fj_arg0) - 1)) END")
_KURTOSIS = "CASE WHEN COUNT(fj_arg0) < 1 OR VAR_POP(fj_arg0) = 0 THEN NULL ELSE KURTOSIS_POP(fj_arg0) END"

# Generators in a select list: one row per element, columns named as Spark names them. An empty or
# NULL array gives one row of NULLs for the _outer forms.
_EXPLODE_OUTER = "UNNEST(CASE WHEN fj_arg0 IS NULL OR LEN(fj_arg0) = 0 THEN [NULL] ELSE fj_arg0 END)"
_POSEXPLODE_OUTER = ("UNNEST(CASE WHEN fj_arg0 IS NULL OR LEN(fj_arg0) = 0 THEN [{'pos': NULL, 'col': NULL}] "
                     "ELSE LIST_TRANSFORM(fj_arg0, (kf_v, kf_i) -> {'pos': CAST(kf_i - 1 AS INTEGER), 'col': kf_v}) END, "
                     "max_depth := 2)")
_INLINE = "UNNEST(fj_arg0, max_depth := 2)"
_INLINE_OUTER = ("UNNEST(CASE WHEN fj_arg0 IS NULL OR LEN(fj_arg0) = 0 THEN [NULL] ELSE fj_arg0 END, "
                 "max_depth := 2)")

# Spark's CRC-32 (java.util.zip.CRC32) of the UTF-8 bytes of text, or of binary, bit by bit.
_CRC32 = (
    "(LIST_TRANSFORM([CASE WHEN TYPEOF(fj_arg0) = 'BLOB' THEN HEX(fj_arg0) ELSE HEX(ENCODE(CAST(fj_arg0 AS VARCHAR))) END], "
    "kf_h -> CASE WHEN kf_h IS NULL THEN NULL ELSE XOR(LIST_REDUCE(LIST_PREPEND(4294967295::BIGINT, "
    "LIST_TRANSFORM(RANGE(LENGTH(kf_h) // 2), kf_i -> ('0x' || kf_h[2 * kf_i + 1:2 * kf_i + 2])::BIGINT)), "
    "(kf_c, kf_x) -> LIST_REDUCE(LIST_PREPEND(XOR(kf_c, kf_x), [0, 1, 2, 3, 4, 5, 6, 7]), "
    "(kf_r, kf_k) -> CASE WHEN kf_r & 1 = 1 THEN XOR(kf_r >> 1, 3988292384) ELSE kf_r >> 1 END))"
    ", 4294967295) END))[1]")

# bitmap_construct_agg: Spark's 4096-byte bitmap, position p in bit p % 8 (lowest first) of byte p // 8.
_BITMAP_CONSTRUCT_AGG = (
    "(LIST_TRANSFORM([CAST(BITSTRING_AGG(fj_arg0, 0, 32767) AS VARCHAR)], kf_s -> CASE WHEN kf_s IS NULL THEN NULL "
    "ELSE UNHEX(ARRAY_TO_STRING(LIST_TRANSFORM(RANGE(4096), kf_i -> LPAD(HEX(CAST('0b' || "
    "REVERSE(SUBSTR(kf_s, kf_i * 8 + 1, 8)) AS INTEGER)), 2, '0')), '')) END))[1]")

# bitmap_count and bitmap_or_agg read a bitmap's bytes as DuckDB's BIT. A zero byte is added first,
# because an empty BLOB can't be cast to BIT and the extra byte has no bits set. bitmap_or_agg ORs
# the first 4096 bytes of each bitmap into Spark's 4096-byte result: all zero bytes when no row has one.
_BITMAP_COUNT = "CAST(BIT_COUNT(CAST(fj_arg0 || UNHEX('00') AS BIT)) AS BIGINT)"
_BITMAP_OR_AGG = ("COALESCE(CAST(BIT_OR(CAST(LEFT(RPAD(CAST(CAST(fj_arg0 || UNHEX('00') AS BIT) AS VARCHAR), "
                  "32768, '0'), 32768) AS BIT)) AS BLOB), UNHEX(REPEAT('00', 4096)))")

# luhn_check: digits only, and the Luhn sum (every second digit from the right doubled) ends in 0.
_LUHN = ("(LIST_TRANSFORM([CAST(fj_arg0 AS VARCHAR)], kf_s -> CASE WHEN kf_s IS NULL THEN NULL "
         "WHEN NOT REGEXP_MATCHES(kf_s, '^[0-9]+$') THEN FALSE ELSE LIST_SUM(LIST_TRANSFORM(RANGE(LENGTH(kf_s)), "
         "kf_i -> (LIST_TRANSFORM([CAST(SUBSTR(REVERSE(kf_s), kf_i + 1, 1) AS INTEGER) * (1 + kf_i % 2)], "
         "kf_d -> CASE WHEN kf_d > 9 THEN kf_d - 9 ELSE kf_d END))[1])) % 10 = 0 END))[1]")

# substring_index(s, delim, n): before the n-th delim from the left (n > 0), after the n-th from the
# right (n < 0), the whole text when there are fewer, '' for n = 0 or an empty delim.
_SUBSTRING_INDEX = (
    "CASE WHEN fj_arg0 IS NULL OR fj_arg1 IS NULL OR fj_arg2 IS NULL THEN NULL "
    "WHEN fj_arg2 = 0 OR fj_arg1 = '' THEN '' "
    "WHEN fj_arg2 > 0 THEN ARRAY_TO_STRING(LIST_SLICE(STRING_SPLIT(fj_arg0, fj_arg1), 1, fj_arg2), fj_arg1) "
    "ELSE ARRAY_TO_STRING(LIST_SLICE(STRING_SPLIT(fj_arg0, fj_arg1), "
    "GREATEST(LEN(STRING_SPLIT(fj_arg0, fj_arg1)) + fj_arg2 + 1, 1), LEN(STRING_SPLIT(fj_arg0, fj_arg1))), fj_arg1) END")

_FIND_IN_SET = ("CASE WHEN fj_arg0 IS NULL OR fj_arg1 IS NULL THEN NULL WHEN STRPOS(fj_arg0, ',') > 0 THEN 0 "
                "ELSE COALESCE(LIST_POSITION(STRING_SPLIT(fj_arg1, ','), fj_arg0), 0) END")

# width_bucket(v, min, max, n), as Spark's: 0 below the range, n + 1 above it, either direction.
_WIDTH_BUCKET = (
    "CASE WHEN fj_arg0 IS NULL OR fj_arg1 IS NULL OR fj_arg2 IS NULL OR fj_arg3 IS NULL THEN NULL "
    "WHEN fj_arg3 <= 0 OR fj_arg1 = fj_arg2 OR ISNAN(CAST(fj_arg0 AS DOUBLE)) OR ISINF(CAST(fj_arg1 AS DOUBLE)) "
    "OR ISINF(CAST(fj_arg2 AS DOUBLE)) THEN NULL "
    "WHEN fj_arg1 < fj_arg2 THEN CASE WHEN fj_arg0 < fj_arg1 THEN 0 WHEN fj_arg0 >= fj_arg2 THEN fj_arg3 + 1 "
    "ELSE CAST(FLOOR((CAST(fj_arg0 AS DOUBLE) - fj_arg1) * fj_arg3 / (CAST(fj_arg2 AS DOUBLE) - fj_arg1)) AS BIGINT) + 1 END "
    "ELSE CASE WHEN fj_arg0 > fj_arg1 THEN 0 WHEN fj_arg0 <= fj_arg2 THEN fj_arg3 + 1 "
    "ELSE CAST(FLOOR((CAST(fj_arg1 AS DOUBLE) - fj_arg0) * fj_arg3 / (CAST(fj_arg1 AS DOUBLE) - fj_arg2)) AS BIGINT) + 1 END END")

# Small x: Math.expm1 and Math.log1p keep the digits exp(x) - 1 and ln(1 + x) lose.
_EXPM1 = ("CASE WHEN ABS(CAST(fj_arg0 AS DOUBLE)) < 1e-5 THEN CAST(fj_arg0 AS DOUBLE) + POW(CAST(fj_arg0 AS DOUBLE), 2) / 2 "
          "+ POW(CAST(fj_arg0 AS DOUBLE), 3) / 6 ELSE EXP(CAST(fj_arg0 AS DOUBLE)) - 1 END")
_LOG1P = ("CASE WHEN CAST(fj_arg0 AS DOUBLE) <= -1 THEN NULL WHEN ABS(CAST(fj_arg0 AS DOUBLE)) < 1e-5 "
          "THEN CAST(fj_arg0 AS DOUBLE) - POW(CAST(fj_arg0 AS DOUBLE), 2) / 2 + POW(CAST(fj_arg0 AS DOUBLE), 3) / 3 "
          "ELSE LN(1 + CAST(fj_arg0 AS DOUBLE)) END")

# Spark's typeof names: int, bigint, string, decimal(p,s), array<int>, ...
_TYPE_NAMES = [("TINYINT", "tinyint"), ("SMALLINT", "smallint"), ("BIGINT", "bigint"), ("HUGEINT", "decimal(38,0)"),
               ("INTEGER", "int"), ("VARCHAR", "string"), ("DOUBLE", "double"), ("FLOAT", "float"),
               ("BOOLEAN", "boolean"), ("TIMESTAMP WITH TIME ZONE", "timestamp"), ("TIMESTAMP", "timestamp_ntz"),
               ("DATE", "date"), ("BLOB", "binary"), ("\"NULL\"", "void"), ("NULL", "void")]


def _typeof(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    if len(args) != 1:
        return None
    # A scalar type, or a list of one (INTEGER[] -> array<int>); deeper types in lower case.
    scalar = "CASE kf_t " + " ".join(f"WHEN '{d}' THEN '{s}'" for d, s in _TYPE_NAMES) + \
             " ELSE CASE WHEN kf_t LIKE 'DECIMAL(%' THEN REPLACE(LOWER(kf_t), ' ', '') ELSE LOWER(kf_t) END END"
    whole = (f"(LIST_TRANSFORM([TYPEOF(fj_arg0)], kf_x -> CASE WHEN kf_x LIKE '%[]' AND kf_x NOT LIKE '%[][]' "
             f"AND kf_x NOT LIKE '%(%' THEN 'array<' || (LIST_TRANSFORM([LEFT(kf_x, LENGTH(kf_x) - 2)], kf_t -> {scalar}))[1] "
             f"|| '>' ELSE (LIST_TRANSFORM([kf_x], kf_t -> {scalar}))[1] END))[1]")
    return _with_args(whole, args)


def _sign(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    # Spark's signum is a double: 1.0, -1.0 or 0.0.
    return _with_args("CAST(SIGN(fj_arg0) AS DOUBLE)", args) if len(args) == 1 else None


def _elt(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    if len(args) < 2:
        return None
    whens = " ".join(f"WHEN {i} THEN fj_arg{i}" for i in range(1, len(args)))
    return _with_args(f"CASE fj_arg0 {whens} ELSE NULL END", args)


# ---- format_string / printf: Java's Formatter, as Spark's

# One format specifier: %[index$][flags][width][.precision]conversion
_JAVA_SPEC = re.compile(r"%(?:(\d+)\$)?([-#+ 0,(<]*)(\d+)?(?:\.(\d+))?([a-zA-Z%])")
_JAVA_INTEGERS = "('TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'HUGEINT', 'UTINYINT', 'USMALLINT', 'UINTEGER', 'UBIGINT')"
# Bits of each signed whole-number type: Java prints a negative one in hex or octal as its two's complement.
_JAVA_BITS = {"TINYINT": 8, "SMALLINT": 16, "INTEGER": 32, "BIGINT": 64}
_JAVA_FLAGS = {"s": "-", "b": "-", "d": "-+ 0,", "x": "-0", "o": "-0", "f": "-+ 0,", "e": "-+ 0"}


def _sql_text(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def java_double_text(x: str) -> str:
    """DuckDB SQL for Java's Double.toString of the DOUBLE ``x`` (Spark's text for a double): the
    shortest digits, as plain decimals from 0.001 up to 10^7 (with .0 for a whole number), else as
    d.dddE±n; NaN, Infinity, -0.0."""
    s = f"CAST(ABS({x}) AS VARCHAR)"
    # DuckDB's shortest digits, in either notation: mantissa digits and the exponent of the first one
    sci = (f"(LIST_TRANSFORM([{{'m': SPLIT_PART({s}, 'e', 1), 'x': COALESCE(TRY_CAST(NULLIF(SPLIT_PART({s}, 'e', 2), '') "
           f"AS INTEGER), 0)}}], kf_n -> (LIST_TRANSFORM([{{'i': SPLIT_PART(kf_n.m, '.', 1), 'd': SPLIT_PART(kf_n.m, '.', 1) "
           f"|| SPLIT_PART(kf_n.m, '.', 2), 'x': kf_n.x}}], kf_p -> (LIST_TRANSFORM([{{'g': RTRIM(LTRIM(kf_p.d, '0'), '0'), "
           f"'e': kf_p.x + LENGTH(kf_p.i) - 1 - (LENGTH(kf_p.d) - LENGTH(LTRIM(kf_p.d, '0')))}}], "
           f"kf_q -> LEFT(kf_q.g, 1) || '.' || COALESCE(NULLIF(SUBSTR(kf_q.g, 2), ''), '0') || 'E' || kf_q.e))[1]))[1]))[1]")
    plain = f"(CASE WHEN CONTAINS({s}, '.') THEN {s} ELSE {s} || '.0' END)"
    return (f"(CASE WHEN {x} IS NULL THEN NULL WHEN ISNAN({x}) THEN 'NaN' WHEN ISINF({x}) THEN "
            f"CASE WHEN {x} > 0 THEN 'Infinity' ELSE '-Infinity' END "
            f"ELSE CASE WHEN SIGNBIT({x}) THEN '-' ELSE '' END || CASE WHEN {x} = 0 THEN '0.0' "
            f"WHEN ABS({x}) >= 0.001 AND ABS({x}) < 10000000 THEN {plain} ELSE {sci} END END)")


def java_value_text(a: str) -> str:
    """DuckDB SQL for Java's String.valueOf of a value, as Spark writes one as text, ready to run (its
    lambdas written as this DuckDB reads them)."""
    from .sql import duckdb_sql  # sql imports this module
    return duckdb_sql(cast(exp.Expression, sqlglot.parse_one(_java_text(a), read="duckdb")))


def _java_text(a: str) -> str:
    """Java's String.valueOf of a value, as %s prints it (a double as Java writes it)."""
    return (f"(CASE WHEN TYPEOF({a}) = 'DOUBLE' THEN {java_double_text(f'TRY_CAST({a} AS DOUBLE)')} "
            f"WHEN TYPEOF({a}) = 'FLOAT' THEN {java_double_text(f'TRY_CAST({a} AS FLOAT)')} "
            f"ELSE CAST({a} AS VARCHAR) END)")


def _refuse_spec(spec: str, why: str) -> ValueError:
    return ValueError(f"format_string() can't run on this PC the way it runs in Fabric, so nothing ran: {spec} {why} "
                      "Run the cell in Fabric, or use %s, %d, %x, %o, %f, %e or %b.")


def java_format_sql(fmt: str, count: int) -> str:
    """DuckDB SQL for Spark's format_string(fmt, fj_arg0, ..., fj_arg{count-1}): Java's Formatter, where
    a NULL argument prints as null (false for %b) and %d, %x, %o take only whole numbers and %f and %e
    only decimals, as in Spark. A conversion this PC can't write as Java does is refused."""
    pieces: list[str] = []
    ordinary = 0
    at = 0
    for m in _JAVA_SPEC.finditer(fmt):
        if m.start() > at:
            pieces.append(_sql_text(fmt[at:m.start()]))
        at = m.end()
        index, flags, width, precision, conversion = m.groups()
        spec = m.group(0)
        if conversion == "%":
            pieces.append("'%'")
            continue
        if conversion == "n":
            pieces.append("CHR(10)")
            continue
        kind = conversion.lower()
        if kind not in _JAVA_FLAGS or (kind in "dxo" and precision is not None):
            raise _refuse_spec(spec, "isn't a conversion this PC writes as Java does.")
        if any(f not in _JAVA_FLAGS[kind] for f in flags):
            raise _refuse_spec(spec, "has a flag this PC doesn't write as Java does.")
        if index is None:
            arg, ordinary = ordinary, ordinary + 1
        else:
            arg = int(index) - 1
        if not 0 <= arg < count:
            raise ValueError(f"format_string(): {spec} has no argument (the format needs more than the {count} given), "
                             "as in Spark.")
        a = f"fj_arg{arg}"
        left = "-" if "-" in flags else ""
        size = f"{left}{width or ''}"
        cut = f".{precision}" if precision is not None else ""
        null = f"PRINTF('%{size}{cut}s', 'null')"  # a NULL prints as null, padded and cut as text
        if kind == "s":
            text = f"(CASE WHEN {a} IS NULL THEN {null} ELSE PRINTF('%{size}{cut}s', {_java_text(a)}) END)"
        elif kind == "b":
            truth = f"(CASE WHEN {a} IS NULL THEN 'false' WHEN TYPEOF({a}) = 'BOOLEAN' THEN CAST({a} AS VARCHAR) ELSE 'true' END)"
            text = f"PRINTF('%{size}{cut}s', {truth})"
        else:
            wrong = f"ERROR('format_string(): ' || {_sql_text(spec)} || ' can''t format a ' || TYPEOF({a}) || ', as in Spark.')"
            fmt_flags = "".join(f for f in flags if f != "-")
            pattern = f"'%{left}{fmt_flags}{width or ''}"
            if kind in "dxo":
                value = a
                if kind != "d":  # a negative number as its two's complement, by its type's size
                    value = (f"(CASE WHEN {a} >= 0 THEN CAST({a} AS UBIGINT) ELSE CAST(CAST({a} AS HUGEINT) + CASE TYPEOF({a}) "
                             + " ".join(f"WHEN '{t}' THEN {2 ** b}" for t, b in _JAVA_BITS.items()) + " END AS UBIGINT) END)")
                ok = f"TYPEOF({a}) IN {_JAVA_INTEGERS}"
                body = f"PRINTF({pattern}{kind}', {value})"
            else:
                places = int(precision) if precision is not None else 6
                ok = f"(TYPEOF({a}) IN ('DOUBLE', 'FLOAT') OR TYPEOF({a}) LIKE 'DECIMAL%')"
                if kind == "f":
                    # Java rounds a double's shortest digits half up; DuckDB's printf rounds its binary value
                    exact = (f"(CASE WHEN TYPEOF({a}) LIKE 'DECIMAL%' THEN CAST({a} AS DECIMAL(38, 18)) "
                             f"ELSE CAST(CAST(CAST({a} AS DOUBLE) AS VARCHAR) AS DECIMAL(38, 18)) END)")
                    rounded = f"ROUND({exact}, {min(places, 18)})"
                    plain = f"PRINTF({pattern}.{places}f', {rounded})"
                    body = (f"(CASE WHEN TYPEOF({a}) LIKE 'DECIMAL%' OR ABS(CAST({a} AS DOUBLE)) < 1e19 THEN {plain} "
                            f"ELSE PRINTF({pattern}.{places}f', CAST({a} AS DOUBLE)) END)")
                else:
                    body = f"PRINTF({pattern}.{places}e', CAST({a} AS DOUBLE))"
                special = (f"WHEN TYPEOF({a}) IN ('DOUBLE', 'FLOAT') AND (ISNAN(CAST({a} AS DOUBLE)) OR ISINF(CAST({a} AS DOUBLE))) "
                           f"THEN PRINTF('%{size}s', {java_double_text(f'TRY_CAST({a} AS DOUBLE)')}) ")
                body = f"(CASE {special}ELSE {body} END)"
            text = f"(CASE WHEN {a} IS NULL THEN {null} WHEN NOT {ok} THEN {wrong} ELSE {body} END)"
        pieces.append(f"UPPER({text})" if conversion.isupper() else text)
    if at < len(fmt):
        pieces.append(_sql_text(fmt[at:]))
    if "%" in _JAVA_SPEC.sub("", fmt):
        raise ValueError(f"format_string(): {fmt!r} has a % that starts no conversion, as in Spark.")
    return "(" + " || ".join(pieces or ["''"]) + ")"


def _format_string(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """format_string/printf with a written format: Java's Formatter, as Spark's (java_format_sql). A
    format that is a column is given to DuckDB's printf, which reads the same %d, %s, %f, %x, %05.2f
    but prints a NULL argument's whole result as NULL."""
    if args and isinstance(args[0], exp.Literal) and args[0].is_string:
        return _with_args(java_format_sql(args[0].this, len(args) - 1), args[1:])
    return exp.Anonymous(this="PRINTF", expressions=[a.copy() for a in args]) if args else None


def _map_zip_with(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """map_zip_with(m1, m2, (k, v1, v2) -> ...): the keys of m1, then m2's other keys, each with the
    lambda's value for it (NULL for a map without the key)."""
    from .sql import _bind  # sql imports this module
    if len(args) != 3 or not isinstance(args[2], exp.Lambda) or len(args[2].expressions) != 3:
        return None
    body = _bind(args[2], ("kf_k", "fj_arg0[kf_k]", "fj_arg1[kf_k]"))
    keys = "LIST_CONCAT(MAP_KEYS(fj_arg0), MAP_KEYS(fj_arg1))"
    return _with_args(f"CASE WHEN fj_arg0 IS NULL OR fj_arg1 IS NULL THEN NULL ELSE MAP_FROM_ENTRIES(LIST_TRANSFORM("
                      f"LIST_FILTER({keys}, (kf_v, kf_i) -> LIST_POSITION({keys}, kf_v) = kf_i), "
                      f"kf_k -> {{'key': kf_k, 'value': {body}}})) END", args[:2])


def _struct(fields: list[tuple[str, exp.Expression]]) -> exp.Struct:
    """A DuckDB struct {'name': value, ...} with these field names."""
    return exp.Struct(expressions=[exp.PropertyEQ(this=exp.to_identifier(n, quoted=True), expression=v)
                                   for n, v in fields])


def _arrays_zip(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """arrays_zip(a, b, ...): one struct per position up to the longest array (NULL past a shorter
    one), its fields named after the arrays when they are columns and "0", "1", ... otherwise, as
    Spark names them. NULL when any array is NULL."""
    if not args:
        return _with_args("[]", [])
    names = [a.name if isinstance(a, exp.Column) else str(i) for i, a in enumerate(args)]
    longest = "GREATEST(" + ", ".join(f"LEN(fj_arg{i})" for i in range(len(args))) + ")"
    tree = _with_args(f"CASE WHEN {' OR '.join(f'fj_arg{i} IS NULL' for i in range(len(args)))} THEN NULL "
                      f"ELSE LIST_TRANSFORM(RANGE({longest}), kf_i -> kf_struct) END", args)
    struct = _struct([(n, _with_args(f"fj_arg{i}[kf_i + 1]", args)) for i, n in enumerate(names)])
    next(c for c in tree.find_all(exp.Column) if c.name == "kf_struct").replace(struct)
    return tree


def _named_tuples(statement: exp.Expression) -> None:
    """A lambda that returns a tuple, (y, x), returns a struct in Spark, its fields named after the
    columns in it (col1, col2, ... for anything else). DuckDB's tuple has no names, so it is written
    as a struct first."""
    for func in statement.find_all(exp.Lambda):
        body = func.this
        if isinstance(body, exp.Tuple) and body.expressions:
            body.replace(_struct([(e.name if isinstance(e, (exp.Column, exp.Identifier)) else f"col{i + 1}", e.copy())
                                  for i, e in enumerate(body.expressions)]))


def _stack(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """stack(n, v1, v2, ...): n rows of ceil(k / n) columns col0, col1, ..., padded with NULL."""
    if len(args) < 2 or not args[0].is_int or int(args[0].name) < 1:
        return None
    rows, values = int(args[0].name), args[1:]
    width = -(-len(values) // rows)
    structs = []
    for r in range(rows):
        fields = []
        for c in range(width):
            i = r * width + c
            fields.append(f"'col{c}': " + (f"fj_arg{i + 1}" if i < len(values) else "NULL"))
        structs.append("{" + ", ".join(fields) + "}")
    return _with_args(f"UNNEST([{', '.join(structs)}], max_depth := 2)", args)


def _json_tuple(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """json_tuple(json, k1, k2, ...): one row, columns c0, c1, ... with each key's value as text
    (an object or list as its JSON), NULL when the key is missing or the text isn't JSON."""
    if len(args) < 2:
        return None
    fields = ", ".join(f"'c{i}': CASE WHEN JSON_VALID(fj_arg0) THEN JSON_EXTRACT_STRING(fj_arg0, "
                       f"'$.\"' || REPLACE(fj_arg{i + 1}, '\"', '\\\"') || '\"') END" for i in range(len(args) - 1))
    return _with_args(f"UNNEST([{{{fields}}}], max_depth := 2)", args)


def _mask(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """mask(s[, upper, lower, digit, other]): letters and digits replaced, as Spark's. Each
    replacement must be written as text or NULL (NULL leaves that kind as it is)."""
    defaults: list[str | None] = ["X", "x", "n", None]
    given = args[1:]
    if not args or len(given) > 4 or any(not (isinstance(a, exp.Null) or a.is_string) for a in given):
        return None
    chars = [None if isinstance(a, exp.Null) else (a.name[:1] if a.name else None) for a in given]
    chars += defaults[len(chars):]
    out = "fj_arg0"
    for char, pattern in zip(chars, (r"\p{Lu}", r"\p{Ll}", r"\p{Nd}", r"[^\p{Lu}\p{Ll}\p{Nd}]"), strict=True):
        if char is not None:
            text = char.replace("\\", "\\\\").replace("'", "''")
            out = f"REGEXP_REPLACE({out}, '{pattern}', '{text}', 'g')"
    return _with_args(out, args[:1])


_CSV_REFUSED = ("ERROR('to_csv() can''t run on this PC the way it runs in Fabric, so nothing ran: Spark writes "
                "doubles, floats and timestamps in its own format. Run the cell in Fabric, or cast the value "
                "to a string first.')")


def _to_csv(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """to_csv(struct(...)) or to_csv(named_struct(...)): the values separated by commas, as Spark's
    CSV writer writes them with default options: spaces at either end trimmed, NULL as nothing, ''
    as "", and a value with a comma, quote or line break in quotes (a quote in it as \\"). A double,
    float or timestamp, which Spark writes in its own format, stops the query with a message."""
    if len(args) != 1 or not isinstance(args[0], exp.Struct) or not args[0].expressions:
        return None
    values = [e.expression if isinstance(e, exp.PropertyEQ) else e.this if isinstance(e, exp.Alias) else e
              for e in args[0].expressions]
    parts = ", ".join(
        f"CASE WHEN TYPEOF(fj_arg{i}) IN ('DOUBLE', 'FLOAT') OR TYPEOF(fj_arg{i}) LIKE 'TIMESTAMP%' "
        f"THEN {_CSV_REFUSED} ELSE (LIST_TRANSFORM([TRIM(CAST(fj_arg{i} AS VARCHAR))], kf_v -> CASE WHEN kf_v IS NULL "
        f"THEN '' WHEN kf_v = '' THEN '\"\"' WHEN REGEXP_MATCHES(kf_v, '[,\"\\r\\n]') "
        f"THEN '\"' || REPLACE(kf_v, '\"', '\\\"') || '\"' ELSE kf_v END))[1] END" for i in range(len(values)))
    return _with_args(f"ARRAY_TO_STRING([{parts}], ',')", values)


def _from_csv(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """from_csv(text, 'a INT, b DOUBLE'): a struct of the comma-separated values read as the schema's
    types (a value that doesn't fit is NULL, as in Spark's default PERMISSIVE mode). Text with a quote
    in it stops the query with a message, since quoted values aren't read here."""
    from .sql import duckdb_sql  # sql imports this module
    if len(args) != 2 or not args[1].is_string:
        return None
    try:
        schema = sqlglot.parse_one(f"STRUCT<{args[1].name}>", read="spark", into=exp.DataType)
    except sqlglot.errors.ParseError:
        return None
    fields: list[tuple[str, exp.Expression]] = []
    for i, column in enumerate(schema.expressions):
        if not isinstance(column, exp.ColumnDef) or column.args.get("kind") is None:
            return None
        kind = duckdb_sql(column.args["kind"])
        fields.append((column.name, _with_args(f"TRY_CAST(NULLIF(STRING_SPLIT(fj_arg0, ',')[{i + 1}], '') AS {kind})", args)))
    tree = _with_args("CASE WHEN fj_arg0 IS NULL THEN NULL WHEN STRPOS(fj_arg0, '\"') > 0 THEN ERROR('from_csv() "
                      "can''t run on this PC the way it runs in Fabric, so nothing ran: quoted values aren''t read "
                      "here. Run the cell in Fabric.') ELSE kf_struct END", args)
    next(c for c in tree.find_all(exp.Column) if c.name == "kf_struct").replace(_struct(fields))
    return tree


def _try_to_binary(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    fmt = args[1].name.lower().replace("-", "") if len(args) == 2 and args[1].is_string else ("hex" if len(args) == 1 else None)
    template = {"utf8": "ENCODE(fj_arg0)", "hex": "TRY(UNHEX(fj_arg0))", "base64": "TRY(FROM_BASE64(fj_arg0))"}.get(fmt or "")
    return _with_args(template, args[:1]) if template else None


def _timestamp_from(scale: int) -> Callable[..., exp.Expression | None]:
    """timestamp_seconds/millis/micros: the time that many units after 1970-01-01 00:00 UTC."""
    to_micros = "fj_arg0" if scale == 1 else f"TRUNC(CAST(fj_arg0 AS DECIMAL(38, 6)) * {scale})"
    return _template(f"MAKE_TIMESTAMP(CAST({to_micros} AS BIGINT))", 1)


def _make_dt_interval(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    if len(args) > 4:
        return None
    parts = ["TO_DAYS(CAST(fj_arg0 AS INTEGER))", "TO_HOURS(CAST(fj_arg1 AS BIGINT))",
             "TO_MINUTES(CAST(fj_arg2 AS BIGINT))", "TO_MICROSECONDS(CAST(ROUND(fj_arg3 * 1000000) AS BIGINT))"]
    return _with_args(" + ".join(parts[:len(args)]) if args else "INTERVAL 0 SECOND", args)


def _to_timestamp(safe: bool) -> Callable[..., exp.Expression | None]:
    """to_timestamp_ntz/ltz and try_to_timestamp read text as Spark's to_timestamp does."""
    def build(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
        if len(args) == 1:
            return _with_args("TRY_CAST(fj_arg0 AS TIMESTAMP)", args)
        if len(args) != 2 or not args[1].is_string:
            return None  # sqlglot reads a format only when it is written out
        tree = sqlglot.parse_one(f"SELECT TO_TIMESTAMP(fj_arg0, {args[1].sql('spark')})", read="spark").expressions[0]
        for column in list(tree.find_all(exp.Column)):
            if column.name == "fj_arg0":
                column.replace(args[0].copy())
        if safe:  # try_to_timestamp: NULL for text that isn't a timestamp, with ANSI on too
            for strptime in tree.find_all(exp.StrToTime):
                strptime.set("safe", True)
        return tree
    return build


def _make_timestamp(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    if len(args) != 6:
        return None  # with a time zone: left to DuckDB, which refuses it
    return _with_args("TRY(MAKE_TIMESTAMP(fj_arg0, fj_arg1, fj_arg2, fj_arg3, fj_arg4, fj_arg5))", args)


def _octet_length(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    if len(args) != 1:
        return None
    if args[0].is_string:
        return _with_args("OCTET_LENGTH(ENCODE(fj_arg0))", args)
    return _with_args("OCTET_LENGTH(CASE WHEN TYPEOF(fj_arg0) = 'BLOB' THEN fj_arg0::BLOB "
                      "ELSE ENCODE(CAST(fj_arg0 AS VARCHAR)) END)", args)


# A window's start as an instant (TIMESTAMPTZ, so the buckets are counted in UTC from 1970, as in
# Spark), and its end that many microseconds later (a day is 86,400 seconds, as in Spark, even when
# the session's time zone changes its clocks that day). Both are shown in the session's time zone.
_WINDOW_START = ("TIME_BUCKET(CAST(fj_arg1 AS INTERVAL), TRY_CAST(fj_arg0 AS TIMESTAMPTZ), "
                 "TIMESTAMPTZ '1970-01-01 00:00:00+00')")
_WINDOW = ("STRUCT_PACK(\"start\" := CAST(fj_arg0 AS TIMESTAMP), \"end\" := CAST(fj_arg0 + "
           "TO_MICROSECONDS(CAST(ROUND(EPOCH(CAST(fj_arg1 AS INTERVAL)) * 1000000) AS BIGINT)) AS TIMESTAMP))")
_WINDOWS_HERE = "this PC runs window(time, duration) with one duration, given as text such as '5 minutes'."


def _time_window(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression:
    """window(time, '5 minutes'): Spark's tumbling window, a struct of start and end. Windows start
    at whole multiples of the duration since 1970-01-01 UTC, as in Spark. Sliding windows and a start
    offset (window's third and fourth arguments) are refused, as is a duration in months or years,
    which Spark doesn't take."""
    if len(args) != 2:
        _refuse("window", "sliding windows and a start time aren't read here: " + _WINDOWS_HERE)(node, args)
    duration = args[1]
    text = duration.name.strip().lower() if duration.is_string else ""
    text = text[len("interval"):].strip() if text.startswith("interval") else text
    if not re.fullmatch(r"(\d+\s*[a-z]+\s*)+", text) or re.search(r"mon|year", text):
        _refuse("window", "its duration isn't one this PC reads: " + _WINDOWS_HERE)(node, args)
    start = _with_args(_WINDOW_START, [args[0], exp.Literal.string(text)])
    return _with_args(_WINDOW, [start, exp.Literal.string(text)])


def _is_window(node: exp.Expression) -> bool:
    return isinstance(node, exp.Anonymous) and node.name.upper() == "WINDOW" and bool(node.meta.get(_MARK))


def _window_columns(statement: exp.Expression) -> None:
    """Where a query groups by window(...), its ``window`` (and ``window.start``, ``window.end``)
    is that window, as Spark names it. Each query with a window() also skips rows whose time is
    NULL (or isn't a time), as Spark's time windows do."""
    for select in list(statement.find_all(exp.Select)):
        mine = [w for w in select.find_all(exp.Anonymous) if _is_window(w) and w.find_ancestor(exp.Select) is select]
        if not mine:
            continue
        group = select.args.get("group")
        grouped = [w for w in mine if group is not None and w.find_ancestor(exp.Group) is group]
        if grouped:
            window = grouped[0]
            for part in [*select.expressions, select.args.get("having"), select.args.get("order")]:
                if part is None:
                    continue
                for column in list(part.find_all(exp.Column)):
                    if column.find_ancestor(exp.Select) is not select:
                        continue
                    if column.table.lower() == "window" and column.name.lower() in ("start", "end") and not column.args.get("db"):
                        field = exp.Anonymous(this="STRUCT_EXTRACT",
                                              expressions=[window.copy(), exp.Literal.string(column.name.lower())])
                        named = column.parent is select
                        column.replace(exp.alias_(field, column.name) if named else field)
                    elif not column.table and column.name.lower() == "window":
                        named = column.parent is select
                        column.replace(exp.alias_(window.copy(), "window") if named else window.copy())
        for w in mine:
            if w.parent is select:
                w.replace(exp.alias_(w.copy(), "window"))
            if len(w.expressions) >= 1:
                select.where(exp.TryCast(this=w.expressions[0].copy(), to=exp.DataType.build("TIMESTAMPTZ"))
                             .is_(exp.null()).not_(), copy=False)


_SKETCH = "it builds a DataSketches or count-min sketch whose bytes this PC can't make the same way."
_XPATH = "XPath isn't available in the engine on this PC."
@functools.lru_cache(maxsize=32)
def _hash_template(name: str, count: int) -> exp.Expression:
    """hash_sql read once per name and argument count: it is long, and reading it is most of the time."""
    return cast(exp.Expression, sqlglot.parse_one(hash_sql(name, count), read="duckdb"))


def _spark_literal(arg: exp.Expression) -> exp.Expression:
    """-2147483648 is an int to Spark and a BIGINT to DuckDB (which reads 2147483648, then negates it):
    a value hashed by its type must be the type Spark gives it."""
    inner = arg.this if isinstance(arg, exp.Neg) else None
    if isinstance(inner, exp.Literal) and not inner.is_string and inner.this == "2147483648":
        return exp.cast(arg.copy(), exp.DataType.Type.INT)
    return arg


def _hash(name: str) -> Callable[..., exp.Expression]:
    """hash(a, b, ...) and xxhash64(a, b, ...) as Spark computes them (spark_hash.py)."""
    def build(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression:
        if not args:
            raise ValueError(f"{name}() needs at least one argument, as in Spark.")
        return _with_args(_hash_template(name, len(args)), [_spark_literal(a) for a in args])
    return build


_ANON: dict[str, Callable[..., exp.Expression | None]] = {
    **_AGGREGATES,
    "ARRAY_REPEAT": _template("CASE WHEN fj_arg1 IS NULL THEN NULL ELSE LIST_TRANSFORM(RANGE(GREATEST(fj_arg1, 0)), "
                              "kf_i -> fj_arg0) END", 2),
    "ASSERT_TRUE": _template("CASE WHEN fj_arg0 THEN NULL ELSE ERROR('''' || CAST(fj_arg0 AS VARCHAR) "
                             "|| ''' is not true!') END", 1),
    "BTRIM": _template("TRIM(fj_arg0)", 1),
    "CARDINALITY": lambda n, a: None if len(a) != 1 else _with_args(
        "CASE WHEN fj_arg0 IS NULL THEN -1 ELSE " + ("CARDINALITY" if isinstance(a[0], (exp.Map, exp.VarMap)) else "LEN")
        + "(fj_arg0) END", a),
    "CRC32": _template(_CRC32, 1),
    "CSC": _template("1 / SIN(CAST(fj_arg0 AS DOUBLE))", 1),
    "E": _template("EXP(1.0)", 0),
    "EXPM1": _template(_EXPM1, 1),
    "FIND_IN_SET": _template(_FIND_IN_SET, 2),
    "GET": _template("CASE WHEN fj_arg1 < 0 THEN NULL ELSE fj_arg0[fj_arg1 + 1] END", 2),
    "HASH": _hash("hash"),
    "HYPOT": _template("SQRT(POW(CAST(fj_arg0 AS DOUBLE), 2) + POW(CAST(fj_arg1 AS DOUBLE), 2))", 2),
    "INLINE_OUTER": _template(_INLINE_OUTER, 1),
    "ISNOTNULL": _template("(fj_arg0 IS NOT NULL)", 1),
    "ISNULL": _template("(fj_arg0 IS NULL)", 1),
    # regexp(str, regex) is rlike and regexp_like under another name: a match anywhere in str
    "REGEXP": lambda n, a: exp.RegexpLike(this=a[0].copy(), expression=a[1].copy()) if len(a) == 2 else None,
    "JSON_TUPLE": _json_tuple,
    "LOG1P": _template(_LOG1P, 1),
    "LUHN_CHECK": _template(_LUHN, 1),
    "MAKE_DT_INTERVAL": _make_dt_interval,
    "MAKE_TIMESTAMP_LTZ": _make_timestamp,
    "MAKE_TIMESTAMP_NTZ": _make_timestamp,
    "MAP_ZIP_WITH": _map_zip_with,
    "MASK": _mask,
    "OCTET_LENGTH": _octet_length,
    "POSITIVE": _template("(+fj_arg0)", 1),
    "SHIFTRIGHTUNSIGNED": _template("CASE WHEN TYPEOF(fj_arg0) = 'BIGINT' THEN CAST(CASE WHEN fj_arg1 % 64 = 0 "
                                    "THEN fj_arg0::HUGEINT ELSE (fj_arg0::HUGEINT & 18446744073709551615) >> (fj_arg1 % 64) END AS BIGINT) "
                                    "ELSE CAST(CASE WHEN fj_arg1 % 32 = 0 THEN fj_arg0::BIGINT "
                                    "ELSE (fj_arg0::BIGINT & 4294967295) >> (fj_arg1 % 32) END AS INTEGER) END", 2),
    "TIMESTAMP_MICROS": _timestamp_from(1),
    "TIMESTAMP_MILLIS": _timestamp_from(1000),
    "TIMESTAMP_SECONDS": _timestamp_from(1000000),
    "TO_CSV": _to_csv,
    "FROM_CSV": _from_csv,
    "TO_TIMESTAMP_LTZ": _to_timestamp(False),
    "TO_TIMESTAMP_NTZ": _to_timestamp(False),
    "TRY_TO_TIMESTAMP": _to_timestamp(True),
    "TRY_TO_BINARY": _try_to_binary,
    "WEEKDAY": _template(f"(ISODOW({_DATE}) - 1)", 1),
    # Refused by name: these can't give Spark's value here.
    "AES_DECRYPT": _refuse("aes_decrypt", "AES isn't available in the engine on this PC."),
    "TRY_AES_DECRYPT": _refuse("try_aes_decrypt", "AES isn't available in the engine on this PC."),
    "COUNT_MIN_SKETCH": _refuse("count_min_sketch", _SKETCH),
    "HISTOGRAM_NUMERIC": _refuse("histogram_numeric", "Spark's bins come from its own streaming algorithm, "
                                 "which this PC doesn't have.", "use width_bucket for fixed-width bins"),
    "HLL_SKETCH_AGG": _refuse("hll_sketch_agg", _SKETCH, "use approx_count_distinct"),
    "HLL_SKETCH_ESTIMATE": _refuse("hll_sketch_estimate", _SKETCH, "use approx_count_distinct"),
    "HLL_UNION": _refuse("hll_union", _SKETCH, "use approx_count_distinct"),
    "HLL_UNION_AGG": _refuse("hll_union_agg", _SKETCH, "use approx_count_distinct"),
    "MAKE_YM_INTERVAL": _refuse("make_ym_interval", "this PC has no year-month interval type."),
    "SCHEMA_OF_CSV": _refuse("schema_of_csv", "this PC doesn't infer a CSV schema from one line the way Spark does.",
                             "write the schema out for from_csv"),
    "SENTENCES": _refuse("sentences", "it splits text by Java's locale rules, which this PC doesn't have."),
    **{name: _refuse(name.lower(), _XPATH) for name in ("XPATH", "XPATH_BOOLEAN", "XPATH_DOUBLE", "XPATH_FLOAT",
                                                       "XPATH_INT", "XPATH_LONG", "XPATH_NUMBER", "XPATH_SHORT",
                                                       "XPATH_STRING")},
    "WINDOW": _time_window,
    "WINDOW_TIME": _template("CAST(STRUCT_EXTRACT(fj_arg0, 'end') - INTERVAL 1 MICROSECOND AS TIMESTAMP)", 1),
    "SESSION_WINDOW": _refuse("session_window", "session windows run in Fabric only."),
    "PRINTF": _format_string,
    "XXHASH64": _hash("xxhash64"),
}


def _typed() -> list[tuple[type, Callable[..., exp.Expression | None]]]:
    """Rewrites of nodes sqlglot reads into its own classes, looked up by name so a sqlglot without
    one of them still loads."""
    table: list[tuple[str, Callable[..., exp.Expression | None]]] = [
        ("ApproxQuantile", lambda n, a: exp.Anonymous(this="QUANTILE_DISC", expressions=[n.this.copy(), n.args["quantile"].copy()])),
        ("ArrayRemove", lambda n, a: _with_args("CASE WHEN fj_arg1 IS NULL THEN NULL ELSE LIST_FILTER(fj_arg0, "
                                                "kf_x -> kf_x IS NULL OR kf_x <> fj_arg1) END", [n.this, n.expression])),
        ("Getbit", lambda n, a: _with_args("CAST((fj_arg0 >> fj_arg1) & 1 AS TINYINT)", [n.this, n.expression])),
        ("Csc", _template("1 / SIN(CAST(fj_arg0 AS DOUBLE))", 1)),
        ("Sec", _template("1 / COS(CAST(fj_arg0 AS DOUBLE))", 1)),
        ("DateFromUnixDate", _template("CAST(DATE '1970-01-01' + CAST(fj_arg0 AS INTEGER) AS DATE)", 1)),
        ("Elt", _elt),
        ("TsOrDsToDate", _to_date_with_format),
        ("ArraysZip", _arrays_zip),
        ("BitmapConstructAgg", _template(_BITMAP_CONSTRUCT_AGG, 1)),
        ("BitmapCount", _template(_BITMAP_COUNT, 1)),
        ("BitmapOrAgg", _template(_BITMAP_OR_AGG, 1)),
        ("FirstValue", lambda n, a: None if n.find_ancestor(exp.Window) else _with_args("FIRST(fj_arg0)", [n.this])),
        ("LastValue", lambda n, a: None if n.find_ancestor(exp.Window) else _with_args("LAST(fj_arg0)", [n.this])),
        ("Format", _format_string),
        ("AtTimeZone", lambda n, a: _with_args("TIMEZONE(fj_arg1, TIMEZONE('UTC', TRY_CAST(fj_arg0 AS TIMESTAMP)))",
                                               [_uncast(n.this), n.args["zone"]])),
        ("FromTimeZone", lambda n, a: _with_args("TIMEZONE('UTC', TIMEZONE(fj_arg1, TRY_CAST(fj_arg0 AS TIMESTAMP)))",
                                                 [_uncast(n.this), n.args["zone"]])),
        ("ToNumber", lambda n, a: _to_number(False)(n, [n.this, n.args["format"]] if n.args.get("format") else [n.this])),
        ("ToChar", lambda n, a: _to_char("to_char")(n, [n.this, n.args["format"]]) if n.args.get("format") else None),
        ("ParseUrl", _parse_url),
        ("Extract", _extract),
        ("Hour", _template(f"HOUR({_TS})", 1)),
        ("Minute", _template(f"MINUTE({_TS})", 1)),
        ("Second", _template(f"SECOND({_TS})", 1)),
        ("Quarter", _template(f"QUARTER({_DATE})", 1)),
        ("Inline", _template(_INLINE, 1)),
        ("Kurtosis", _template(_KURTOSIS, 1)),
        ("Skewness", _template(_SKEWNESS, 1)),
        ("Nanvl", _template("CASE WHEN ISNAN(CAST(fj_arg0 AS DOUBLE)) THEN CAST(fj_arg1 AS DOUBLE) "
                            "ELSE CAST(fj_arg0 AS DOUBLE) END", 2)),
        ("Negative", _template("(-fj_arg0)", 1)),
        ("Overlay", lambda n, a: _with_args(
            "LEFT(fj_arg0, fj_arg2 - 1) || fj_arg1 || SUBSTR(fj_arg0, fj_arg2 + "
            + ("CASE WHEN fj_arg3 < 0 THEN LENGTH(fj_arg1) ELSE fj_arg3 END" if n.args.get("for_") else "LENGTH(fj_arg1)") + ")",
            [n.this, n.expression, n.args["from_"], *([n.args["for_"]] if n.args.get("for_") else [])])),
        ("PosexplodeOuter", _template(_POSEXPLODE_OUTER, 1)),
        ("ExplodeOuter", _template(_EXPLODE_OUTER, 1)),
        ("RegexpSubstr", lambda n, a: None if len(a) != 2 else _with_args(
            "CASE WHEN REGEXP_MATCHES(fj_arg0, fj_arg1) THEN REGEXP_EXTRACT(fj_arg0, fj_arg1) END", a)),
        ("Rint", _template("ROUND_EVEN(CAST(fj_arg0 AS DOUBLE), 0)", 1)),
        ("Sign", _sign),
        ("Stack", _stack),
        ("SubstringIndex", lambda n, a: _with_args(_SUBSTRING_INDEX, [n.this, n.args["delimiter"], n.args["count"]])),
        ("SafeAdd", _template("TRY(fj_arg0 + fj_arg1)", 2)),
        ("SafeSubtract", _template("TRY(fj_arg0 - fj_arg1)", 2)),
        ("SafeMultiply", _template("TRY(fj_arg0 * fj_arg1)", 2)),
        ("Typeof", _typeof),
        ("WidthBucket", lambda n, a: _with_args(_WIDTH_BUCKET, [n.this, n.args["min_value"], n.args["max_value"],
                                                                n.args["num_buckets"]])),
    ]
    return [(cls, build) for name, build in table if (cls := getattr(exp, name, None)) is not None]


def _to_date_with_format(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """to_date(text, format) reads the text with the format, also when the format is sqlglot's
    default ('yyyy-MM-dd HH:mm:ss', 'yyyy-MM-dd'), which sqlglot otherwise writes as a plain cast
    that ignores it: Spark gives NULL for '2026-03-02' with 'yyyy-MM-dd HH:mm:ss'."""
    fmt = node.args.get("format")
    if fmt is None or not fmt.is_string:
        return None
    parsed = exp.StrToTime(this=node.this.copy(), format=fmt.copy(), safe=True)
    return exp.cast(exp.cast(parsed, exp.DataType.Type.TIMESTAMP), exp.DataType.Type.DATE)


def _format_text(node: exp.Expression) -> str | None:
    """A number format written as text: one literal, or literals Spark joins ('9' '9', also written '9''9'),
    in upper case as Spark reads it ('99d9' is '99D9')."""
    from .spark_number_format import spark_format
    if isinstance(node, exp.Literal) and node.is_string:
        return spark_format(node.name)
    if isinstance(node, exp.Concat) and all(isinstance(e, exp.Literal) and e.is_string for e in node.expressions):
        return spark_format("".join(e.name for e in node.expressions))
    return None


def _to_number(try_: bool) -> Callable[..., exp.Expression | None]:
    """to_number(text, format) and try_to_number: Spark's number format patterns (spark_number_format).
    A format that isn't a constant text is refused, as Spark refuses it."""
    name = "try_to_number" if try_ else "to_number"

    def build(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
        from . import spark_number_format
        if len(args) != 2:
            return None
        fmt = _format_text(args[1])
        if fmt is None:
            raise ValueError(f"{name}()'s format must be written out as text ('999.99'), as Spark requires.")
        shown = fmt.replace("'", "''''")   # inside the SQL text of ERROR's argument
        fail = "NULL" if try_ else (f"ERROR('{name}(): the input ''' || kf_s || ''' doesn''t match the format "
                                    f"''{shown}'', as in Fabric.')")
        text, _, _ = spark_number_format.parse_sql(fmt, fail)
        return _with_args(text, [args[0]])
    return build


def _to_char(name: str) -> Callable[..., exp.Expression | None]:
    """to_char(number, format) and to_varchar: a number written with Spark's number format patterns."""
    def build(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
        from . import spark_number_format
        if len(args) != 2:
            return None
        fmt = _format_text(args[1])
        if fmt is None:
            raise ValueError(f"{name}()'s format must be written out as text ('999.99'), as Spark requires.")
        return _with_args(spark_number_format.format_sql(fmt), [args[0]])
    return build


# parse_url as Spark 3.5 answers it: Spark reads the text with java.net.URI (RFC 2396) and gives NULL
# when URI refuses it. These are URI's character sets: "other" is any character above US-ASCII
# that isn't a control or a space, which URI allows outside the scheme, host and port.
_URL_OTHER = r"[^\x00-\x{9F}\p{Z}]"
_URL_ESC = "%[0-9A-Fa-f]{2}"
_URL_UNRESERVED = r"A-Za-z0-9\-_.!~*'()"
_URL_PATH = f"^(?:[{_URL_UNRESERVED}:@&=+$,;/]|{_URL_ESC}|{_URL_OTHER})*$"
_URL_URIC = f"^(?:[{_URL_UNRESERVED};/?:@&=+$,\\[\\]]|{_URL_ESC}|{_URL_OTHER})*$"
_URL_REGISTRY = f"^(?:[{_URL_UNRESERVED}$,;:@&=+]|{_URL_ESC}|{_URL_OTHER})+$"
_URL_USERINFO = f"(?:[{_URL_UNRESERVED};:&=+$,]|{_URL_ESC}|{_URL_OTHER})*"
_URL_HOSTNAME = r"(?:[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?\.)*[A-Za-z](?:[A-Za-z0-9\-]*[A-Za-z0-9])?\.?"
_URL_BYTE = "(?:25[0-5]|2[0-4][0-9]|[01]?[0-9]?[0-9])"
_URL_IPV4 = f"(?:{_URL_BYTE}\\.){{3}}{_URL_BYTE}"
_URL_SERVER = f"^(?:({_URL_USERINFO})@)?({_URL_HOSTNAME}|{_URL_IPV4})(?::[0-9]*)?$"
_URL_SCHEME = r"[A-Za-z][A-Za-z0-9+.\-]*"
_URL_PARTS = f"^(?:({_URL_SCHEME}):)?(?://([^/?#]*))?([^?#]*)(?:\\?([^#]*))?(?:#(.*))?$"


def _q(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _parse_url(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """parse_url(url, part[, key]). A URL whose host is in brackets (IPv6) is refused by name, and so
    is a key Spark would read as a pattern: there the answer here could differ."""
    part, key = node.args.get("part_to_extract"), node.args.get("key")
    if part is None:
        return None
    refuse = ("ERROR('parse_url() can''t run on this PC the way it runs in Fabric, so nothing ran: {}. "
              "Run the cell in Fabric.')")
    keyed = key is not None
    # kf_a: the text; kf_b: its pieces as URI splits them; kf_c: whether URI reads it, and the server.
    pieces = (f"{{'m': REGEXP_EXTRACT(kf_a.u, {_q(_URL_PARTS)}, ['s', 'a', 'p', 'q', 'f']), "
              f"'hs': REGEXP_MATCHES(kf_a.u, {_q('^' + _URL_SCHEME + ':')}), "
              f"'ha': REGEXP_MATCHES(kf_a.u, {_q('^(?:' + _URL_SCHEME + ':)?//')}), "
              "'hq': REGEXP_MATCHES(kf_a.u, '^[^#]*\\?'), 'hf': STRPOS(kf_a.u, '#') > 0}")
    opaque = "(kf_b.hs AND NOT STARTS_WITH(kf_b.m.a || kf_b.m.p, '/') AND NOT kf_b.ha)"
    ssp = f"REGEXP_EXTRACT(kf_a.u, {_q('^' + _URL_SCHEME + ':([^#]*)')}, 1)"
    valid = (f"CASE WHEN {opaque} THEN {ssp} <> '' AND REGEXP_MATCHES({ssp}, {_q(_URL_URIC)}) "
             f"AND (NOT kf_b.hf OR REGEXP_MATCHES(kf_b.m.f, {_q(_URL_URIC)})) "
             f"ELSE (kf_b.hs OR kf_b.ha OR NOT REGEXP_MATCHES(kf_b.m.p, '^[^/]*:')) "
             f"AND REGEXP_MATCHES(kf_b.m.p, {_q(_URL_PATH)}) "
             f"AND (NOT kf_b.hq OR REGEXP_MATCHES(kf_b.m.q, {_q(_URL_URIC)})) "
             f"AND (NOT kf_b.hf OR REGEXP_MATCHES(kf_b.m.f, {_q(_URL_URIC)})) "
             f"AND (NOT kf_b.ha OR (kf_b.m.a = '' AND (kf_b.m.p <> '' OR kf_b.hq OR kf_b.hf)) "
             f"OR REGEXP_MATCHES(kf_b.m.a, {_q(_URL_SERVER)}) OR REGEXP_MATCHES(kf_b.m.a, {_q(_URL_REGISTRY)})) END")
    server = (f"CASE WHEN kf_b.ha AND REGEXP_MATCHES(kf_b.m.a, {_q(_URL_SERVER)}) "
              f"THEN REGEXP_EXTRACT(kf_b.m.a, {_q(_URL_SERVER)}, ['ui', 'h']) END")
    at = "STRPOS(kf_b.m.a, '@') > 0"
    query = "CASE WHEN kf_b.hq THEN kf_b.m.q END"
    if keyed:
        query = (f"CASE WHEN NOT REGEXP_MATCHES(kf_a.key, '^[A-Za-z0-9_\\-.]*$') THEN "
                 f"{refuse.format('Spark reads this key as a pattern')} "
                 f"WHEN kf_b.hq AND REGEXP_MATCHES(kf_b.m.q, '(?:&|^)' || kf_a.key || '=') "
                 f"THEN REGEXP_EXTRACT(kf_b.m.q, '(?:&|^)' || kf_a.key || '=([^&]*)', 1) END")
    answer = (f"CASE WHEN STRPOS(kf_b.m.a, '[') > 0 AND kf_b.ha THEN {refuse.format('its host is in brackets (IPv6)')} "
              f"WHEN NOT kf_c.ok THEN NULL "
              f"WHEN kf_a.k = 'QUERY' THEN {query} "
              + ("ELSE NULL END" if keyed else
                 f"WHEN kf_a.k = 'PROTOCOL' THEN CASE WHEN kf_b.hs THEN kf_b.m.s END "
                 f"WHEN kf_a.k = 'REF' THEN CASE WHEN kf_b.hf THEN kf_b.m.f END "
                 f"WHEN {opaque} THEN NULL "
                 f"WHEN kf_a.k = 'HOST' THEN kf_c.sv.h "
                 f"WHEN kf_a.k = 'PATH' THEN kf_b.m.p "
                 f"WHEN kf_a.k = 'FILE' THEN kf_b.m.p || CASE WHEN kf_b.hq THEN '?' || kf_b.m.q ELSE '' END "
                 f"WHEN kf_a.k = 'AUTHORITY' THEN CASE WHEN kf_b.ha AND kf_b.m.a <> '' THEN kf_b.m.a END "
                 f"WHEN kf_a.k = 'USERINFO' THEN CASE WHEN {at} THEN kf_c.sv.ui END "
                 "ELSE NULL END"))
    if keyed:
        answer = answer.replace(f"WHEN kf_a.k = 'QUERY' THEN {query} ", f"WHEN kf_a.k = 'QUERY' AND NOT {opaque} THEN {query} ")
    else:
        answer = answer.replace(f"WHEN kf_a.k = 'QUERY' THEN {query} ", f"WHEN kf_a.k = 'QUERY' AND NOT {opaque} THEN {query} ")
    inputs = "{'u': CAST(fj_arg0 AS VARCHAR), 'k': CAST(fj_arg1 AS VARCHAR)" + (", 'key': CAST(fj_arg2 AS VARCHAR)}" if keyed else "}")
    nulls = "fj_arg0 IS NULL OR fj_arg1 IS NULL" + (" OR fj_arg2 IS NULL" if keyed else "")
    template = (f"CASE WHEN {nulls} THEN NULL ELSE LIST_TRANSFORM([{inputs}], kf_a -> "
                f"LIST_TRANSFORM([{pieces}], kf_b -> LIST_TRANSFORM([{{'ok': {valid}, 'sv': {server}}}], "
                f"kf_c -> {answer})[1])[1])[1] END")
    return _with_args(template, [node.this, part] + ([key] if keyed else []))


# Spark's EXTRACT / date_part fields whose value DuckDB's EXTRACT gives differently: the day of the
# week counts from Sunday = 1 (DuckDB's from 0), and seconds keep their fraction as decimal(8,6).
_SPARK_DOW = {"DOW", "DAYOFWEEK"}
_SPARK_DOW_ISO = {"DOW_ISO", "DAYOFWEEK_ISO"}
_SPARK_SECOND = {"S", "SEC", "SECS", "SECOND", "SECONDS"}


def _extract(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """EXTRACT(field FROM value) as Spark answers it. Spark's TIMESTAMP is read as TIMESTAMPTZ, which
    sqlglot won't extract from; the session's zone here is UTC, as in Fabric (spark_api), so the same
    instant as a plain UTC timestamp gives the same field."""
    value, part = node.expression, node.this.name.upper()
    if not isinstance(value, exp.Expression):
        return None
    ts = "TRY_CAST(fj_arg0 AS TIMESTAMP)"
    if part in _SPARK_DOW:
        return _with_args(f"CAST(DAYOFWEEK({ts}) + 1 AS INTEGER)", [value])
    if part in _SPARK_DOW_ISO:
        return _with_args(f"CAST(ISODOW({ts}) AS INTEGER)", [value])
    if part in _SPARK_SECOND:
        return _with_args(f"CAST(CAST(MICROSECOND({ts}) AS DECIMAL(14, 6)) / 1000000 AS DECIMAL(8, 6))", [value])
    out = node.copy()
    if (isinstance(value, exp.Literal) and value.is_string) or value.is_type(*exp.DataType.TEXT_TYPES):
        out.set("expression", _with_args(ts, [value]))  # text is read as a timestamp, as Spark casts it
    elif value.is_type(exp.DataType.Type.TIMESTAMPTZ, exp.DataType.Type.TIMESTAMPLTZ):
        if isinstance(value, (exp.Cast, exp.TryCast)):
            plain = value.copy()
            plain.set("to", exp.DataType.build("TIMESTAMP"))
            plain.type = exp.DataType.build("TIMESTAMP")
        else:
            plain = exp.cast(value.copy(), exp.DataType.Type.TIMESTAMP)
        out.set("expression", plain)
    return exp.cast(out, exp.DataType.Type.INT)  # Spark's fields are int; DuckDB's are bigint


def _date_part(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """date_part('field', value) and datepart: Spark's EXTRACT with the field as text."""
    if len(args) != 2 or not (isinstance(args[0], exp.Literal) and args[0].is_string):
        return None
    return _extract(exp.Extract(this=exp.var(args[0].name.upper()), expression=args[1].copy()), [])


def _uncast(node: exp.Expression) -> exp.Expression:
    """from_utc_timestamp's argument without the cast sqlglot puts round it (it is cast here)."""
    return node.this if isinstance(node, (exp.Cast, exp.TryCast)) else node


_TYPED = _typed()
_ANON["TRY_TO_NUMBER"] = _to_number(True)
_ANON["TO_VARCHAR"] = _to_char("to_varchar")
_ANON["DATE_PART"] = _ANON["DATEPART"] = _date_part
_RANKS = tuple(c for c in (getattr(exp, n, None) for n in ("Rank", "DenseRank", "PercentRank")) if c is not None)


def _grouping_id(node: exp.Expression) -> exp.Expression | None:
    """grouping_id() with no arguments: over every GROUP BY column, as Spark's (the first column is
    the highest bit, as in DuckDB's grouping_id)."""
    if node.expressions:
        return None
    select = node.find_ancestor(exp.Select)
    group = select.args.get("group") if select is not None else None
    if group is None:
        return None
    columns: list[exp.Expression] = []
    for kind in ("expressions", "cube", "rollup", "grouping_sets"):
        for item in group.args.get(kind) or []:
            inner = item.expressions if isinstance(item, (exp.Cube, exp.Rollup, exp.GroupingSets)) else [item]
            for e in inner:
                for c in (e.expressions if isinstance(e, exp.Tuple) else [e]):
                    if not any(c == seen for seen in columns):
                        columns.append(c)
    if not columns:
        return None
    return exp.Anonymous(this="GROUPING_ID", expressions=[c.copy() for c in columns])


def _any(node: exp.Expression) -> exp.Expression | None:
    """any(col) as an aggregate (not ``x = ANY (subquery)``)."""
    if isinstance(node.parent, (exp.Binary, exp.Predicate)) and not isinstance(node.parent, (exp.And, exp.Or)):
        return None
    inner = node.this.this if isinstance(node.this, exp.Paren) else node.this
    if isinstance(inner, (exp.Subquery, exp.Select, exp.Query)):
        return None
    return _with_args("BOOL_OR(fj_arg0)", [inner])


_GENERATORS = {"INLINE_OUTER", "JSON_TUPLE"}


def names() -> set[str]:
    """The function names rewritten here that keep Spark's name for a selected column (a selected
    window() is named window, as in Spark: _window_columns names it)."""
    return set(_ANON) - _GENERATORS - {"WINDOW"}


def typed_classes() -> tuple[type, ...]:
    generators = {getattr(exp, n, None) for n in ("Inline", "PosexplodeOuter", "ExplodeOuter", "Stack")}
    return tuple(cls for cls, _ in _TYPED if cls not in generators)


_MARK = "kf_spark_builtin"


def _candidates(statement: exp.Expression) -> list[exp.Expression]:
    return list(statement.find_all(exp.Anonymous, exp.Any, exp.GroupingId, exp.TimestampTrunc, exp.DateTrunc,
                                   *_RANKS, *(c for c, _ in _TYPED)))


def _raw_unix_formats(statement: exp.Expression) -> None:
    """sqlglot reads to_unix_timestamp's format as written ('yyyy-MM-dd'), where unix_timestamp's
    is read into %-codes; give it the same reading, so both take the same path."""
    from sqlglot.dialects.spark import Spark
    for node in statement.find_all(exp.StrToUnix):
        fmt = node.args.get("format")
        if isinstance(fmt, exp.Literal) and fmt.is_string and "%" not in fmt.name:
            read = Spark.format_time(fmt)
            if read is not None:
                node.set("format", read)


def mark(statement: exp.Expression) -> exp.Expression:
    """Mark the calls the person wrote, before sql.spark_fixes' other rewrites run. Only marked nodes
    are rewritten by ``rewrite``, so DuckDB SQL another rewrite wrote (its FORMAT, its QUANTILE) is
    never read as a Spark call."""
    _named_tuples(statement)
    _raw_unix_formats(statement)
    for node in _candidates(statement):
        if isinstance(node, exp.Anonymous) and node.name.upper() not in _ANON:
            continue
        for ident in node.find_all(exp.Identifier):
            if ident.name.lower().startswith("kf_"):  # the rewrites' own lambda names (as sql._no_reserved_names)
                raise ValueError(f"The name {ident.name} can't be used inside this function on this PC: names "
                                 "that start with kf_ are reserved here. Rename it (AS ... or another lambda "
                                 "argument name) and run it again.")
        node.meta[_MARK] = True
    return statement


def rewrite(statement: exp.Expression) -> exp.Expression:
    """Every marked call above in ``statement``, deepest first, as DuckDB SQL that gives Spark's value."""
    _window_columns(statement)
    found = [n for n in _candidates(statement) if n.meta.get(_MARK)]
    for node in reversed(found):
        replacement: exp.Expression | None = None
        if isinstance(node, exp.Anonymous):
            build = _ANON.get(node.name.upper())
            if build is not None:
                replacement = build(node, list(node.expressions))
        elif isinstance(node, exp.Any):
            replacement = _any(node)
        elif isinstance(node, exp.GroupingId):
            replacement = _grouping_id(node)
        elif isinstance(node, (exp.TimestampTrunc, exp.DateTrunc)):
            _truncated_time(node)
            continue
        elif isinstance(node, _RANKS):
            if node.expressions:  # Spark ignores rank(b)'s argument: the order is the window's ORDER BY
                node.set("expressions", [])
            continue
        else:
            for cls, build in _TYPED:
                if isinstance(node, cls):
                    replacement = build(node, _args(node))
                    break
        if replacement is not None:
            if node is statement:
                return replacement
            if isinstance(node, exp.ExplodeOuter) and isinstance(node.parent, exp.Select):
                replacement = cast(exp.Expression, exp.alias_(replacement, "col"))  # Spark names explode_outer's column col
            node.replace(replacement)
    _no_duckdb_hash(statement)
    return statement


def _no_duckdb_hash(statement: exp.Expression) -> None:
    """A hash or xxhash64 still here lost its mark on the way (another rewrite wrote it out as text):
    DuckDB would answer with its own hash, another number, so the cell stops instead."""
    for call in statement.find_all(exp.Anonymous):
        if call.name.upper() in ("HASH", "XXHASH64"):
            name = call.name.lower()
            raise ValueError(f"{name}() can't run on this PC the way it runs in Fabric here, so nothing ran: "
                             f"written this way it would give another number than Spark's {name}. Run the cell "
                             "in Fabric, or compute the hash in a column of its own first.")


def _truncated_time(node: exp.Expression) -> None:
    """trunc(x, unit) reads x as a date and date_trunc(unit, 'text') reads text as a timestamp, as
    Spark does (text that isn't one gives NULL). date_trunc of anything else is left as it is, so a
    timestamp keeps its type."""
    value = node.this
    if isinstance(node, exp.TimestampTrunc):
        text = value.is_string or (isinstance(value, (exp.Cast, exp.TryCast)) and value.to.is_type(*exp.DataType.TEXT_TYPES))
        if not text:
            return
        target = exp.DataType.Type.TIMESTAMP
    else:
        target = exp.DataType.Type.DATE
    if isinstance(value, (exp.Cast, exp.TryCast)) and value.to.this == target:
        return
    node.set("this", exp.TryCast(this=value.copy(), to=exp.DataType.build(target)))
