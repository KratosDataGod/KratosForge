"""Local T-SQL where DuckDB stopped with its own binder error and T-SQL answers or gives its own error.

No warehouse runs in CI: each expected answer is T-SQL's documented rule, quoted beside it
(https://learn.microsoft.com/en-us/sql/t-sql/...).
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("tsql-fractions") / "app" / "local-data")
    return local.engine(local.create("Fractions")["id"])


def one(engine, expression: str) -> object:
    return engine.execute(f"SELECT {expression} AS v", dialect="tsql")["rows"][0][0]


# functions/cast-and-convert-transact-sql, "Truncating and rounding results": decimal, numeric and float to an
# integer type truncate. SUBSTRING's start and length and CHARINDEX's start are bigint, so 2.7 is 2 and -0.5 is 0.
@pytest.mark.parametrize(("expression", "want"), [
    ("SUBSTRING('abcdef', 2.7, 2.9)", "bc"),
    ("SUBSTRING('abcdef', CAST(2.7 AS FLOAT), 2)", "bc"),
    ("SUBSTRING('abcdef', -0.5, 2)", "a"),
    ("SUBSTRING('abcdef', 2, -0.5)", ""),
    ("SUBSTRING('abcdef', 1e0, 2)", "ab"),
    ("(SELECT SUBSTRING('abcdef', c, d) FROM (VALUES (2.7, 2.9)) AS t(c, d))", "bc"),
    ("(SELECT SUBSTRING('abcdef', c, d) FROM (VALUES (-1.5, 3.9)) AS t(c, d))", "a"),
    ("(SELECT SUBSTRING('abcdef', c, 2) FROM (VALUES (CAST(NULL AS FLOAT))) AS t(c))", None),
    ("CHARINDEX('c', 'abcabc', 3.9)", 3),
    ("CHARINDEX('c', 'abcabc', 4.2)", 6),
    ("CHARINDEX('a', 'abcabc', 0.5)", 1),
    ("(SELECT CHARINDEX('c', 'abcabc', c) FROM (VALUES (3.9)) AS t(c))", 3),
    ("(SELECT CHARINDEX('c', 'abcabc', c) FROM (VALUES (CAST(NULL AS FLOAT))) AS t(c))", None),
    ("LEFT('abcdef', 2.9)", "ab"),
    ("RIGHT('abcdef', CAST(2.9 AS FLOAT))", "ef"),
    ("(SELECT LEFT('abcdef', c) FROM (VALUES (CAST(2.9 AS FLOAT))) AS t(c))", "ab"),
])
def test_a_start_or_length_with_a_fraction_is_cut_toward_zero(engine, expression: str, want: object) -> None:
    assert one(engine, expression) == want


def test_a_start_past_a_bigint_is_the_overflow_error(engine) -> None:
    with pytest.raises(ValueError, match="Arithmetic overflow error converting expression to data type bigint"):
        one(engine, "SUBSTRING('abc', 1e30, 2)")


@pytest.mark.parametrize(("expression", "part", "kind"), [
    # functions/datetrunc-transact-sql: "to truncate to the millisecond datepart, the fractional time scale must be at
    # least 3 ... microsecond ... at least 6", with Msg 9810 "The datepart microsecond is not supported by date
    # function datetrunc for data type datetime2." datetime keeps three digits, smalldatetime none.
    ("DATETRUNC(microsecond, CAST('2021-12-12 12:12:12.12345' AS DATETIME2(3)))", "microsecond", "datetime2"),
    ("DATETRUNC(millisecond, CAST('2021-12-12 12:12:12.12' AS DATETIME2(2)))", "millisecond", "datetime2"),
    ("DATETRUNC(microsecond, CAST('2021-12-12 12:12:12.123' AS DATETIME))", "microsecond", "datetime"),
    ("DATETRUNC(millisecond, CAST('2021-12-12 12:12:12' AS SMALLDATETIME))", "millisecond", "smalldatetime"),
    ("DATETRUNC(millisecond, CAST('12:12:12' AS TIME(0)))", "millisecond", "time"),
    # "The datepart year is not supported by date function datetrunc for data type time."
    ("DATETRUNC(year, CAST('12:12:12.1234567' AS TIME))", "year", "time"),
    ("DATETRUNC(hour, CAST('2024-01-01' AS DATE))", "hour", "date"),
])
def test_a_datepart_the_type_cant_hold_is_tsqls_error(engine, expression: str, part: str, kind: str) -> None:
    with pytest.raises(ValueError, match=f"The datepart {part} is not supported by date function datetrunc for data "
                                         f"type {kind}. T-SQL gives the same error."):
        one(engine, expression)


@pytest.mark.parametrize(("expression", "want"), [
    # The same page's examples: truncating to the type's own precision changes nothing; smalldatetime is precise to
    # the minute, so second leaves it as it is.
    ("DATETRUNC(millisecond, CAST('2015-04-29 05:06:07.123' AS DATETIME))", "2015-04-29 05:06:07.123000"),
    ("DATETRUNC(second, CAST('2009-09-11 12:42:12' AS SMALLDATETIME))", "2009-09-11 12:42:00"),
    ("DATETRUNC(millisecond, CAST('2021-12-12 12:12:12.12345' AS DATETIME2(3)))", "2021-12-12 12:12:12.123000"),
    # a time is truncated as a time
    ("DATETRUNC(minute, CAST('10:11:12.5' AS TIME(0)))", "10:11:00"),
    ("DATETRUNC(millisecond, CAST('10:11:12.5678' AS TIME))", "10:11:12.567000"),
    # Example C: text "that can resolve to a datetime2(7)", written in the query or held in a column
    ("DATETRUNC(month, '1998-03-04')", "1998-03-01 00:00:00"),
    ("(SELECT DATETRUNC(minute, c) FROM (VALUES ('1998-03-04 10:10:05')) AS t(c))", "1998-03-04 10:10:00"),
    ("(SELECT DATETRUNC(millisecond, c) FROM (VALUES (N'1998-03-04 10:10:05.1234567')) AS t(c))",
     "1998-03-04 10:10:05.123000"),
])
def test_datetrunc_answers_by_the_type_of_its_value(engine, expression: str, want: str) -> None:
    assert str(one(engine, expression)) == want


@pytest.mark.parametrize("expression", [
    # functions/regexp-instr-transact-sql: "If the value contains a character other than those listed ... Invalid
    # flag provided. '<invalid character>' are not valid flags. Only {c,i,s,m} flags are valid."
    "REGEXP_LIKE('abc', 'a b c', 'x')", "REGEXP_REPLACE('abc', 'b', 'x', 1, 0, 'ix')",
    "REGEXP_COUNT('abc', 'b', 1, 'x')", "REGEXP_INSTR('abc', 'b', 1, 1, 0, 'x')",
])
def test_a_flag_tsql_doesnt_take_is_its_error(engine, expression: str) -> None:
    with pytest.raises(ValueError, match=r"Invalid flag provided\. 'x' are not valid flags\. Only \{c,i,s,m\} flags "
                                         r"are valid\. T-SQL gives the same error\."):
        one(engine, expression)


def test_regexp_instr_return_option_and_group(engine) -> None:
    # return_option: "Use 0 for the beginning, and 1 for the end ... The query returns error for any other value."
    assert int(one(engine, "REGEXP_INSTR('abcabc', 'b', 1, 1, 1)")) == 3
    assert int(one(engine, "REGEXP_INSTR('abcabc', 'b(c)', 1, 1, 0, 'i', 0)")) == 2
    with pytest.raises(ValueError, match="return_option must be 0 .* or 1"):
        one(engine, "REGEXP_INSTR('abcabc', 'b', 1, 1, 2)")
    with pytest.raises(ValueError, match="worked out from a column .* so nothing ran. Write 0"):
        one(engine, "(SELECT REGEXP_INSTR('abcabc', 'b', 1, 1, c) FROM (VALUES (1)) AS t(c))")
    # group: sqlglot dropped it, so the whole match's position came back (2, where group 1 is at 3)
    with pytest.raises(ValueError, match="group argument other than 0 isn't mapped yet, so nothing ran"):
        one(engine, "REGEXP_INSTR('abcabc', 'b(c)', 1, 1, 0, 'i', 1)")


def test_nested_fraction_starts_grow_the_sql_linearly() -> None:
    sizes = []
    for depth in range(1, 6):
        expression = "x"
        for _ in range(depth):
            expression = f"SUBSTRING({expression}, CHARINDEX('a', {expression[:1]}, y), y)"
        sizes.append(len(tsql.translate(f"SELECT {expression} FROM (VALUES ('abc', 1.5)) AS t(x, y)")[0][-1]))
    steps = [b - a for a, b in zip(sizes, sizes[1:], strict=False)]
    assert max(steps) <= 1.2 * min(steps), sizes


def test_a_computed_start_is_read_once() -> None:
    assert tsql.translate("SELECT SUBSTRING('abc', RAND() * 3, RAND() * 2)")[0][-1].upper().count("RANDOM()") == 2
    assert tsql.translate("SELECT CHARINDEX('a', 'abc', RAND() * 3)")[0][-1].upper().count("RANDOM()") == 1
