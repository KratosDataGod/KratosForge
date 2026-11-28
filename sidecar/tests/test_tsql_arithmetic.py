"""WR-211: local T-SQL arithmetic and implicit conversion give a Fabric Warehouse's answers at their edges.

This file checks:
- integer division and modulo with negatives, and overflow on int and bigint;
- text that meets a number ('1' + 1);
- ISNULL against COALESCE result types;
- CAST and TRY_CAST failures;
- division and modulo by zero;
- ROUND with negative lengths and in truncate mode.

Each value is what T-SQL's reference gives. A remaining difference is listed in sql-compatibility.md.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("arith") / "app" / "local-data")
    e = local.engine(local.create("Arith")["id"])
    e.execute("CREATE TABLE dbo.t AS SELECT CAST(7 AS INTEGER) AS i, CAST(0 AS INTEGER) AS z, CAST(NULL AS INTEGER) AS ni, "
              "CAST(NULL AS VARCHAR) AS nv, CAST(2.5 AS DOUBLE) AS f, CAST(748.58 AS DECIMAL(5, 2)) AS d")
    return e


def run(engine, sql):
    r = engine.execute(f"SELECT {sql} AS v FROM dbo.t", dialect="tsql")
    got = r["rows"][0][0]
    return (None if got is None else str(got)), (r.get("translation") or {}).get("notes") or []


# (T-SQL expression on dbo.t, T-SQL's answer as text; None for NULL; "ERROR:<words>" for an error that says them)
CASES = [
 # integer division and modulo: toward zero, and the remainder takes the dividend's sign
 ("7 / 2", "3"), ("-7 / 2", "-3"), ("7 / -2", "-3"), ("-7 / -2", "3"), ("i / 2", "3"), ("-i / 2", "-3"),
 ("7 % 2", "1"), ("-7 % 2", "-1"), ("7 % -2", "1"), ("-7 % -2", "-1"), ("-i % 3", "-1"),
 # overflow: an error, as in T-SQL (in SQL Server's words where they're certain, WR-247)
 ("CAST(2147483647 AS INT) + 1", "ERROR:Arithmetic overflow error converting expression to data type int."),
 ("2147483647 * 2", "ERROR:Arithmetic overflow error converting expression to data type int."),
 ("CAST(9223372036854775807 AS BIGINT) + 1", "ERROR:Arithmetic overflow error converting expression to data type bigint."),
 ("CAST(-2147483648 AS INT) / -1", "ERROR:Arithmetic overflow error converting expression to data type int."),
 ("ABS(CAST(-2147483648 AS INT))", "ERROR:Arithmetic overflow error converting expression to data type int."), ("CAST(2147483647 AS BIGINT) + 1", "2147483648"),
 ("CAST(300 AS TINYINT)", "ERROR:"), ("CAST(-1 AS TINYINT)", "ERROR:"), ("CAST(255 AS TINYINT)", "255"),
 # text meets a number: the number's type wins
 ("'1' + 1", "2"), ("1 + '1'", "2"), ("'' + 1", "1"), ("' 2 ' + 1", "3"), ("'1' + 1.5", "2.5"),
 ("'1.5' + 1", "ERROR:Conversion failed when converting the varchar value '1.5' to data type int."), ("'x' + 1", "ERROR:Conversion failed"),
 ("'1e2' + 1", "ERROR:Conversion failed"), ("'a' + 'b'", "ab"),
 # ISNULL takes the first argument's type, COALESCE the highest-ranked one
 ("ISNULL(CAST(NULL AS VARCHAR(2)), 'abcd')", "ab"), ("COALESCE(CAST(NULL AS VARCHAR(2)), 'abcd')", "abcd"),
 ("ISNULL(ni, 2.7)", "2"), ("ISNULL(ni, -2.7)", "-2"), ("COALESCE(ni, 2.7)", "2.7"),
 ("ISNULL(CAST(NULL AS INT), 2.7)", "2"), ("ISNULL(ni, '5')", "5"), ("ISNULL(ni, 'x')", "ERROR:Conversion failed"),
 ("ISNULL(CAST(NULL AS DECIMAL(5, 1)), 2.76)", "2.8"), ("ISNULL(i, 2.7)", "7"), ("ISNULL(nv, 'abc')", "abc"),
 # CAST and TRY_CAST: an error, or NULL; a decimal to an integer is cut toward zero, a decimal to a decimal rounds
 ("CAST('abc' AS INT)", "ERROR:Conversion failed"), ("TRY_CAST('abc' AS INT)", None),
 ("CAST('1.5' AS INT)", "ERROR:Conversion failed"), ("TRY_CAST('1.5' AS INT)", None),
 ("CAST(1.5 AS INT)", "1"), ("CAST(-1.5 AS INT)", "-1"), ("CAST(f AS INT)", "2"),
 ("CAST(2.5 AS DECIMAL(5,0))", "3"), ("CAST(-2.5 AS DECIMAL(5,0))", "-3"), ("CAST(123.456 AS DECIMAL(4,1))", "123.5"),
 ("CAST(12345.6 AS DECIMAL(4,1))", "ERROR:"), ("TRY_CAST(12345.6 AS DECIMAL(4,1))", None),
 ("TRY_CAST(300 AS TINYINT)", None), ("TRY_CAST('2024-02-30' AS DATE)", None),
 # dividing by zero is an error, for integers, decimals and floats, and for %
 ("1 / 0", "ERROR:Divide by zero error encountered."), ("i / z", "ERROR:Divide by zero error encountered."),
 ("1.0 / 0", "ERROR:Divide by zero error encountered."), ("f / z", "ERROR:Divide by zero error encountered."),
 ("CAST(1 AS FLOAT) / 0", "ERROR:Divide by zero error encountered."), ("i % z", "ERROR:Divide by zero error encountered."),
 ("1 % 0", "ERROR:Divide by zero error encountered."), ("ni / z", None), ("i / ni", None),
 ("CAST(NULL AS DECIMAL(5, 2)) / z", None), ("ni % z", None),
 # ROUND keeps its value's type: Microsoft Learn's ROUND examples
 ("ROUND(123.9994, 3)", "123.9990"), ("ROUND(123.9995, 3)", "124.0000"),
 ("ROUND(123.4545, 2)", "123.4500"), ("ROUND(123.45, -2)", "100.00"),
 ("ROUND(150.75, 0)", "151.00"), ("ROUND(150.75, 0, 1)", "150.00"),
 ("ROUND(748.58, -1)", "750.00"), ("ROUND(748.58, -2)", "700.00"),
 ("ROUND(748.58, -3)", "ERROR:Arithmetic overflow error converting expression to data type numeric."),
 ("ROUND(748.58, -4)", "0.00"), ("ROUND(d, -2)", "700.00"),
 ("ROUND(999.5, 0)", "ERROR:Arithmetic overflow error converting expression to data type numeric."),
 ("ROUND(-2.5, 0)", "-3.0"), ("ROUND(1234.567, -2)", "1200.000"), ("ROUND(-1234.567, -2, 1)", "-1200.000"),
 ("ROUND(1250, -2)", "1300"), ("ROUND(-1250, -2)", "-1300"), ("ROUND(i, -1)", "10"),
 ("ROUND(CAST(150 AS TINYINT), -2)", "200"),
 ("ROUND(CAST(250 AS TINYINT), -2)", "ERROR:Arithmetic overflow error converting expression to data type tinyint."),
 ("ROUND(f, 0)", "3.0"),
 ("ROUND(2147483647, -1)", "ERROR:Arithmetic overflow error converting expression to data type int."),
 ("ROUND(CAST(32767 AS SMALLINT), -1)", "ERROR:Arithmetic overflow error converting expression to data type smallint."),
 ("ROUND(CAST(9223372036854775807 AS BIGINT), -1)",
  "ERROR:Arithmetic overflow error converting expression to data type bigint."),
 ("ROUND(CAST(255 AS TINYINT), -1)", "ERROR:Arithmetic overflow error converting expression to data type tinyint."),
 ("ROUND(2147483644, -1)", "2147483640"), ("ROUND(CAST(32764 AS SMALLINT), -1)", "32760"),
 ("ROUND(CAST(9223372036854775804 AS BIGINT), -1)", "9223372036854775800"), ("ROUND(-2147483648, -1)", "-2147483650"),
]

VALUES = [c for c in CASES if c[1] is None or not c[1].startswith("ERROR:")]
ERRORS = [c for c in CASES if c[1] is not None and c[1].startswith("ERROR:")]


@pytest.mark.parametrize(("sql", "want"), VALUES, ids=[c[0] for c in VALUES])
def test_each_case_gives_tsqls_answer(engine, sql, want):
    got, notes = run(engine, sql)
    assert got == want
    assert notes == [], "a checked case runs without a caveat"


@pytest.mark.parametrize(("sql", "want"), ERRORS, ids=[c[0] for c in ERRORS])
def test_each_error_case_stops_as_tsql_does(engine, sql, want):
    with pytest.raises(Exception, match=want[len("ERROR:"):].replace(".", r"\.").replace("(", r"\(") or "."):
        run(engine, sql)


def test_a_divisor_written_as_a_number_other_than_zero_is_left_alone():
    from fabric_desktop.localdata.tsql import translate
    texts, _ = translate("SELECT a / 2, a % 3 FROM dbo.t", {"dbo": {"t": {"a": "INTEGER"}}})
    assert texts == ["SELECT a // 2, a % 3 FROM dbo.t"]
