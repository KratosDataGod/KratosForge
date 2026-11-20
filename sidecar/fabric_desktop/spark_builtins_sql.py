"""Spark SQL built-in functions that DuckDB lacks or answers differently, written as DuckDB SQL that
gives Spark's value (the Fails and Differs rows of docs/spark-functions.md).

``rewrite(statement)`` is the last step of sql.spark_fixes, so the Spark SQL a notebook runs on
this PC and the DuckDB engine's Databricks SQL both get it. It runs after every other rewrite there,
so nothing it writes is rewritten again; each argument is the person's own node, already rewritten.

Functions that can't give Spark's value here (sketches, XPath, AES, session windows) are refused by
name with a message, so a cell stops before anything runs with another answer.
"""

from __future__ import annotations

import contextvars
import functools
import re
from typing import Callable, cast

import sqlglot
from sqlglot import exp

from . import spark_time_zone
from .spark_hash import hash_sql

# ---- helpers


# An argument a template reads more than once is put in once, as a lambda's parameter, when it is this big
# (in nodes): copied into each place, calls nested in each other (upper(lower(...)), substring_index of
# substring_index) would grow exponentially, to megabytes of SQL that take seconds to plan.
_BIND_ABOVE = 40
# A lambda's body can't hold these, so an argument or template with one is copied as before.
_NOT_IN_LAMBDA = (exp.AggFunc, exp.Window, exp.Subquery, exp.Select)
# Nor is one that can raise on a row's values put in once: bound, it is worked out on every row, where
# the template may have read it only on some (a raise_error a CASE would have skipped would stop the
# query). raise_error and assert_true come here as ERROR(<the row's message>); an ERROR with a fixed
# message is a refusal by type (spark_text_arg's), the same on every row, and can be bound.
_RAISES = {"RAISE_ERROR", "ASSERT_TRUE"}


def _raises(node: exp.Expression) -> bool:
    return any(f.name.upper() in _RAISES or (f.name.upper() == "ERROR" and not (
        f.expressions and isinstance(f.expressions[0], exp.Literal) and f.expressions[0].is_string))
        for f in node.find_all(exp.Anonymous))


def _with_args(template: str | exp.Expression, args: list[exp.Expression]) -> exp.Expression:
    """``template`` read as DuckDB SQL (or already read: a copy is used), with the arguments in place
    of fj_arg0, fj_arg1, ... (as sql._with_args). Lambda names in templates start with kf_, which a
    query can't use. A big argument read more than once is read once (_BIND_ABOVE)."""
    tree = (template.copy() if isinstance(template, exp.Expression)
            else cast(exp.Expression, sqlglot.parse_one(template, read="duckdb")))
    places: dict[int, list[exp.Column]] = {}
    for column in list(tree.find_all(exp.Column)):
        if column.name.startswith("fj_arg") and not column.table:
            places.setdefault(int(column.name[6:]), []).append(column)
    bound = [i for i, cols in places.items()
             if len(cols) > 1 and sum(1 for _ in args[i].walk()) > _BIND_ABOVE and not _raises(args[i])]
    # An aggregate, window or subquery may be in a bound argument, which goes in the list outside the
    # lambda, but not in the lambda's body: so not in the template or an argument left in place, and in
    # one bound argument at most, which is put in outermost.
    held = [i for i in places if args[i].find(*_NOT_IN_LAMBDA)]
    if tree.find(*_NOT_IN_LAMBDA) or len(held) > 1 or any(i not in bound for i in held):
        bound = []
    bound.sort(key=lambda i: i not in held)  # the first is put in outermost
    for i, cols in places.items():
        for column in cols:
            column.replace(exp.column(f"kf_bound{i}") if i in bound else args[i].copy())
    for i in reversed(bound):
        wrap = cast(exp.Expression, sqlglot.parse_one(f"(LIST_TRANSFORM([fj_value], kf_bound{i} -> fj_body))[1]",
                                                      read="duckdb"))
        for column in list(wrap.find_all(exp.Column)):
            if column.name == "fj_value":
                column.replace(args[i].copy())
            elif column.name == "fj_body":
                column.replace(tree)
        tree = wrap
    return tree


_JAVA = "it calls a Java method by reflection, and this PC runs no Java."
_INPUT_FILE = "this PC doesn't track the file each row was read from."


def _refuse(name: str, why: str, instead: str = "") -> Callable[..., exp.Expression]:
    def refuse(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression:
        raise ValueError(f"{name}() can't run on this PC the way it runs in Fabric, so nothing ran: {why} "
                         f"Run the cell in Fabric{', or ' + instead if instead else ''}.")
    return refuse


def _sql_keywords(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression:
    raise ValueError("sql_keywords() can't run on this PC the way it runs in Fabric, because this PC doesn't have "
                     "Spark's list of SQL keywords and which of them are reserved, so nothing ran. Run the cell in "
                     "Fabric.")


# base64 takes bytes, and text as its UTF-8 bytes (a NULL of no type is NULL); any other type is refused, as in
# Spark. Spark 3.5 writes it as MIME does (spark.sql.chunkBase64String.enabled): a line break (CR LF) after
# every 76 characters. Databricks SQL keeps one line.
_BASE64_BYTES = ("TO_BASE64(CASE WHEN TYPEOF(fj_arg0) = 'BLOB' THEN CAST(fj_arg0 AS BLOB) "
                 "WHEN TYPEOF(fj_arg0) = 'VARCHAR' THEN ENCODE(CAST(fj_arg0 AS VARCHAR)) "
                 "WHEN TYPEOF(fj_arg0) = '\"NULL\"' THEN CAST(NULL AS BLOB) "
                 "ELSE CAST(ERROR('base64() takes text or binary, as in Spark, so nothing ran. Cast the value to "
                 "string first.') AS BLOB) END)")
_BASE64_LINES = (f"(LIST_TRANSFORM([{_BASE64_BYTES}], kf_b -> ARRAY_TO_STRING(LIST_TRANSFORM(RANGE(0, LENGTH(kf_b), 76), "
                 "kf_i -> SUBSTR(kf_b, kf_i + 1, 76)), CHR(13) || CHR(10))))[1]")


def _base64(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    return _with_args(_BASE64_BYTES if _ANSI.get() else _BASE64_LINES, args) if len(args) == 1 else None


# unbase64 as Spark 3.5 reads text (Java's MIME decoder): characters outside the base64 alphabet are skipped
# and padding is optional, but padding that doesn't end a group of four ('YQ=', '=' alone), base64 after the
# padding, or one character left over at the end stops, as in Spark.
_UNBASE64_STOP = ("'unbase64() stops on this text, as Spark does, because its padding or its last group of characters "
                  "isn''t valid base64 (Input byte array has wrong 4-byte ending unit). Check the end of the text for "
                  "stray characters or misplaced ''=''.'")
_UNBASE64 = ("(LIST_TRANSFORM([CAST(fj_arg0 AS VARCHAR)], kf_t -> CASE WHEN kf_t IS NULL THEN NULL ELSE "
             "(LIST_TRANSFORM([{'s': REGEXP_REPLACE(SPLIT_PART(kf_t, '=', 1), '[^A-Za-z0-9+/]', '', 'g'), "
             "'pad': STRPOS(kf_t, '=') > 0, 'after': SUBSTR(kf_t, LENGTH(SPLIT_PART(kf_t, '=', 1)) + 2)}], kf_u -> "
             "CASE WHEN (kf_u.pad AND (LENGTH(kf_u.s) % 4 IN (0, 1) OR (LENGTH(kf_u.s) % 4 = 2 AND LEFT(kf_u.after, 1) <> '='))) "
             "OR (NOT kf_u.pad AND LENGTH(kf_u.s) % 4 = 1) OR REGEXP_MATCHES(kf_u.after, '[A-Za-z0-9+/]') "
             f"THEN CAST(ERROR({_UNBASE64_STOP}) AS BLOB) "
             "ELSE FROM_BASE64(kf_u.s || REPEAT('=', (4 - LENGTH(kf_u.s) % 4) % 4)) END))[1] END))[1]")
# hex takes a whole number (a double or decimal is cut toward zero first, as Spark casts it to a long), text
# (its UTF-8 bytes) or binary.
_HEX = ("CASE WHEN TYPEOF(fj_arg0) = 'BLOB' THEN HEX(TRY_CAST(fj_arg0 AS BLOB)) "
        "WHEN TYPEOF(fj_arg0) = 'VARCHAR' THEN HEX(TRY_CAST(fj_arg0 AS VARCHAR)) "
        "WHEN TYPEOF(fj_arg0) IN ('DOUBLE', 'FLOAT') OR TYPEOF(fj_arg0) LIKE 'DECIMAL%' "
        "THEN HEX(kf_spark_whole) ELSE HEX(TRY_CAST(fj_arg0 AS BIGINT)) END")
# unhex: an odd number of digits has a 0 put before them; anything that isn't all hex digits is NULL.
_UNHEX = ("(LIST_TRANSFORM([CAST(fj_arg0 AS VARCHAR)], kf_x -> CASE WHEN REGEXP_FULL_MATCH(kf_x, '[0-9A-Fa-f]*') "
          "THEN UNHEX(CASE WHEN LENGTH(kf_x) % 2 = 1 THEN '0' || kf_x ELSE kf_x END) END))[1]")


def _sha2(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """sha2(x, bits): 0 or 256 is SHA-256, 224, 384 and 512 their SHA-2 (spark_hash.register), and any other
    length NULL, as in Spark. Text is hashed as its UTF-8 bytes."""
    if len(args) != 2:
        return None
    data = "CASE WHEN TYPEOF(fj_arg0) = 'BLOB' THEN CAST(fj_arg0 AS BLOB) ELSE ENCODE(CAST(fj_arg0 AS VARCHAR)) END"
    bits = args[1]
    if isinstance(bits, exp.Literal) and not bits.is_string:
        n = int(bits.name) if bits.name.lstrip("-").isdigit() else None
        if n in (0, 256):
            return _with_args(f"SHA256({data})", args)
        if n in (224, 384, 512):
            return _with_args(f"__kf_sha2({data}, {n})", args)
        return _with_args("CAST(NULL AS VARCHAR)", args)
    return _with_args(f"(LIST_TRANSFORM([CAST(fj_arg1 AS INTEGER)], kf_n -> CASE WHEN kf_n IN (0, 256) THEN SHA256({data}) "
                      f"WHEN kf_n IN (224, 384, 512) THEN __kf_sha2({data}, kf_n) END))[1]", args)


def _template(text: str, count: int | tuple[int, ...]) -> Callable[..., exp.Expression | None]:
    counts = (count,) if isinstance(count, int) else count

    def build(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
        return _with_args(text, args) if len(args) in counts else None
    return build


def _row_ids(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """monotonically_increasing_id() as a row number. DuckDB numbers rows this way only in a SELECT list: in
    WHERE, HAVING, GROUP BY or a JOIN it stops with a window-function error, and in ORDER BY the numbers
    don't follow the rows' order, so there it is refused by name."""
    if args:
        return None
    clause = node.parent
    while clause is not None and not isinstance(clause, (exp.Select, exp.Where, exp.Having, exp.Join, exp.Group,
                                                                  exp.Order)):
        clause = clause.parent
    if clause is not None and not isinstance(clause, exp.Select):
        _refuse("monotonically_increasing_id", "this PC can't number the rows in WHERE, HAVING, GROUP BY, "
                "ORDER BY or a JOIN condition.", "select it in a subquery and filter on that column outside it")(node, args)
    numbered = _with_args("(ROW_NUMBER() OVER () - 1)", args)
    for window in numbered.find_all(exp.Window):  # this PC's own window: spark_windows leaves it be
        window.meta["kf_own_window"] = True
    return numbered


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
# Both forms read the text and count once, bound in one struct (kf_r), so a nested call isn't copied. The plain
# form's delimiter is a literal (ARRAY_TO_STRING needs a constant one), and the other form binds it too.
_SUBSTRING_INDEX = (
    "(LIST_TRANSFORM([{'s': fj_arg0, 'n': fj_arg2}], kf_r -> "
    "CASE WHEN kf_r.s IS NULL OR fj_arg1 IS NULL OR kf_r.n IS NULL THEN NULL "
    "WHEN kf_r.n = 0 OR fj_arg1 = '' THEN '' "
    "WHEN kf_r.n > 0 THEN ARRAY_TO_STRING(LIST_SLICE(STRING_SPLIT(kf_r.s, fj_arg1), 1, kf_r.n), fj_arg1) "
    "ELSE ARRAY_TO_STRING(LIST_SLICE(STRING_SPLIT(kf_r.s, fj_arg1), "
    "GREATEST(LEN(STRING_SPLIT(kf_r.s, fj_arg1)) + kf_r.n + 1, 1), LEN(STRING_SPLIT(kf_r.s, fj_arg1))), fj_arg1) END))[1]")

# A delimiter that can overlap itself ('aa' in 'aaa'): Spark looks for the next one from the character after
# the last one found, so each is counted, from the left for n > 0 and the right for n < 0.
_SUBSTRING_INDEX_OVERLAP = (
    "(LIST_TRANSFORM([{'s': fj_arg0, 'd': fj_arg1, 'n': fj_arg2}], kf_r -> "
    "CASE WHEN kf_r.s IS NULL OR kf_r.d IS NULL OR kf_r.n IS NULL THEN NULL "
    "WHEN kf_r.n = 0 OR kf_r.d = '' THEN '' ELSE (LIST_TRANSFORM([LIST_FILTER(RANGE(1, LENGTH(kf_r.s) - "
    "LENGTH(kf_r.d) + 2), kf_i -> SUBSTRING(kf_r.s, kf_i, LENGTH(kf_r.d)) = kf_r.d)], kf_p -> "
    "CASE WHEN LEN(kf_p) < ABS(kf_r.n) THEN kf_r.s WHEN kf_r.n > 0 THEN LEFT(kf_r.s, LIST_EXTRACT(kf_p, kf_r.n) - 1) "
    "ELSE SUBSTRING(kf_r.s, LIST_EXTRACT(kf_p, LEN(kf_p) + kf_r.n + 1) + LENGTH(kf_r.d)) END))[1] END))[1]")


def _substring_index(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression:
    delimiter = node.args["delimiter"]
    plain = isinstance(delimiter, exp.Literal) and delimiter.is_string and not any(
        delimiter.name[:k] == delimiter.name[-k:] for k in range(1, len(delimiter.name)))
    return _with_args(_SUBSTRING_INDEX if plain else _SUBSTRING_INDEX_OVERLAP,
                      [node.this, delimiter, node.args["count"]])


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

# ---- math (WR-116): Spark's answers are Java's Math and StrictMath ones, where DuckDB stops with an error or
# answers as C's libm does

_NAN = "CAST('NaN' AS DOUBLE)"
_VOLATILE = (exp.Rand, exp.Randn, exp.Uuid)


def _doubles(args: list[exp.Expression]) -> list[exp.Expression]:
    return [exp.cast(a.copy(), exp.DataType.build("DOUBLE")) for a in args]


def _doubles_once(template: str, args: list[exp.Expression]) -> exp.Expression:
    """_read_once over ``args`` cast to double, where a column or literal cast is still read as it is."""
    return _read_once(template, _doubles(args), [_cheap(a) for a in args])


# log(base, x), log10 and log2 (sqlglot reads them all as LOG(base, x)): NULL for x <= 0 or base <= 0, as Spark's
# Logarithm, where DuckDB stops ("cannot take logarithm of zero") or, from 2.0, gives -inf or NaN. Base 1 is
# ln(x) / 0, +-inf (or NaN for x = 1), which DuckDB stops on too.
_LOG = ("CASE WHEN fj_arg0 <= 0 OR fj_arg1 <= 0 THEN NULL WHEN fj_arg0 = 1 OR ISNAN(fj_arg0) OR ISNAN(fj_arg1) "
        "THEN LN(fj_arg1) / LN(fj_arg0) ELSE LOG(fj_arg0, fj_arg1) END")
# Java's Math.pow, where C's pow differs: NaN for a NaN exponent, and for 1 or -1 to an infinite power.
_POW = (f"CASE WHEN fj_arg0 IS NULL OR fj_arg1 IS NULL THEN NULL WHEN ISNAN(fj_arg1) OR (ISINF(fj_arg1) "
        f"AND ABS(fj_arg0) = 1) THEN {_NAN} ELSE POW(fj_arg0, fj_arg1) END")
# Java's Math.hypot: infinite when either side is, even beside a NaN; past 1e150 (or below 1e-150) scaled by the
# larger side, so the squares don't overflow to inf (or underflow to 0).
_HYPOT = ("CASE WHEN fj_arg0 IS NULL OR fj_arg1 IS NULL THEN NULL "
          "WHEN ISINF(fj_arg0) OR ISINF(fj_arg1) THEN CAST('Infinity' AS DOUBLE) "
          "WHEN GREATEST(ABS(fj_arg0), ABS(fj_arg1)) > 1e150 OR (GREATEST(ABS(fj_arg0), ABS(fj_arg1)) < 1e-150 "
          "AND GREATEST(ABS(fj_arg0), ABS(fj_arg1)) > 0) THEN GREATEST(ABS(fj_arg0), ABS(fj_arg1)) "
          "* SQRT(1 + POW(LEAST(ABS(fj_arg0), ABS(fj_arg1)) / GREATEST(ABS(fj_arg0), ABS(fj_arg1)), 2)) "
          "ELSE SQRT(POW(fj_arg0, 2) + POW(fj_arg1, 2)) END")


def _try_arithmetic(op: str) -> Callable[..., exp.Expression | None]:
    """try_add, try_subtract and try_multiply: NULL where the result overflows its type, and of two ints an int,
    as in Spark, -2147483648 included (_spark_literal)."""
    def build(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
        return _with_args(f"TRY(fj_arg0 {op} fj_arg1)", [_spark_literal(a) for a in args]) if len(args) == 2 else None
    return build


def _log(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    if isinstance(node, exp.Ln) or len(args) == 1:  # ln(x), and log(x) as Spark's log(e, x)
        return _doubles_once("CASE WHEN fj_arg0 <= 0 THEN NULL ELSE LN(fj_arg0) END", args[:1])
    return _doubles_once(_LOG, args) if len(args) == 2 else None


def _factorial(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """factorial(n) of n cast to an int as Spark casts it (a bigint or decimal: 3.7 is 3), a bigint, NULL
    outside 0 to 20, where DuckDB takes only an INTEGER and stops on a negative one."""
    if len(args) != 1:
        return None
    return _with_args(f"(LIST_TRANSFORM([{_spark_whole('fj_arg0', 'INTEGER')}], lambda kf_f: CASE WHEN kf_f "
                      "BETWEEN 0 AND 20 THEN CAST(FACTORIAL(kf_f) AS BIGINT) END))[1]", args)


# Spark's typeof names: int, bigint, string, decimal(p,s), array<int>, ...
_TYPE_NAMES = [("TINYINT", "tinyint"), ("SMALLINT", "smallint"), ("BIGINT", "bigint"), ("HUGEINT", "bigint"),
               ("INTEGER", "int"), ("VARCHAR", "string"), ("DOUBLE", "double"), ("FLOAT", "float"),
               ("BOOLEAN", "boolean"), ("TIMESTAMP WITH TIME ZONE", "timestamp"), ("TIMESTAMP", "timestamp_ntz"),
               ("DATE", "date"), ("BLOB", "binary"), ("\"NULL\"", "void"), ("NULL", "void")]


def _typeof(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    if len(args) != 1:
        return None
    if isinstance(args[0], exp.Anonymous) and args[0].name.upper() in ("RAISE_ERROR", "ERROR"):
        return exp.Literal.string("void")  # Spark names raise_error's type without running it
    # A scalar type, or a list of one (INTEGER[] -> array<int>); deeper types in lower case.
    # A struct or map is named from its type where spark_complex_types can work it out; otherwise refused.
    stop = ("'typeof() can''t run on this PC the way it runs in Fabric, because the struct or map''s type "
            "couldn''t be worked out here, so nothing ran. Run the cell in Fabric.'")
    scalar = "CASE kf_t " + " ".join(f"WHEN '{d}' THEN '{s}'" for d, s in _TYPE_NAMES) + \
             " ELSE CASE WHEN kf_t LIKE 'DECIMAL(%' THEN REPLACE(LOWER(kf_t), ' ', '') " \
             f"WHEN kf_t LIKE 'STRUCT(%' OR kf_t LIKE 'MAP(%' THEN ERROR({stop}) ELSE LOWER(kf_t) END END"
    whole = (f"(LIST_TRANSFORM([TYPEOF(fj_arg0)], kf_x -> CASE WHEN kf_x LIKE '%[]' AND kf_x NOT LIKE '%[][]' "
             f"AND kf_x NOT LIKE '%(%' THEN 'array<' || (LIST_TRANSFORM([LEFT(kf_x, LENGTH(kf_x) - 2)], kf_t -> {scalar}))[1] "
             f"|| '>' ELSE (LIST_TRANSFORM([kf_x], kf_t -> {scalar}))[1] END))[1]")
    arg = args[0].copy()
    arg.meta["kf_complex_arg"] = True  # spark_complex._mark: named from its type there
    named = _with_args(whole, [arg])
    named.meta["kf_complex"] = "typeof"
    return named


def _sign(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    # Spark's signum is Java's Math.signum, a double: 1.0 or -1.0, and the value itself for NaN, 0.0 and -0.0
    # (DuckDB's SIGN gives 0 for NaN and loses the sign of -0.0).
    # The value is read once, through a lambda, so sign(rand() - 0.5) checks and signs the same sample.
    return _with_args("(LIST_TRANSFORM([CAST(fj_arg0 AS DOUBLE)], kf_v -> CASE WHEN ISNAN(kf_v) OR kf_v = 0 THEN kf_v "
                      "ELSE CAST(SIGN(kf_v) AS DOUBLE) END))[1]", args) if len(args) == 1 else None


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
    d.dddE±n; NaN, Infinity, -0.0. Its lambdas are written as DuckDB 2 reads them (lambda x: ...), which
    DuckDB 1.5 and sqlglot read too, so the SQL also runs as it is (Column.cast)."""
    s = f"CAST(ABS({x}) AS VARCHAR)"
    # DuckDB's shortest digits, in either notation: mantissa digits and the exponent of the first one
    sci = (f"(LIST_TRANSFORM([{{'m': SPLIT_PART({s}, 'e', 1), 'x': COALESCE(TRY_CAST(NULLIF(SPLIT_PART({s}, 'e', 2), '') "
           f"AS INTEGER), 0)}}], lambda kf_n: (LIST_TRANSFORM([{{'i': SPLIT_PART(kf_n.m, '.', 1), 'd': SPLIT_PART(kf_n.m, '.', 1) "
           f"|| SPLIT_PART(kf_n.m, '.', 2), 'x': kf_n.x}}], lambda kf_p: (LIST_TRANSFORM([{{'g': RTRIM(LTRIM(kf_p.d, '0'), '0'), "
           f"'e': kf_p.x + LENGTH(kf_p.i) - 1 - (LENGTH(kf_p.d) - LENGTH(LTRIM(kf_p.d, '0')))}}], "
           f"lambda kf_q: LEFT(kf_q.g, 1) || '.' || COALESCE(NULLIF(SUBSTR(kf_q.g, 2), ''), '0') || 'E' || kf_q.e))[1]))[1]))[1]")
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


def _java_text(a: str, refuse_nested: bool = False) -> str:
    """Java's String.valueOf of a value, as %s prints it (a double as Java writes it). With ``refuse_nested``
    (format_string's %s, array_join), an array, map or struct stops by name: Spark writes them its own way
    there ([1,2], no spaces). Elsewhere (crosstab's labels, java_value_text) DuckDB's text of them is kept,
    which is what Spark gives there."""
    nested = (f"WHEN TYPEOF({a}) LIKE '%]' OR TYPEOF({a}) LIKE 'STRUCT(%' OR TYPEOF({a}) LIKE 'MAP(%' "
              f"THEN CAST(ERROR('An array, map or struct isn''t written as text here as Spark writes it, so nothing "
              f"ran on this PC. Cast it to string in Fabric, or run the cell there.') AS VARCHAR) "
              if refuse_nested else "")
    return (f"(CASE WHEN TYPEOF({a}) = 'DOUBLE' THEN {java_double_text(f'TRY_CAST({a} AS DOUBLE)')} "
            f"WHEN TYPEOF({a}) = 'FLOAT' THEN {java_double_text(f'TRY_CAST({a} AS FLOAT)')} "
            f"{nested}ELSE CAST({a} AS VARCHAR) END)")


def spark_text_arg(a: str, fn: str, bind: bool = False) -> str:
    """DuckDB SQL for Spark's implicit cast of ``a`` to text where ``fn`` takes text (upper(14) is
    upper('14')): a number, boolean, date or timestamp as Spark writes it. Binary, arrays, maps and
    structs are refused by name, where DuckDB would write them its own way. TYPEOF folds when the query
    is planned, so text passes through at no cost; any other type is read once, in a lambda.

    Unbound, ``a`` is written three times, which suits a column or a literal. With ``bind`` the whole
    cast is a lambda over ``a``, written once, so calls nested in each other (lower(trim(x))) don't
    multiply their argument's SQL with each level, and an argument such as rand() is evaluated once."""
    kind = "TYPEOF(kf_t)"
    other = (f"CASE WHEN {kind} = 'BLOB' OR {kind} LIKE '%]' OR {kind} LIKE 'STRUCT(%' "
             f"OR {kind} LIKE 'MAP(%' OR {kind} LIKE 'UNION(%' THEN CAST(ERROR('{fn}() reads text here, and on this PC "
             f"a binary, array, map or struct argument isn''t read as Spark reads it, so nothing ran. Cast it to "
             f"string first, or run the cell in Fabric.') AS VARCHAR) "
             f"WHEN {kind} = 'TIMESTAMP WITH TIME ZONE' THEN CAST(CAST(kf_t AS TIMESTAMP) AS VARCHAR) "
             f"ELSE {_java_text('kf_t')} END")
    if bind:
        return (f"(LIST_TRANSFORM([{a}], lambda kf_t: CASE WHEN {kind} = 'VARCHAR' THEN CAST(kf_t AS VARCHAR) "
                f"ELSE {other} END))[1]")
    return (f"(CASE WHEN TYPEOF({a}) = 'VARCHAR' THEN CAST({a} AS VARCHAR) "
            f"ELSE (LIST_TRANSFORM([{a}], kf_t -> {other}))[1] END)")


# A plain column, a parameter or a literal: cheap to write more than once in a rewrite.
_CHEAP = (exp.Column, exp.Literal, exp.Null, exp.Boolean, exp.Placeholder, exp.Parameter)
# Calls whose DuckDB SQL is text whatever their argument (that argument is made text, by _text_this).
_TEXT_RESULTS = (exp.Upper, exp.Lower, exp.Trim, exp.Pad, exp.Repeat, exp.Left, exp.Right)
_TEXT_KEY = "kf_text_result"  # meta of a rewrite whose SQL is text (initcap's)
_UPPER_KEY = "kf_upper_result"  # meta of upper's rewrite: Java's toUpperCase of it is itself
_TEXT_TYPES = (exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR)
# A column as DuckDB writes one: a name, or quoted names joined by dots
_PLAIN_COLUMN = re.compile(r'\w+|"(?:[^"]|"")*"(?:\."(?:[^"]|"")*")*')


def _cheap(node: exp.Expression) -> bool:
    return isinstance(node.unnest(), _CHEAP)


def _is_text(node: exp.Expression) -> bool:
    """``node`` is surely text in DuckDB, so Spark's implicit cast to text leaves it as it is."""
    if node.meta.get("kf_complex_typed") and isinstance(node.type, exp.DataType):  # spark_complex.TYPED
        return node.type.this in _TEXT_TYPES  # a Spark cast to string, as sql.spark_text_cast writes it
    inner = node.unnest()
    if isinstance(inner, _TEXT_RESULTS) or inner.meta.get(_TEXT_KEY):
        return True
    if isinstance(inner, (exp.Cast, exp.TryCast)) and isinstance(inner.to, exp.DataType):
        return inner.to.this in _TEXT_TYPES
    return False


def _text_of(arg: exp.Expression, fn: str) -> exp.Expression:
    """``arg`` cast to text as Spark casts it where ``fn`` takes text (spark_text_arg): as it is when it
    is text already, written out for a column or literal, else bound once."""
    if _is_text(arg):
        return arg.copy()
    return _with_args(spark_text_arg("fj_arg0", fn, bind=not _cheap(arg)), [arg])


def _read_once(template: str, args: list[exp.Expression], cheap: list[bool] | None = None) -> exp.Expression:
    """``template`` with ``args`` in place of fj_arg0, fj_arg1, ... (as _with_args), where an argument the
    template names more than once and that isn't a column or literal (or, given ``cheap``, isn't marked
    cheap there) is bound once, through a lambda over a one-element list: its SQL isn't repeated, and
    rand() in it is one sample."""
    def uses(i: int) -> int:
        return len(re.findall(rf"\bfj_arg{i}\b", template))
    marks = cheap if cheap is not None else [_cheap(a) for a in args]
    bound = [i for i in range(len(args)) if uses(i) > 1 and not marks[i]]
    if not bound:
        return _with_args(template, args)
    body = re.sub(r"\bfj_arg(\d+)\b",
                  lambda m: f"kf_once.a{m.group(1)}" if int(m.group(1)) in bound else m.group(0), template)
    fields = ", ".join(f"'a{i}': fj_arg{i}" for i in bound)
    return _with_args(f"(LIST_TRANSFORM([{{{fields}}}], lambda kf_once: {body}))[1]", args)


def spark_text_arg_sql(a: str, fn: str) -> str:
    """spark_text_arg over DuckDB SQL ``a``, ready to run (its lambdas written as this DuckDB reads them).
    Only the template goes through sqlglot: ``a`` is put in afterwards as it is, since DuckDB's own SQL
    for an expression (its lambda x: ... syntax) needn't read back. Anything but a column is bound once
    (spark_text_arg), so F.upper(F.upper(...)) doesn't multiply its SQL with each level."""
    from .sql import duckdb_sql  # sql imports this module
    bind = not _PLAIN_COLUMN.fullmatch(a.strip())
    template = duckdb_sql(cast(exp.Expression, sqlglot.parse_one(spark_text_arg("kf_text_arg", fn, bind),
                                                                  read="duckdb")))
    return re.sub(r"\bkf_text_arg\b", lambda _m: f"({a})", template)


def _text_this(fn: str) -> Callable[..., exp.Expression | None]:
    """A typed node whose first argument Spark casts to text: that argument as spark_text_arg."""
    def build(node: exp.Expression, args: list[exp.Expression]) -> None:
        arg = node.args.get("this")
        if isinstance(arg, exp.Expression):
            node.set("this", _text_of(arg, fn))
        return None
    return build


def _initcap(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """initcap(str) as Spark's InitCap: the text lower-cased, then each character after a space (and the first)
    upper-cased; words are split at spaces only. sqlglot's own DuckDB SQL for it reads its argument back as text,
    which fails once that argument is rewritten (initcap(upper(x)) and initcap('aaa') stopped with "Expecting )"),
    so it is written here with the argument node put in, cast to text as Spark casts it."""
    delimiter = node.args.get("expression")
    if delimiter is not None and not (isinstance(delimiter, exp.Literal) and delimiter.is_string
                                      and delimiter.name == " "):
        return None
    table = _title_table()
    special = _sql_text("[" + "".join(table) + "]")
    whens = " ".join(f"WHEN {_sql_text(c)} THEN {_sql_text(t)}" for c, t in table.items())
    title = f"CASE LEFT(kf_w, 1) {whens} ELSE UPPER(LEFT(kf_w, 1)) END"
    plain = "UPPER(LEFT(kf_w, 1))"
    # The text is checked for those characters first, as upper does, so other text skips the long CASE.
    capped = _with_args(f"(LIST_TRANSFORM([{_lower_sql('fj_arg0')}], kf_x -> CASE WHEN "
                        f"REGEXP_MATCHES(kf_x, {special}) THEN ARRAY_TO_STRING(LIST_TRANSFORM(STRING_SPLIT(kf_x, ' '), "
                        f"kf_w -> {title} || SUBSTRING(kf_w, 2)), ' ') ELSE ARRAY_TO_STRING(LIST_TRANSFORM("
                        f"STRING_SPLIT(kf_x, ' '), kf_w -> {plain} || SUBSTRING(kf_w, 2)), ' ') END))[1]",
                        [_text_of(node.this, "initcap")])
    capped.meta[_TEXT_KEY] = True
    return capped


# levenshtein counts character edits in Spark (UTF8String.levenshteinDistance); DuckDB's LEVENSHTEIN counts
# byte edits, so levenshtein('12', 'ÄÖü') was 6, not 3. ASCII text keeps DuckDB's; other text is worked out
# here row by row over the characters (Wagner-Fischer: each row of the table from the one before). LIST_REDUCE
# keeps one type, so each index is a list of one: [i], and a row starts as [i] too.
_CHAR_LEVENSHTEIN = (
    "(LIST_TRANSFORM([{'a': STRING_SPLIT(fj_arg0, ''), 'b': STRING_SPLIT(fj_arg1, '')}], kf_l -> "
    "LIST_REDUCE(LIST_TRANSFORM(RANGE(1, LEN(kf_l.a) + 1), kf_x -> [kf_x]), (kf_p, kf_i) -> "
    "LIST_REDUCE(LIST_TRANSFORM(RANGE(1, LEN(kf_l.b) + 1), kf_y -> [kf_y]), "
    "(kf_r, kf_j) -> LIST_APPEND(kf_r, LEAST(kf_p[kf_j[1] + 1] + 1, kf_r[kf_j[1]] + 1, "
    "kf_p[kf_j[1]] + CASE WHEN kf_l.a[kf_i[1]] = kf_l.b[kf_j[1]] THEN 0 ELSE 1 END)), kf_i), "
    "RANGE(0, LEN(kf_l.b) + 1))[LEN(kf_l.b) + 1]))[1]")
_LEVENSHTEIN = ("CAST(CASE WHEN fj_arg0 IS NULL OR fj_arg1 IS NULL THEN NULL WHEN fj_arg0 = '' THEN LENGTH(fj_arg1) "
                "WHEN fj_arg1 = '' THEN LENGTH(fj_arg0) WHEN NOT REGEXP_MATCHES(fj_arg0 || fj_arg1, '[^[:ascii:]]') "
                f"THEN LEVENSHTEIN(fj_arg0, fj_arg1) ELSE {_CHAR_LEVENSHTEIN} END AS INTEGER)")


def _levenshtein(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """levenshtein(a, b[, threshold]) counting characters, as Spark does. sqlglot reads Spark's third argument,
    the threshold, as ins_cost: with it the distance is -1 when it is past the threshold."""
    if any(node.args.get(k) is not None for k in ("del_cost", "sub_cost", "max_dist")):
        return None
    given = [node.this, node.expression]
    # A column's text is cheap to write out again; anything else is bound once
    distance = _read_once(_LEVENSHTEIN, [_text_of(a, "levenshtein") for a in given], [_cheap(a) for a in given])
    threshold = node.args.get("ins_cost")
    if threshold is None:
        return distance
    return _with_args("(LIST_TRANSFORM([fj_arg0], kf_v -> CASE WHEN fj_arg1 IS NULL THEN NULL "
                      "WHEN kf_v <= fj_arg1 THEN kf_v ELSE -1 END))[1]", [distance, threshold])


@functools.cache
def _full_upper() -> dict[str, str]:
    """The characters whose upper case is more than one character (Unicode's SpecialCasing, as Java's
    toUpperCase gives it: ß is SS); DuckDB's UPPER maps each character to one."""
    return {c: c.upper() for c in map(chr, range(0x80, 0x20000)) if len(c.upper()) > 1}


def _upper(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """upper(str) as Java's toUpperCase: the characters of _full_upper become their full upper case. TEXT is
    checked for them first, so other text takes DuckDB's UPPER as it is."""
    inner = node.this.unnest() if isinstance(node.this, exp.Expression) else None
    if inner is not None and inner.meta.get(_UPPER_KEY):  # upper(upper(x)) is upper(x): its text is upper case
        return node.this.copy()
    table = _full_upper()
    whens = " ".join(f"WHEN {_sql_text(c)} THEN {_sql_text(u)}" for c, u in table.items())
    special = _sql_text("[" + "".join(table) + "]")
    upper = _with_args(f"(LIST_TRANSFORM([fj_arg0], kf_u -> CASE WHEN "
                       f"REGEXP_MATCHES(kf_u, {special}) THEN ARRAY_TO_STRING(LIST_TRANSFORM(STRING_SPLIT(kf_u, ''), "
                       f"kf_c -> CASE kf_c {whens} ELSE UPPER(kf_c) END), '') ELSE UPPER(kf_u) END))[1]",
                       [_text_of(node.this, "upper")])
    upper.meta[_TEXT_KEY] = True  # its SQL is text: an outer text function reads it as it is
    upper.meta[_UPPER_KEY] = True
    return upper


# Java's toLowerCase: İ is i and a combining dot, and a capital sigma that ends a word (a cased letter before
# it, none after) is the final ς, as Unicode's SpecialCasing says. DuckDB's LOWER gave i and σ.
_CASED = r"\p{Lu}\p{Ll}\p{Lt}"
_FINAL_SIGMA = f"([{_CASED}]\\p{{M}}*)Σ($|[^{_CASED}\\p{{M}}])"


def _lower_sql(text: str) -> str:
    return (f"(LIST_TRANSFORM([{text}], kf_l -> CASE WHEN REGEXP_MATCHES(kf_l, '[İΣ]') THEN "
            f"LOWER(REPLACE(REGEXP_REPLACE(kf_l, {_sql_text(_FINAL_SIGMA)}, '\\1ς\\2', 'g'), 'İ', 'i̇')) "
            f"ELSE LOWER(kf_l) END))[1]")


def _lower(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    lower = _with_args(_lower_sql("fj_arg0"), [_text_of(node.this, "lower")])
    lower.meta[_TEXT_KEY] = True
    return lower


@functools.cache
def _title_table() -> dict[str, str]:
    """The characters whose title case (Java's Character.toTitleCase, a character's own simple mapping) isn't
    DuckDB's UPPER of it, and that title case: ß stays ß, dž is ǅ, Georgian letters stay as they are. It reads
    the installed DuckDB's UPPER on purpose, so the table holds what that DuckDB gets wrong and nothing it already
    gets right, whichever DuckDB version runs."""
    def title(c: str) -> str:
        t, u = c.title(), c.upper()
        return t if len(t) == 1 else u if len(u) == 1 else c
    chars = [c for c in map(chr, range(0x80, 0x20000)) if not 0xD800 <= ord(c) <= 0xDFFF]
    upper = dict(zip(chars, _duckdb_upper(chars), strict=True))
    return {c: title(c) for c in chars if title(c) != upper[c]}


def _duckdb_upper(chars: list[str]) -> list[str]:
    import duckdb
    return [row[0] for row in duckdb.connect().execute("SELECT UPPER(UNNEST(?))", [chars]).fetchall()]


def _array_join(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """array_join(array, delimiter[, null_replacement]) as Spark's: NULL when any argument is NULL, NULL
    elements skipped (or replaced), and '' for an array with nothing left; elements are written as Spark
    casts them to text. DuckDB's ARRAY_TO_STRING ignored a NULL delimiter and gave NULL for [NULL, NULL]."""
    replacement = node.args.get("null")
    text = _java_text("kf_e", refuse_nested=True)
    if replacement is None:
        parts = f"LIST_FILTER(LIST_TRANSFORM(fj_arg0, kf_e -> {text}), kf_s -> kf_s IS NOT NULL)"
        return _with_args(f"CASE WHEN fj_arg0 IS NULL OR fj_arg1 IS NULL THEN NULL "
                          f"ELSE COALESCE(ARRAY_TO_STRING({parts}, fj_arg1), '') END", [node.this, node.expression])
    parts = f"LIST_TRANSFORM(fj_arg0, kf_e -> COALESCE({text}, fj_arg2))"
    return _with_args(f"CASE WHEN fj_arg0 IS NULL OR fj_arg1 IS NULL OR fj_arg2 IS NULL THEN NULL "
                      f"ELSE COALESCE(ARRAY_TO_STRING({parts}, fj_arg1), '') END", [node.this, node.expression, replacement])


# array_insert(array, pos, value) as Spark 3.5's: a position past the end pads with NULLs, and a negative one
# counts from the end, -1 being after the last element, padding at the front past the start. 0 is an error.
# Each argument is read once (kf_q.a, .p, .v), and padding of more than _PAD_MOST NULLs is refused by name before
# it is built: Spark pads up to its array limit, but a billion NULLs would use up this PC's memory.
_PAD_MOST = 10_000_000
_ARRAY_INSERT = (
    "(LIST_TRANSFORM([{'a': fj_arg0, 'p': CAST(fj_arg1 AS BIGINT), 'v': fj_arg2}], kf_q -> "
    "(LIST_TRANSFORM([CAST(LEN(kf_q.a) AS BIGINT)], kf_n -> "
    "CASE WHEN kf_q.a IS NULL OR kf_q.p IS NULL THEN NULL "
    "WHEN kf_q.p = 0 THEN ERROR('[INVALID_INDEX_OF_ZERO] The index 0 is invalid. An index shall be either < 0 or > 0 "
    "(the first element has index 1).') "
    f"WHEN kf_q.p - kf_n - 1 > {_PAD_MOST} OR -(kf_n + kf_q.p + 1) > {_PAD_MOST} THEN ERROR('This Spark SQL can''t be "
    f"run on this PC: array_insert would pad the array with more than {_PAD_MOST:,} NULLs. Spark pads up to its "
    "array limit, but this PC refuses that many to keep its memory.') "
    "WHEN kf_q.p > kf_n + 1 THEN LIST_CONCAT(kf_q.a, LIST_TRANSFORM(RANGE(kf_q.p - kf_n - 1), "
    "kf_i -> CASE WHEN FALSE THEN kf_q.v END), [kf_q.v]) "
    "WHEN kf_q.p > 0 THEN LIST_CONCAT(LIST_SLICE(kf_q.a, 1, kf_q.p - 1), [kf_q.v], LIST_SLICE(kf_q.a, kf_q.p, kf_n)) "
    "WHEN kf_n + kf_q.p + 1 >= 0 THEN LIST_CONCAT(LIST_SLICE(kf_q.a, 1, kf_n + kf_q.p + 1), [kf_q.v], "
    "LIST_SLICE(kf_q.a, kf_n + kf_q.p + 2, kf_n)) "
    "ELSE LIST_CONCAT([kf_q.v], LIST_TRANSFORM(RANGE(-(kf_n + kf_q.p + 1)), kf_i -> CASE WHEN FALSE THEN kf_q.v END), "
    "kf_q.a) END))[1]))[1]")


def _array_insert(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    if isinstance(node.args["position"], exp.Null):
        raise ValueError("array_insert() needs an INT position, as in Spark, where a bare NULL has no type: "
                         "[DATATYPE_MISMATCH.UNEXPECTED_INPUT_TYPE]. Write CAST(NULL AS INT).")
    return _with_args(_ARRAY_INSERT, [node.this, node.args["position"], node.expression])


def _strposition(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """locate(substr, str, pos) and instr: an empty substr is found at 1 from any start of 1 or more, as
    Spark's UTF8String.indexOf has it (DuckDB found it at the start). Otherwise as instr."""
    _text_this("instr")(node, args)
    find = node.args.get("substr")
    if isinstance(find, exp.Expression):  # instr(12345, 34) is 3: Spark casts the substring to text too
        node.set("substr", _text_of(find, "instr"))
    start = node.args.get("position")
    find = node.args.get("substr")
    if start is None or find is None or (isinstance(find, exp.Literal) and find.is_string and find.name):
        return None
    # The node names fj_arg0 and fj_arg1 itself, so _with_args sees each read twice and binds a big one once:
    # nested locate(b, locate(...), 2) grows linearly with its depth, not 3x per level.
    inner = node.copy()
    inner.set("substr", exp.column("fj_arg0"))
    inner.set("position", exp.column("fj_arg1"))
    template = cast(exp.Expression, sqlglot.parse_one("CASE WHEN fj_arg0 = '' AND fj_arg1 >= 1 THEN 1 ELSE fj_arg2 END",
                                                      read="duckdb"))
    for column in list(template.find_all(exp.Column)):
        if column.name == "fj_arg2":
            column.replace(inner)
    return _with_args(template, [find, start])


_CONCAT_WS_NESTED = ("concat_ws() takes only text or arrays of text, and this is a map or struct, so nothing ran on "
                     "this PC. Spark stops on it too, with a data type mismatch. Cast the map or struct to string, "
                     "or pick its fields, first.")


def refuse_nested_concat_ws(statement: exp.Expression) -> exp.Expression:
    """concat_ws of a map or struct refused by name, as Spark refuses it ([DATATYPE_MISMATCH]); DuckDB wrote its own
    text of it ({'a': 1}). Run on the Spark tree as read, before its parts are cast to text: a part that isn't a
    literal or an array is checked by its DuckDB type when the query runs (check_nested_concat_ws)."""
    for node in list(statement.find_all(exp.ConcatWs)):
        for part in list(node.expressions)[1:]:
            if isinstance(part, (exp.Struct, exp.Map, exp.VarMap)):
                raise ValueError(_CONCAT_WS_NESTED)
            from .spark_strings import _ARRAYS
            if isinstance(part, _ARRAYS) and any(isinstance(e, (exp.Struct, exp.Map, exp.VarMap))
                                                 for e in part.expressions):
                raise ValueError(_CONCAT_WS_NESTED)  # array(named_struct(...)): Spark refuses array<struct> too
    return statement


def check_nested_concat_ws(statement: exp.Expression) -> exp.Expression:
    """concat_ws's parts that aren't literals or arrays, checked by their DuckDB type when the query runs (a
    map or struct column is refused as refuse_nested_concat_ws refuses a written one). sql.spark_fixes runs it
    once the names the person wrote are checked, since it adds a lambda of its own (kf_cw)."""
    from .spark_strings import _ARRAYS
    for node in list(statement.find_all(exp.ConcatWs)):
        for part in list(node.expressions)[1:]:
            if isinstance(part, (exp.Literal, exp.Null, exp.Cast, exp.TryCast, *_ARRAYS)):
                continue  # an array call stays as it is: spark_strings joins its elements
            # The part is read once, through a lambda, so nested concat_ws doesn't repeat it at each level.
            check = sqlglot.parse_one(
                "(LIST_TRANSFORM([fj_part], kf_cw -> CASE WHEN TYPEOF(kf_cw) LIKE 'STRUCT(%' OR TYPEOF(kf_cw) "
                "LIKE 'MAP(%' THEN ERROR(fj_why) ELSE kf_cw END))[1]", read="duckdb")
            why = next(c for c in check.find_all(exp.Column) if c.name == "fj_why")
            why.replace(exp.Literal.string(_CONCAT_WS_NESTED))
            slot = next(c for c in check.find_all(exp.Column) if c.name == "fj_part")
            part.replace(check)
            slot.replace(part)
    return statement


def _concat_ws(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """concat_ws(sep) alone is '' (NULL for a NULL sep), as in Spark; DuckDB's needs a value to join."""
    if len(node.expressions) != 1:
        return None
    return _with_args("CASE WHEN fj_arg0 IS NULL THEN NULL ELSE '' END", [node.expressions[0]])


def _contains(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """contains with a bare NULL is NULL, as in Spark; DuckDB couldn't choose which contains was meant."""
    for key in ("this", "expression"):
        if isinstance(node.args.get(key), exp.Null):
            node.set(key, exp.cast(exp.null(), "VARCHAR"))
    return None


def _empty_match(pattern: str) -> bool:
    try:
        return re.search(pattern, "") is not None
    except re.error:  # silent-ok: not Python's syntax, so left to DuckDB as before
        return False


def _split(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """split(str, regex, limit), as Spark's: with a limit above 0, at most that many parts, the last holding
    the rest of the text (as spark_compat.split); 0 or less splits at every match."""
    _text_this("split")(node, args)
    pattern = node.expression
    if isinstance(pattern, exp.Literal) and pattern.is_string and pattern.this and _empty_match(pattern.this):
        raise ValueError(f"split() can't run on this PC with a pattern that can match empty text ({pattern.this!r}), so "
                         "nothing ran: Java and this PC split around an empty match differently. Use a pattern that "
                         "always matches a character, or run the cell in Fabric.")
    limit = node.args.get("limit")
    if limit is None:
        return None
    if not (isinstance(limit, exp.Literal) and not limit.is_string) and not (
            isinstance(limit, exp.Neg) and isinstance(limit.this, exp.Literal)):
        raise ValueError("split() can't run on this PC with a limit worked out from the data, so nothing ran. Give "
                         "the limit as a number, or run the cell in Fabric.")
    n = int(limit.this.this) * -1 if isinstance(limit, exp.Neg) else int(limit.this)
    text, pattern = node.this, node.expression
    if isinstance(pattern, exp.Literal) and pattern.is_string and pattern.this == "":
        chars = "STRING_SPLIT(fj_arg0, '')"
        return _with_args(chars if n <= 0 else f"{chars}[1:{n}]", [text])
    parts = "REGEXP_SPLIT_TO_ARRAY(fj_arg0, fj_arg1)"
    if n <= 0:
        return _with_args(parts, [text, pattern])
    seps = "REGEXP_EXTRACT_ALL(fj_arg0, fj_arg1)"
    before = (f"COALESCE(LIST_SUM(LIST_TRANSFORM({parts}[1:{n - 1}], kf_p -> LENGTH(kf_p))), 0) + "
              f"COALESCE(LIST_SUM(LIST_TRANSFORM({seps}[1:{n - 1}], kf_p -> LENGTH(kf_p))), 0)")
    return _with_args(f"CASE WHEN fj_arg0 IS NULL THEN NULL WHEN LEN({parts}) <= {n} THEN {parts} "
                      f"ELSE LIST_CONCAT({parts}[1:{n - 1}], [SUBSTRING(fj_arg0, CAST({before} + 1 AS BIGINT))]) END",
                      [text, pattern])


def _replace(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """replace(str, search) drops each search, as replace(str, search, '') does in Spark."""
    _text_this("replace")(node, args)
    if node.args.get("replacement") is None:
        node.set("replacement", exp.Literal.string(""))
    return None


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
            text = f"(CASE WHEN {a} IS NULL THEN {null} ELSE PRINTF('%{size}{cut}s', {_java_text(a, refuse_nested=True)}) END)"
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
        return _read_once(java_format_sql(args[0].this, len(args) - 1), args[1:])
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
    if any(isinstance(a, exp.Null) for a in args):
        raise ValueError("json_tuple's arguments must all be strings, as in Spark, so nothing ran. Write a NULL "
                         "as CAST(NULL AS STRING).")
    # A key the object repeats gives its last value, as in Spark (json_keys and $.* list the object's
    # members in order); a string value without its quotes.
    fields = ", ".join(f"'c{i}': (LIST_TRANSFORM(LIST_FILTER(RANGE(1, LEN(fj_o.k) + 1), fj_n -> fj_o.k[fj_n] = "
                       f"fj_arg{i + 1}), fj_n -> fj_o.v[fj_n] ->> '$'))[-1]" for i in range(len(args) - 1))
    members = ("CASE WHEN JSON_VALID(fj_arg0) AND JSON_TYPE(fj_arg0) = 'OBJECT' THEN {'k': JSON_KEYS(fj_arg0), "
               "'v': JSON_EXTRACT(fj_arg0, '$.*')} END")
    return _with_args(f"UNNEST(LIST_TRANSFORM([{members}], fj_o -> {{{fields}}}), max_depth := 2)", args)


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
    """timestamp_seconds/millis/micros: the instant that many units after 1970-01-01 00:00 UTC, as Spark's
    timestamp (with time zone)."""
    to_micros = "fj_arg0" if scale == 1 else f"TRUNC(CAST(fj_arg0 AS DECIMAL(38, 6)) * {scale})"
    return _template(f"TIMEZONE('UTC', MAKE_TIMESTAMP(CAST({to_micros} AS BIGINT)))", 1)


def _make_dt_interval(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    if len(args) > 4:
        return None
    parts = ["TO_DAYS(CAST(fj_arg0 AS INTEGER))", "TO_HOURS(CAST(fj_arg1 AS BIGINT))",
             "TO_MINUTES(CAST(fj_arg2 AS BIGINT))", "TO_MICROSECONDS(CAST(ROUND(fj_arg3 * 1000000) AS BIGINT))"]
    return _with_args(" + ".join(parts[:len(args)]) if args else "INTERVAL 0 SECOND", args)


def _to_timestamp(safe: bool) -> Callable[..., exp.Expression | None]:
    """to_timestamp_ntz/ltz and try_to_timestamp read text as Spark's to_timestamp does. to_timestamp_ltz
    and try_to_timestamp give Spark's timestamp (with time zone, in the session's zone); to_timestamp_ntz
    a timestamp without one."""
    def build(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
        from . import spark_datetime
        ntz = node.name.upper() == "TO_TIMESTAMP_NTZ"
        if len(args) == 1:
            return _with_args(f"TRY_CAST(fj_arg0 AS {'TIMESTAMP' if ntz else 'TIMESTAMPTZ'})", args)
        if len(args) != 2 or not args[1].is_string:
            return None  # sqlglot reads a format only when it is written out
        if not ntz:
            return _with_args(spark_datetime.parse_sql(args[1].name, node.name.lower()), args[:1])
        tree = sqlglot.parse_one(f"SELECT TO_TIMESTAMP(fj_arg0, {args[1].sql('spark')})", read="spark").expressions[0]
        for column in list(tree.find_all(exp.Column)):
            if column.name == "fj_arg0":
                column.replace(args[0].copy())
        if safe:  # try_to_timestamp: NULL for text that isn't a timestamp, with ANSI on too
            for strptime in tree.find_all(exp.StrToTime):
                strptime.set("safe", True)
        return tree
    return build


def _zone(zone: exp.Expression) -> exp.Expression:
    """A time zone argument written out as text, as DuckDB names it (spark_time_zone.zone_id)."""
    if isinstance(zone, exp.Literal) and zone.is_string:
        return exp.Literal.string(spark_time_zone.zone_id(zone.name))
    return zone


def _make_timestamp(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """make_timestamp, make_timestamp_ltz and make_timestamp_ntz: NULL for a date or time that doesn't
    exist (ANSI off). make_timestamp and _ltz give Spark's timestamp: the wall time in the session's zone,
    or in the zone given as the seventh argument; _ntz a timestamp without a zone."""
    ntz = isinstance(node, exp.Anonymous) and node.name.upper() == "MAKE_TIMESTAMP_NTZ"
    made = "TRY(MAKE_TIMESTAMP(fj_arg0, fj_arg1, fj_arg2, fj_arg3, fj_arg4, CAST(fj_arg5 AS DOUBLE)))"
    if len(args) == 6:
        return _with_args(made if ntz else f"CAST({made} AS TIMESTAMPTZ)", args)
    if len(args) == 7 and not ntz:
        zone = "CAST(fj_arg6 AS VARCHAR)"
        return _with_args(f"TRY({spark_time_zone.earlier_in(zone, f'TIMEZONE({zone}, {made})')})",
                          [*args[:6], _zone(args[6])])
    return None  # left to DuckDB, which refuses it


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
    a value hashed by its type, or added in try_add, must be the type Spark gives it."""
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


def _user_size(arg: exp.Expression) -> exp.Expression:
    size = exp.ArraySize(this=arg.copy())
    size.meta["kf_complex_written"] = True  # the query's own call (spark_complex.mark_calls)
    return size


# shiftrightunsigned (>>> in Java): the bits move as an unsigned long or int, by the count mod 64 or 32
_SHIFT_UNSIGNED = ("CASE WHEN TYPEOF(fj_arg0) = 'BIGINT' THEN CAST(CASE WHEN fj_arg1 % 64 = 0 "
                   "THEN fj_arg0::HUGEINT ELSE (fj_arg0::HUGEINT & 18446744073709551615) >> (fj_arg1 % 64) END AS BIGINT) "
                   "ELSE CAST(CASE WHEN fj_arg1 % 32 = 0 THEN fj_arg0::BIGINT "
                   "ELSE (fj_arg0::BIGINT & 4294967295) >> (fj_arg1 % 32) END AS INTEGER) END")
_shift_unsigned = _template(_SHIFT_UNSIGNED, 2)

_ANON: dict[str, Callable[..., exp.Expression | None]] = {
    **_AGGREGATES,
    "ARRAY_REPEAT": _template("CASE WHEN fj_arg1 IS NULL THEN NULL ELSE LIST_TRANSFORM(RANGE(GREATEST(fj_arg1, 0)), "
                              "kf_i -> fj_arg0) END", 2),
    "ASSERT_TRUE": _template("CASE WHEN fj_arg0 THEN NULL ELSE ERROR('''' || CAST(fj_arg0 AS VARCHAR) "
                             "|| ''' is not true!') END", 1),
    # spaces, or each of the characters given, off both ends
    "BTRIM": lambda n, a: _with_args("TRIM(fj_arg0)" if len(a) == 1 else "TRIM(fj_arg0, fj_arg1)", a) if len(a) in (1, 2) else None,
    # cardinality is size: spark_complex gives -1 for NULL, an int, and a map's key count.
    "CARDINALITY": lambda n, a: None if len(a) != 1 else _user_size(a[0]),
    "CRC32": _template(_CRC32, 1),
    "CSC": _template("1 / SIN(CAST(fj_arg0 AS DOUBLE))", 1),
    "E": _template("EXP(1.0)", 0),
    "EXPM1": _template(_EXPM1, 1),
    "FIND_IN_SET": _template(_FIND_IN_SET, 2),
    "GET": _template("CASE WHEN fj_arg1 < 0 THEN NULL ELSE fj_arg0[fj_arg1 + 1] END", 2),
    "HASH": _hash("hash"),
    "HYPOT": lambda n, a: _doubles_once(_HYPOT, a) if len(a) == 2 else None,
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
    "SHIFTRIGHTUNSIGNED": lambda n, a: None if (u := _shift_unsigned(n, a)) is None else _int_when_int(exp.paren(u), a[0]),
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
    "AES_ENCRYPT": _refuse("aes_encrypt", "AES isn't available in the engine on this PC."),
    "TRY_AES_ENCRYPT": _refuse("try_aes_encrypt", "AES isn't available in the engine on this PC."),
    **{name: _refuse(name.lower(), _JAVA) for name in ("JAVA_METHOD", "REFLECT")},
    **{name: _refuse(name.lower(), _INPUT_FILE) for name in ("INPUT_FILE_NAME", "INPUT_FILE_BLOCK_LENGTH",
                                                             "INPUT_FILE_BLOCK_START")},
    "SQL_KEYWORDS": _sql_keywords,
    "SPARK_PARTITION_ID": _refuse("spark_partition_id", "this PC doesn't split a query's rows into Spark "
                                  "partitions, so there is no partition number to give."),
    # Increasing and unique, as Spark promises (its values have gaps between partitions), as
    # spark_compat.monotonically_increasing_id writes it for a DataFrame
    "MONOTONICALLY_INCREASING_ID": _row_ids,
    # Stops the query with the message, as Spark's does (Spark's error class is USER_RAISED_EXCEPTION);
    # a NULL message stops it too, with the message null, as Java prints it
    "RAISE_ERROR": _template("ERROR(COALESCE(CAST(fj_arg0 AS VARCHAR), 'null'))", 1),
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
    # Java's URLDecoder, as Spark's: + is a space, and a % that doesn't start two hex digits is an error.
    # Bytes that aren't UTF-8 are refused: Java puts U+FFFD in their place, by rules this PC doesn't follow.
    "URL_DECODE": _template(
        "CASE WHEN fj_arg0 IS NULL THEN NULL "
        "WHEN NOT REGEXP_FULL_MATCH(fj_arg0, '([^%]|%[0-9A-Fa-f]{2})*') THEN ERROR('[CANNOT_DECODE_URL] The provided "
        "URL cannot be decoded: ' || fj_arg0 || '. Please ensure that the URL is properly formatted and try again.') "
        "ELSE COALESCE(TRY(URL_DECODE(REPLACE(fj_arg0, '+', ' '))), ERROR('url_decode() can''t run on this PC the way "
        "it runs in Fabric, so nothing ran: ' || fj_arg0 || ' decodes to bytes that aren''t UTF-8, which Spark "
        "replaces with U+FFFD. Run the cell in Fabric.')) END", 1),
    # Java's URLEncoder, as Spark's: a space is +, and only A-Z a-z 0-9 . - * _ stay as they are.
    "URL_ENCODE": _template("REPLACE(REPLACE(REPLACE(URL_ENCODE(fj_arg0), '%20', '+'), '~', '%7E'), '%2A', '*')", 1),
    "XXHASH64": _hash("xxhash64"),
}


# randn(): a standard normal draw (Box-Muller from two uniform draws in [0, 1)); 1 - random() keeps LN off 0.
# Spark's seeded randn gives its own XORShift sequence, which this PC can't make, so a seed is refused.
_RANDN = "(SQRT(-2 * LN(1 - RANDOM())) * COS(2 * PI() * RANDOM()))"
# shuffle(a): a random order of a's elements (each sorted by its own random draw), NULLs kept; the array
# is read once.
_SHUFFLE = ("(LIST_TRANSFORM([fj_arg0], kf_a -> CASE WHEN kf_a IS NULL THEN NULL ELSE LIST_TRANSFORM(LIST_SORT("
            "LIST_TRANSFORM(kf_a, (kf_v, kf_i) -> {'r': RANDOM(), 'i': kf_i})), kf_p -> kf_a[kf_p.i]) END))[1]")
_SEEDED_RANDN = _refuse("randn", "with a seed Spark gives its own sequence of numbers, which this PC can't make.",
                        "leave out the seed")


def _spark_whole(value: str, duck_type: str) -> str:
    """``value`` cast to a whole-number type as Spark casts it where a function takes one (a decimal or double
    is cut toward zero, and one that doesn't fit keeps its low bits or the nearest end, as Java's casts:
    sql.spark_integer_cast), evaluated once."""
    from . import sql
    spark_type = {"INTEGER": "INT", "BIGINT": "BIGINT"}[duck_type]
    mode = "ansi" if _ANSI.get() else "legacy"
    return f"(LIST_TRANSFORM([{value}], kf_w -> {sql.spark_integer_cast('kf_w', duck_type, spark_type, mode)}))[1]"


_LONG_TYPES = "('BIGINT', 'HUGEINT', 'UBIGINT', 'UHUGEINT', 'UINTEGER')"
# Java's shifts: the count is taken mod 32 for an int and mod 64 for a long (n & 31, n & 63), << keeps the low
# bits, and >> is arithmetic. DuckDB stops on a negative count, and on << of a negative number.
_SHIFT_LEFT = ("CAST((LIST_TRANSFORM([((kf_s.v::HUGEINT & {mask}) & ((1::HUGEINT << ({bits} - kf_s.k)) - 1)) << kf_s.k], "
               "kf_r -> CASE WHEN kf_r >= {half} THEN kf_r - {size} ELSE kf_r END))[1] AS {duck})")
_SHIFT_RIGHT = "CAST(CASE WHEN kf_s.v >= 0 THEN kf_s.v >> kf_s.k ELSE -1 - ((-1 - kf_s.v) >> kf_s.k) END AS {duck})"


def _shift_sql(left: bool, bits: int) -> str:
    duck = "BIGINT" if bits == 64 else "INTEGER"
    body = (_SHIFT_LEFT if left else _SHIFT_RIGHT).format(mask=2 ** bits - 1, bits=bits, half=2 ** (bits - 1),
                                                         size=2 ** bits, duck=duck)
    return (f"(LIST_TRANSFORM([{{'v': CAST({_spark_whole('fj_arg0', duck)} AS BIGINT), "
            f"'k': CAST({_spark_whole('fj_arg1', 'INTEGER')} AS BIGINT) & {bits - 1}}}], kf_s -> {body}))[1]")


def _int_literal(node: exp.Expression) -> int | None:
    inner = node.this if isinstance(node, exp.Neg) else node
    if isinstance(inner, exp.Literal) and inner.is_int:
        return -int(inner.name) if isinstance(node, exp.Neg) else int(inner.name)
    return None


def _shift(left: bool) -> Callable[..., exp.Expression | None]:
    """shiftleft and shiftright as Spark's (Java's) on an int or a long. An int literal keeps the int path, so
    the result is an int as in Spark; otherwise the value's type picks the path when the query runs."""
    def build(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression:
        operands = [node.this, node.expression]
        literal = _int_literal(node.this)
        if literal is not None:
            return exp.paren(_with_args(_shift_sql(left, 32 if -2 ** 31 <= literal < 2 ** 31 else 64), operands))
        return _int_when_int(exp.paren(_with_args(f"CASE WHEN TYPEOF(fj_arg0) IN {_LONG_TYPES} THEN {_shift_sql(left, 64)} "
                                                  f"ELSE {_shift_sql(left, 32)} END", operands)), node.this)
    return build



def _int_when_int(new: exp.Expression, value: exp.Expression) -> exp.Expression:
    """``new``, a shift that picks its int or long path by ``value``'s type when the query runs, so DuckDB
    types it BIGINT either way: marked for spark_complex_types, which casts it to INT where ``value`` is
    a tinyint, smallint or int, as Spark's shiftleft, shiftright and shiftrightunsigned of one is an int."""
    from . import spark_complex
    arg = next((n for n in new.walk() if n is not new and n == value), None)
    if arg is not None:
        spark_complex._mark(new, "int_shift", arg)
    return new


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
        # Spark's uuid() is a string; DuckDB's is its own UUID type, which string functions don't take.
        ("Uuid", lambda n, a: exp.cast(n.copy(), exp.DataType.build("VARCHAR")) if not a else None),
        ("Shuffle", _template(_SHUFFLE, 1)),
        ("Randn", lambda n, a: _with_args(_RANDN, []) if not a else _SEEDED_RANDN(n, a)),
        ("TsOrDsToDate", _to_date_with_format),
        ("TimeToStr", _date_format),
        ("StrToTime", _to_timestamp_with_format),
        ("UnixToStr", _date_format),
        ("ArraysZip", _arrays_zip),
        ("BitmapConstructAgg", _template(_BITMAP_CONSTRUCT_AGG, 1)),
        ("BitmapCount", _template(_BITMAP_COUNT, 1)),
        ("BitmapOrAgg", _template(_BITMAP_OR_AGG, 1)),
        ("FirstValue", lambda n, a: None if n.find_ancestor(exp.Window) else _with_args("FIRST(fj_arg0)", [n.this])),
        ("LastValue", lambda n, a: None if n.find_ancestor(exp.Window) else _with_args("LAST(fj_arg0)", [n.this])),
        ("Format", _format_string),
        # from_utc_timestamp and to_utc_timestamp: Spark's timestamp (with time zone), whose wall time in
        # the session's zone is the converted one
        ("AtTimeZone", lambda n, a: _with_args("CAST(TIMEZONE(fj_arg1, TIMEZONE('UTC', TRY_CAST(fj_arg0 AS TIMESTAMP))) "
                                               "AS TIMESTAMPTZ)", [_uncast(n.this), _zone(n.args["zone"])])),
        ("FromTimeZone", lambda n, a: _with_args(
            "CAST(TIMEZONE('UTC', " + spark_time_zone.earlier_in("fj_arg1", "TIMEZONE(fj_arg1, TRY_CAST(fj_arg0 AS TIMESTAMP))")
            + ") AS TIMESTAMPTZ)", [_uncast(n.this), _zone(n.args["zone"])])),
        ("TimestampFromParts", _make_timestamp),
        ("ToNumber", lambda n, a: _to_number(False)(n, [n.this, n.args["format"]] if n.args.get("format") else [n.this])),
        ("ToChar", lambda n, a: _to_char("to_char")(n, [n.this, n.args["format"]]) if n.args.get("format") else None),
        ("ParseUrl", _parse_url),
        ("Extract", _extract),
        ("Hour", _template(f"HOUR({_TS})", 1)),
        # Spark casts a number, boolean or date to text where these take text (upper(14) = '14'). Not initcap:
        # sqlglot writes its DuckDB SQL by reading the argument back, which this one can't be.
        *((name, _text_this(fn)) for name, fn in (
            ("Trim", "trim"), ("Ascii", "ascii"), ("Length", "length"),
            ("Translate", "translate"), ("RegexpReplace", "regexp_replace"), ("Left", "left"), ("Right", "right"),
            ("Repeat", "repeat"), ("Pad", "lpad"))),
        ("RegexpSplit", _split),
        ("Upper", _upper),
        ("Lower", _lower),
        ("StrPosition", _strposition),
        ("ArrayToString", _array_join),
        ("ArrayInsert", _array_insert),
        ("ConcatWs", _concat_ws),
        ("Contains", _contains),
        ("Replace", _replace),
        ("ToBase64", _base64),
        ("FromBase64", lambda n, a: None if _ANSI.get() else _with_args(_UNBASE64, [n.this])),
        ("Hex", lambda n, a: _with_args(_HEX.replace("kf_spark_whole", _spark_whole("fj_arg0", "BIGINT")), [n.this])),
        ("Unhex", _template(_UNHEX, 1)),
        ("SHA2", _sha2),
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
            # LEFT of 0 or less: Spark's substringSQL(1, pos - 1) is empty there, where DuckDB's LEFT(s, -n) drops
            # the last n characters (overlay('abc' PLACING ',' FROM 0) is ',abc', not 'ab,abc').
            "LEFT(fj_arg0, GREATEST(fj_arg2 - 1, 0)) || fj_arg1 || SUBSTR(fj_arg0, fj_arg2 + "
            + ("CASE WHEN fj_arg3 < 0 THEN LENGTH(fj_arg1) ELSE fj_arg3 END" if n.args.get("for_") else "LENGTH(fj_arg1)") + ")",
            [n.this, n.expression, n.args["from_"], *([n.args["for_"]] if n.args.get("for_") else [])])),
        ("Initcap", _initcap),
        # Spark's sqrt is Java's Math.sqrt: NaN below 0, where DuckDB's stops with "cannot take square root"
        # (read once, so sqrt(rand() - 0.5) checks and takes the root of the same sample)
        ("Sqrt", _template("(LIST_TRANSFORM([CAST(fj_arg0 AS DOUBLE)], kf_v -> CASE WHEN kf_v < 0 THEN "
                           "CAST('NaN' AS DOUBLE) ELSE SQRT(kf_v) END))[1]", 1)),
        ("Levenshtein", _levenshtein),
        ("Ln", _log),
        ("Log", _log),
        ("Pow", lambda n, a: _doubles_once(_POW, a) if len(a) == 2 else None),
        ("Factorial", _factorial),
        # Java's Math.acos and Math.asin: NaN outside [-1, 1], where DuckDB 1.5 stops
        *((name, _template(f"CASE WHEN ABS(CAST(fj_arg0 AS DOUBLE)) > 1 THEN {_NAN} ELSE {name.upper()}(fj_arg0) END", 1))
          for name in ("Acos", "Asin")),
        # Spark's cot is 1 / Math.tan: inf at 0, where DuckDB 1.5 stops
        ("Cot", _template("1 / TAN(CAST(fj_arg0 AS DOUBLE))", 1)),
        # Spark's atan2 adds 0.0 to each side first, so -0.0 counts as 0.0: atan2(0.0, -0.0) is 0.0, not pi
        ("Atan2", lambda n, a: _doubles_once("ATAN2(CASE WHEN fj_arg0 = 0 THEN 0.0 ELSE fj_arg0 END, "
                                          "CASE WHEN fj_arg1 = 0 THEN 0.0 ELSE fj_arg1 END)", a)
         if len(a) == 2 else None),
        # bit_count counts the bits of the value as a long (Long.bitCount), so -1 gives 64 for an int too.
        ("BitwiseCount", lambda n, a: _with_args(f"CAST(BIT_COUNT({_spark_whole('fj_arg0', 'BIGINT')}) AS INTEGER)",
                                                 [n.this])),
        ("BitwiseLeftShift", _shift(left=True)),
        ("BitwiseRightShift", _shift(left=False)),
        # isnan(NULL) is false in Spark (IsNaN isn't nullable)
        ("IsNan", _template("COALESCE(ISNAN(fj_arg0), FALSE)", 1)),
        ("PosexplodeOuter", _template(_POSEXPLODE_OUTER, 1)),
        ("ExplodeOuter", _template(_EXPLODE_OUTER, 1)),
        ("RegexpSubstr", lambda n, a: None if len(a) != 2 else _with_args(
            "CASE WHEN REGEXP_MATCHES(fj_arg0, fj_arg1) THEN REGEXP_EXTRACT(fj_arg0, fj_arg1) END", a)),
        ("Rint", _template("ROUND_EVEN(CAST(fj_arg0 AS DOUBLE), 0)", 1)),
        ("Sign", _sign),
        ("Stack", _stack),
        ("SubstringIndex", _substring_index),
        ("SafeAdd", _try_arithmetic("+")),
        ("SafeSubtract", _try_arithmetic("-")),
        ("SafeMultiply", _try_arithmetic("*")),
        ("Typeof", _typeof),
        ("WidthBucket", lambda n, a: _with_args(_WIDTH_BUCKET, [n.this, n.args["min_value"], n.args["max_value"],
                                                                n.args["num_buckets"]])),
    ]
    return [(cls, build) for name, build in table if (cls := getattr(exp, name, None)) is not None]


def written_pattern(node: exp.Expression) -> str | None:
    """A datetime pattern, the second argument, as the person wrote it (date_format(x, 'yyyy-MM-dd')),
    before sqlglot turned it into strftime codes; None when it isn't a constant text."""
    from . import spark_sql_names
    written = node.meta.get(spark_sql_names._ARGS)
    if not written or len(written) != 2:
        return None
    try:
        literal = sqlglot.parse_one(written[1], read="spark")
    except sqlglot.errors.ParseError:  # silent-ok: not a constant, so it stays as sqlglot reads it
        return None
    return literal.name if isinstance(literal, exp.Literal) and literal.is_string else None


def _date_format(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """date_format(x, pattern) and from_unixtime(seconds, pattern) with Spark's datetime patterns, in
    the session's time zone (spark_datetime). A pattern letter Spark refuses is refused here by name."""
    from . import spark_datetime, spark_sql_names
    name = node.meta.get(spark_sql_names._NAME)
    pattern = written_pattern(node) if name in ("date_format", "from_unixtime") else None
    if pattern is None:
        return None
    if isinstance(node, exp.UnixToStr):
        value: exp.Expression = _with_args("TO_TIMESTAMP(TRUNC(CAST(fj_arg0 AS DOUBLE)))", [node.this])
    else:
        value = node.this.this if isinstance(node.this, exp.TimeStrToTime) else node.this
    return _with_args(spark_datetime.format_sql(pattern, strict=_ANSI.get()), [value])


def _to_timestamp_with_format(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """to_timestamp(text, pattern): Spark's timestamp (with time zone, in the session's zone), read with
    Spark's datetime pattern (spark_datetime); NULL for text that doesn't match, as with ANSI off."""
    from . import spark_datetime, spark_sql_names
    if node.meta.get(spark_sql_names._NAME) != "to_timestamp":
        return None
    pattern = written_pattern(node)
    if pattern is None:
        return None
    return _with_args(spark_datetime.parse_sql(pattern, "to_timestamp", strict=_ANSI.get()), [node.this])


# Spark's reading of text as a date (DateTimeUtils.stringToDate): spaces and control characters trimmed, an
# optional sign, a year of 4 to 7 digits, then optionally -month (1 or 2 digits) and -day (1 or 2), and after the
# day ' ' or 'T' and anything. A part left out is 1; a date that doesn't exist, or anything else, is NULL.
# DuckDB reads '24-02-29' as the year 24.
_TEXT_DATE = ("^[\\s\\p{Cc}]*([+-]?)([0-9]{4,7})(?:-([0-9]{1,2})(?:-([0-9]{1,2})(?:[ T].*)?)?)?[\\s\\p{Cc}]*$")


# Databricks SQL (ANSI on) stops on text that isn't a date, where Spark with ANSI off gives NULL.
_DATE_MALFORMED = ("'[CAST_INVALID_INPUT] The value ''' || LEFT(CAST(kf_a.t AS VARCHAR), 200) || ''' of the type "
                   "\"STRING\" cannot be cast to \"DATE\" because it is malformed. Correct the value as per the "
                   "syntax, or change its target type. Use try_cast to tolerate malformed input and return NULL "
                   "instead.'")


def text_date(value: exp.Expression) -> exp.Expression:
    """to_date(value), or Spark's CAST(value AS DATE), as Spark gives it (spark_text_date). In Databricks SQL
    (ANSI on) text that isn't a date stops with CAST_INVALID_INPUT, as Databricks does."""
    if not _ANSI.get():
        return _with_args(spark_text_date("fj_arg0"), [value])
    return _with_args(f"(LIST_TRANSFORM([{{'t': fj_arg0, 'd': {spark_text_date('fj_arg0')}}}], kf_a -> "
                      f"CASE WHEN kf_a.t IS NOT NULL AND kf_a.d IS NULL THEN CAST(ERROR({_DATE_MALFORMED}) AS DATE) "
                      "ELSE kf_a.d END))[1]", [value])


def spark_text_date(value: str) -> str:
    """DuckDB SQL for to_date(value) as Spark's: text read by Spark's rules (_TEXT_DATE), anything else cast."""
    parts = f"REGEXP_EXTRACT(CAST({value} AS VARCHAR), '{_TEXT_DATE}', ['s', 'y', 'm', 'd'])"
    date = ("CASE WHEN kf_dt.y = '' THEN NULL ELSE TRY(MAKE_DATE(CAST(kf_dt.s || kf_dt.y AS BIGINT), "
            "CAST(COALESCE(NULLIF(kf_dt.m, ''), '1') AS BIGINT), CAST(COALESCE(NULLIF(kf_dt.d, ''), '1') AS BIGINT))) END")
    return (f"CASE WHEN TYPEOF({value}) = 'VARCHAR' THEN (LIST_TRANSFORM([{parts}], kf_dt -> {date}))[1] "
            f"ELSE TRY_CAST({value} AS DATE) END")


def _to_date_with_format(node: exp.Expression, args: list[exp.Expression]) -> exp.Expression | None:
    """to_date(text, format) reads the text with the format, also when the format is sqlglot's
    default ('yyyy-MM-dd HH:mm:ss', 'yyyy-MM-dd'), which sqlglot otherwise writes as a plain cast
    that ignores it: Spark gives NULL for '2026-03-02' with 'yyyy-MM-dd HH:mm:ss'. The pattern is read
    as Spark reads it (spark_datetime) when it was written out."""
    from . import spark_datetime
    fmt = node.args.get("format")
    if fmt is None:
        return text_date(node.this)
    if not fmt.is_string:
        return None
    pattern = written_pattern(node)
    if pattern is not None:
        read = spark_datetime.parse_sql(pattern, "to_date", strict=_ANSI.get())
        return _with_args(f"CAST({read} AS DATE)", [node.this])
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
    if not isinstance(value, exp.Literal) and (value.type is None or value.is_type(exp.DataType.Type.UNKNOWN)) \
            and re.fullmatch(r"[A-Z_]+", part):
        # A column's type isn't known here: text is read as a timestamp, as Spark casts it, and an interval
        # keeps its own fields. TYPEOF is fixed when DuckDB plans the query, so only one branch runs.
        return _with_args(f"CAST(CASE WHEN TYPEOF(fj_arg0) = 'INTERVAL' THEN EXTRACT({part} FROM TRY_CAST(fj_arg0 AS INTERVAL)) "
                          f"ELSE EXTRACT({part} FROM {ts}) END AS INTEGER)", [value])
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
    """any(col) as an aggregate (not ``x = ANY (subquery)`` or ``s LIKE ANY ('a%', 'b%')``). Spark's quantifier
    takes a subquery, or a list after LIKE, so ``any(x) = true`` and ``true = any(x)`` are the aggregate."""
    if isinstance(node.parent, (exp.Like, exp.ILike)) and node.arg_key != "this":
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


def rewrite(statement: exp.Expression, ansi: bool = False) -> exp.Expression:
    """Every marked call above in ``statement``, deepest first, as DuckDB SQL that gives Spark's value.
    ``ansi``: Databricks SQL, where text that doesn't match a datetime pattern is an error, not NULL."""
    token = _ANSI.set(ansi)
    try:
        return _rewrite(statement)
    finally:
        _ANSI.reset(token)


_ANSI: contextvars.ContextVar[bool] = contextvars.ContextVar("kf_spark_ansi", default=False)


def _rewrite(statement: exp.Expression) -> exp.Expression:
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
    _lateral_rows(statement)
    _no_duckdb_hash(statement)
    return statement


def _lateral_rows(statement: exp.Expression) -> None:
    """stack, json_tuple and inline give rows of structs, which UNNEST(..., max_depth := 2) spreads
    into columns. After LATERAL VIEW, DuckDB's UNNEST leaves out max_depth, so each row would be one
    struct column; written as inline, it becomes a CROSS JOIN LATERAL subquery that keeps it."""
    for lateral in list(statement.find_all(exp.Lateral)):
        rows = lateral.this
        if (lateral.args.get("view") and type(rows) is exp.Explode and len(rows.expressions) == 1
                and isinstance(rows.expressions[0], exp.PropertyEQ) and rows.expressions[0].name == "max_depth"):
            rows.replace(exp.Inline(this=rows.this))


def _no_duckdb_hash(statement: exp.Expression) -> None:
    """A hash or xxhash64 still here lost its mark on the way (another rewrite wrote it out as text):
    DuckDB would answer with its own hash, another number, so the cell stops instead."""
    for call in statement.find_all(exp.Anonymous):
        if call.name.upper() in ("HASH", "XXHASH64"):
            name = call.name.lower()
            raise ValueError(f"{name}() can't run on this PC the way it runs in Fabric, because written this way "
                             "it would give a different number from Spark's, so nothing ran. Compute the hash in a "
                             "column of its own first, or run the cell in Fabric.")


def _truncated_time(node: exp.Expression) -> None:
    """trunc(x, unit) reads x as a date and date_trunc(unit, 'text') reads text as a timestamp (with
    time zone, in the session's zone), as Spark does (text that isn't one gives NULL). date_trunc of anything else is left as it is, so a
    timestamp keeps its type."""
    value = node.this
    if isinstance(node, exp.TimestampTrunc):
        text = value.is_string or (isinstance(value, (exp.Cast, exp.TryCast)) and value.to.is_type(*exp.DataType.TEXT_TYPES))
        if not text:
            return
        target = exp.DataType.Type.TIMESTAMPTZ  # Spark's timestamp, read in the session's zone
    else:
        target = exp.DataType.Type.DATE
    if isinstance(value, (exp.Cast, exp.TryCast)) and value.to.this == target:
        return
    node.set("this", exp.TryCast(this=value.copy(), to=exp.DataType.build(target)))
