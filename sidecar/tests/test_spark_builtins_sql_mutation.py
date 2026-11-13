"""Spark SQL built-ins that mutation testing (batch 8, docs/quality/mutation-testing.md) showed were
under-tested in spark_builtins_sql: split's limit, replace without a replacement, format_string's
refusals and two's complement, signum's type, stack, try_to_binary, hash() with no argument and
Spark's int literal, extract from text. Each expected value is what Apache Spark 3.5 gives."""

from __future__ import annotations

import pytest

from fabric_desktop import sql


def one(query: str):
    return next(iter(sql.run(query).to_pylist()[0].values()))


# ---- split(str, regex, limit)

@pytest.mark.parametrize(("query", "expected"), [
    ("SELECT split('a,b,c', ',', 2)", ["a", "b,c"]),
    ("SELECT split('a,b,c', ',', 1)", ["a,b,c"]),
    ("SELECT split('a,b,c', ',', 3)", ["a", "b", "c"]),
    ("SELECT split('a,b,c', ',', 0)", ["a", "b", "c"]),
    ("SELECT split('a,b,c', ',', -1)", ["a", "b", "c"]),
    ("SELECT split('a1b22c', '[0-9]+', 2)", ["a", "b22c"]),
    # Spark's empty pattern: one character each, at most limit of them (UTF8String.split)
    ("SELECT split('abc', '', 1)", ["a"]),
    ("SELECT split('abc', '', 2)", ["a", "b"]),
    ("SELECT split('abc', '', 0)", ["a", "b", "c"]),
    ("SELECT split('abc', '', -1)", ["a", "b", "c"]),
    ("SELECT split(NULL, ',', 2)", None),
])
def test_split_with_a_limit(query, expected):
    assert one(query) == expected


@pytest.mark.parametrize("limit", ["x", "-x"])
def test_split_refuses_a_limit_from_the_data(limit):
    with pytest.raises(ValueError, match="limit worked out from the data"):
        sql.run(f"SELECT split('a,b,c', ',', {limit}) FROM VALUES (2) t(x)")


# ---- replace(str, search)

def test_replace_without_a_replacement_drops_the_search():
    assert one("SELECT replace('abcb', 'b')") == "ac"
    assert one("SELECT replace('abcb', 'b', 'x')") == "axcx"
    assert one("SELECT replace(123, '2')") == "13"  # the number as text, as Spark casts it


# ---- format_string / printf

@pytest.mark.parametrize(("query", "expected"), [
    ("SELECT format_string('%s', NULL)", "null"),
    ("SELECT format_string('%5s|', NULL)", " null|"),
    ("SELECT format_string('%d', -5)", "-5"),
    ("SELECT format_string('%-+d|', 5)", "+5|"),
    ("SELECT format_string('%x', 255)", "ff"),
    ("SELECT format_string('%x', -1)", "ffffffff"),
    ("SELECT format_string('%X', -1)", "FFFFFFFF"),
    ("SELECT format_string('%x', -1L)", "ffffffffffffffff"),
    ("SELECT format_string('%x', CAST(-1 AS TINYINT))", "ff"),
    ("SELECT format_string('%x', CAST(-2 AS TINYINT))", "fe"),
    ("SELECT format_string('%o', CAST(-1 AS SMALLINT))", "177777"),
    ("SELECT format_string('%2$s %1$s', 'a', 'b')", "b a"),
    ("SELECT format_string('%1$s', 'a')", "a"),
])
def test_format_string_as_java_writes_it(query, expected):
    assert one(query) == expected


@pytest.mark.parametrize(("fmt", "message"), [
    ("%#d", "has a flag this PC doesn't write"),
    ("%.3d", "isn't a conversion this PC writes"),
    ("%q", "isn't a conversion this PC writes"),
])
def test_format_string_refuses_what_it_cant_write_as_java(fmt, message):
    with pytest.raises(ValueError, match=message):
        sql.run(f"SELECT format_string('{fmt}', 1)")


@pytest.mark.parametrize("query", [
    "SELECT format_string('%s %s', 'a')",
    "SELECT format_string('%3$s', 1, 2)",
    "SELECT format_string('%0$s', 1)",
])
def test_format_string_with_too_few_arguments_is_refused(query):
    with pytest.raises(ValueError, match="has no argument"):
        sql.run(query)


# ---- signum, stack, try_to_binary, hash

def test_signum_is_a_double():
    assert one("SELECT typeof(signum(-4))") == "double"
    assert one("SELECT signum(-4)") == -1.0


def test_stack_of_one_value():
    assert sql.run("SELECT stack(1, 5)").to_pylist() == [{"col0": 5}]
    assert sql.run("SELECT stack(2, 1, 2, 3)").to_pylist() == [{"col0": 1, "col1": 2}, {"col0": 3, "col1": None}]


def test_stack_of_no_rows_is_not_rewritten():
    with pytest.raises(Exception, match="(?i)stack"):
        sql.run("SELECT stack(0, 1, 2)")


@pytest.mark.parametrize(("query", "expected"), [
    ("SELECT try_to_binary('6162')", b"ab"),
    ("SELECT try_to_binary('6162', 'hex')", b"ab"),
    ("SELECT try_to_binary('abc', 'utf-8')", b"abc"),
    ("SELECT try_to_binary('YWI=', 'base64')", b"ab"),
    ("SELECT try_to_binary('zz')", None),
])
def test_try_to_binary(query, expected):
    assert one(query) == expected


@pytest.mark.parametrize("name", ["hash", "xxhash64"])
def test_hash_of_nothing_is_refused(name):
    with pytest.raises(ValueError, match=rf"{name}\(\) needs at least one argument"):
        sql.run(f"SELECT {name}()")


def test_hash_of_the_smallest_int_hashes_an_int():
    # -2147483648 is an int to Spark: its hash is the int's, not the bigint's
    assert one("SELECT hash(-2147483648)") == one("SELECT hash(CAST(-2147483648 AS INT))")
    assert one("SELECT hash(-2147483648)") != one("SELECT hash(CAST(-2147483648 AS BIGINT))")
    assert one("SELECT hash('a')") == one("SELECT hash(CAST('a' AS STRING))")


# ---- extract from text, from_utc_timestamp of a timestamp

def test_extract_reads_text_as_a_timestamp():
    assert one("SELECT extract(hour FROM '2009-07-30 12:58:59')") == 12
    assert one("SELECT extract(hour FROM CAST(s AS STRING)) FROM VALUES ('2009-07-30 12:58:59') t(s)") == 12
    assert one("SELECT extract(hour FROM CAST('2016-08-31 03:00:00' AS TIMESTAMP))") == 3


def test_from_utc_timestamp_of_a_timestamp():
    import datetime as dt
    assert one("SELECT from_utc_timestamp(CAST('2016-08-31 00:00:00' AS TIMESTAMP), 'Asia/Seoul')") == \
        dt.datetime(2016, 8, 31, 9, 0)


# ---- a format or argument from the data, and calls Spark refuses

def test_format_string_with_a_format_from_the_data():
    assert one("SELECT format_string(f, 1) FROM VALUES ('%d') t(f)") == "1"


def test_try_to_binary_of_a_column():
    assert one("SELECT try_to_binary(s, 'utf-8') FROM VALUES ('abc') t(s)") == b"abc"
    assert one("SELECT try_to_binary(s) FROM VALUES ('6162') t(s)") == b"ab"


def test_to_char_refuses_a_format_that_is_a_number():
    with pytest.raises(ValueError, match="must be written out as text"):
        sql.run("SELECT to_char(123, 999)")


# Spark refuses each of these (AnalysisException); here DuckDB stops them, with the function's name
@pytest.mark.parametrize(("query", "name"), [
    ("SELECT to_csv(1)", "to_csv"),
    ("SELECT to_csv(map('a', 1))", "to_csv"),
    ("SELECT map_zip_with(map(1, 'a'), map(1, 'b'), (k, v) -> v)", "map_zip_with"),
    ("SELECT extract(year FROM 2020)", "date_part"),
])
def test_calls_spark_refuses_still_stop(query, name):
    with pytest.raises(Exception, match=f"(?i){name}"):
        sql.run(query)
