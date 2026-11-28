"""WR-281: local T-SQL's window functions, aggregates, GROUP BY, TOP and OFFSET/FETCH give T-SQL's answer or its error.

T-SQL's reference (the Microsoft Learn page named on each case) is the oracle. Before this, DuckDB gave an answer
where T-SQL stops (MIN of a bit, RANK() OVER () with no ORDER BY, COUNT(DISTINCT x) OVER (), LAG(x, -1) read as
LEAD(x, 1), PERCENTILE_CONT(-0.5), FETCH NEXT 0 ROWS), a different answer (LAG(int, 1, 2.5) as 3, TOP (2.7) as 3
rows, STRING_AGG of a float as 3.0 and of a bit as true), or its own error (AVG of a bit, SUM of text, NTILE(0),
PERCENTILE_CONT(1.5), GROUP BY ... WITH ROLLUP, TOP (-1), OFFSET -1 ROWS).
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql

DOCS = "https://learn.microsoft.com/en-us/sql/t-sql/"


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("tsql-windows-aggregates") / "app" / "local-data")
    e = local.engine(local.create("Windows")["id"])
    e.execute("CREATE TABLE dbo.t (id INT, x INT, g VARCHAR(5), s VARCHAR(10), b BIT, f FLOAT)", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES (1, 10, 'a', 'p', 1, 1.5), (2, NULL, 'a', NULL, 0, NULL), "
              "(3, 20, 'b', 'r', 1, 2.5), (4, 10, 'b', 'q', NULL, 3.0), (5, NULL, 'b', 's', 0, 4.0)", dialect="tsql")
    return e


def _rows(engine, sql: str) -> list[list[object]]:
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


@pytest.mark.parametrize(("sql", "want"), [
    # functions/ntile-transact-sql: "Larger groups come before smaller groups"; more groups than rows is one each
    (("SELECT NTILE(2) OVER (ORDER BY id), NTILE(4) OVER (ORDER BY id), NTILE(7) OVER (ORDER BY id) FROM dbo.t "
     "ORDER BY id"), [[1, 1, 1], [1, 1, 2], [1, 2, 3], [2, 3, 4], [2, 4, 5]]),
    # functions/lag-transact-sql: "Return types: The data type of the specified scalar_expression", so the
    # default is converted to an int as T-SQL converts a decimal or float: toward zero
    ("SELECT LAG(x, 1, 2.5) OVER (ORDER BY id) FROM dbo.t ORDER BY id", [[2], [10], [None], [20], [10]]),
    ("SELECT LAG(id, 1, -1.9) OVER (ORDER BY id) FROM dbo.t ORDER BY id", [[-1], [1], [2], [3], [4]]),
    ("SELECT LAG(x, 1, f) OVER (ORDER BY id) FROM dbo.t ORDER BY id", [[1], [10], [None], [20], [10]]),
    ("SELECT LEAD(f, 1, 7) OVER (ORDER BY id) FROM dbo.t ORDER BY id", [[None], [2.5], [3.0], [4.0], [7.0]]),
    # lag-transact-sql: the offset "can be implicitly converted to bigint": 1.7 is 1; 0 is the row itself
    ("SELECT LAG(x, 1.7) OVER (ORDER BY id) FROM dbo.t ORDER BY id", [[None], [10], [None], [20], [10]]),
    ("SELECT LAG(x, 0) OVER (ORDER BY id) FROM dbo.t ORDER BY id", [[10], [None], [20], [10], [None]]),
    ("SELECT LAG(x, CAST(id AS FLOAT) / 2) OVER (ORDER BY id) FROM dbo.t ORDER BY id",
     [[10], [10], [None], [None], [20]]),
    ("SELECT LAG(x, id - 1) OVER (ORDER BY id) FROM dbo.t ORDER BY id", [[10], [10], [10], [10], [10]]),
    # functions/string-agg-transact-sql: values "are implicitly converted to string types" as a CAST does:
    # a float in style 0, a bit as 1 or 0; NULLs are left out with their separator
    ("SELECT STRING_AGG(f, ',') WITHIN GROUP (ORDER BY id) FROM dbo.t", [["1.5,2.5,3,4"]]),
    ("SELECT STRING_AGG(b, ',') WITHIN GROUP (ORDER BY id) FROM dbo.t", [["1,0,1,0"]]),
    ("SELECT STRING_AGG(CAST(1.0E7 AS FLOAT), ',')", [["1e+007"]]),
    ("SELECT STRING_AGG(x, '-') FROM dbo.t", [["10-20-10"]]),
    # queries/top-transact-sql: "expression is converted to bigint" without PERCENT
    ("SELECT TOP (2.7) id FROM dbo.t ORDER BY id", [[1], [2]]),
    ("SELECT TOP 0 id FROM dbo.t ORDER BY id", []),
    # The ones that were right already, kept so
    ("SELECT COUNT(DISTINCT x), COUNT(x), COUNT(*), COUNT_BIG(*) FROM dbo.t", [[2, 3, 5, 5]]),
    ("SELECT AVG(x), AVG(DISTINCT x), SUM(DISTINCT x) FROM dbo.t", [[13, 15, 30]]),
    ("SELECT STDEV(x), VAR(x), STDEVP(x), VARP(x) FROM dbo.t WHERE id = 1", [[None, None, 0.0, 0.0]]),
    ("SELECT g, SUM(x), GROUPING(g) FROM dbo.t GROUP BY ROLLUP(g) ORDER BY g", [[None, 40, 1], ["a", 10, 0],
                                                                                 ["b", 30, 0]]),
    ("SELECT COUNT(b), COUNT(DISTINCT b) FROM dbo.t", [[4, 2]]),
    ("SELECT DISTINCT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY b) OVER () FROM dbo.t", [[0.5]]),
])
def test_tsqls_answer(engine, sql: str, want: list[list[object]]) -> None:
    got = _rows(engine, sql)
    assert got == want
    assert [[type(v) for v in r] for r in got] == [[type(v) for v in r] for r in want]


@pytest.mark.parametrize(("sql", "says"), [
    # functions/min-transact-sql: "MIN can be used with numeric, char, ... or datetime columns, but not with bit
    # columns", and max-transact-sql the same; sum- and avg-transact-sql take numbers only (error 8117)
    ("SELECT MIN(b) FROM dbo.t", "Msg 8117, Level 16: Operand data type bit is invalid for min operator."),
    ("SELECT MAX(b) FROM dbo.t", "Msg 8117, Level 16: Operand data type bit is invalid for max operator."),
    ("SELECT SUM(b) FROM dbo.t", "Msg 8117, Level 16: Operand data type bit is invalid for sum operator."),
    ("SELECT AVG(b) FROM dbo.t", "Msg 8117, Level 16: Operand data type bit is invalid for avg operator."),
    ("SELECT MIN(CAST(1 AS BIT))", "Msg 8117, Level 16: Operand data type bit is invalid for min operator."),
    ("SELECT MAX(b) OVER () FROM dbo.t", "Msg 8117, Level 16: Operand data type bit is invalid for max operator."),
    ("SELECT SUM(s) FROM dbo.t", "SUM takes numbers only, as in T-SQL, and here its value is text."),
    ("SELECT AVG(CAST('2024-01-01' AS DATE))", "AVG takes numbers only, as in T-SQL, and here its value is a date"),
    # functions/row-number-transact-sql and each ranking and analytic function's syntax: ORDER BY in OVER
    ("SELECT ROW_NUMBER() OVER () FROM dbo.t",
     "Msg 4112, Level 16: The function 'ROW_NUMBER' must have an OVER clause with ORDER BY."),
    ("SELECT RANK() OVER (PARTITION BY g) FROM dbo.t", "The function 'RANK' must have an OVER clause with ORDER BY."),
    ("SELECT NTILE(2) OVER () FROM dbo.t", "The function 'NTILE' must have an OVER clause with ORDER BY."),
    ("SELECT LAG(x) OVER (PARTITION BY g) FROM dbo.t", "The function 'LAG' must have an OVER clause with ORDER BY."),
    ("SELECT FIRST_VALUE(x) OVER () FROM dbo.t", "The function 'FIRST_VALUE' must have an OVER clause"),
    ("SELECT CUME_DIST() OVER () FROM dbo.t", "The function 'CUME_DIST' must have an OVER clause"),
    # queries/select-over-clause-transact-sql: "You can't use the OVER clause with the DISTINCT aggregations"
    ("SELECT COUNT(DISTINCT x) OVER () FROM dbo.t",
     "Msg 10759, Level 16: Use of DISTINCT is not allowed with the OVER clause."),
    ("SELECT SUM(DISTINCT x) OVER (PARTITION BY g) FROM dbo.t", "Use of DISTINCT is not allowed with the OVER"),
    # functions/lag-transact-sql, lead-transact-sql: "offset cannot be a negative value"
    ("SELECT LAG(x, -1) OVER (ORDER BY id) FROM dbo.t", "The offset of LAG can't be a negative value"),
    ("SELECT LEAD(x, -2, 0) OVER (ORDER BY id) FROM dbo.t", "The offset of LEAD can't be a negative value"),
    ("SELECT LAG(x, 1 - id) OVER (ORDER BY id) FROM dbo.t", "The offset of LAG can't be a negative value"),
    # functions/ntile-transact-sql: "A positive integer expression ... of type int, or bigint"
    ("SELECT NTILE(0) OVER (ORDER BY id) FROM dbo.t", "NTILE takes a positive whole number"),
    ("SELECT NTILE(-2) OVER (ORDER BY id) FROM dbo.t", "NTILE takes a positive whole number"),
    ("SELECT NTILE(2.5) OVER (ORDER BY id) FROM dbo.t", "NTILE takes a positive whole number"),
    # functions/percentile-cont-transact-sql: "The value must range between 0.0 and 1.0", the ORDER BY "must
    # evaluate to an exact or approximate numeric type", and "The ORDER BY clause and <rows or range clause> of
    # the OVER syntax can't be specified"
    ("SELECT DISTINCT PERCENTILE_CONT(-0.5) WITHIN GROUP (ORDER BY f) OVER () FROM dbo.t",
     "The percentile of PERCENTILE_CONT must range between 0.0 and 1.0"),
    ("SELECT DISTINCT PERCENTILE_CONT(1.5) WITHIN GROUP (ORDER BY f) OVER () FROM dbo.t",
     "The percentile of PERCENTILE_CONT must range between 0.0 and 1.0"),
    ("SELECT PERCENTILE_DISC(-0.25) WITHIN GROUP (ORDER BY f) FROM dbo.t",
     "The percentile of PERCENTILE_DISC must range between 0.0 and 1.0"),
    ("SELECT DISTINCT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY s) OVER () FROM dbo.t",
     "PERCENTILE_CONT takes numbers only in its WITHIN GROUP"),
    ("SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f) OVER (PARTITION BY g ORDER BY id) FROM dbo.t",
     "PERCENTILE_CONT's OVER takes only PARTITION BY"),
    # queries/select-group-by-transact-sql: the Fabric Warehouse syntax has ROLLUP (...) and CUBE (...), not WITH
    ("SELECT g, COUNT(*) FROM dbo.t GROUP BY g WITH ROLLUP", "Write GROUP BY ROLLUP (a, b) instead."),
    ("SELECT g, COUNT(*) FROM dbo.t GROUP BY g WITH CUBE", "Write GROUP BY CUBE (a, b) instead."),
    # queries/select-order-by-clause-transact-sql: OFFSET is "greater than or equal to zero", FETCH "greater than
    # or equal to one", and both are part of ORDER BY
    ("SELECT id FROM dbo.t ORDER BY id OFFSET -1 ROWS", "The number of rows in a OFFSET clause has to be 0 or more"),
    ("SELECT id FROM dbo.t ORDER BY id OFFSET 1 ROWS FETCH NEXT 0 ROWS ONLY",
     "The number of rows in a FETCH clause has to be 1 or more"),
    ("SELECT id FROM dbo.t ORDER BY id OFFSET 1.5 ROWS",
     "The number of rows provided for a OFFSET clause must be an integer."),
    ("SELECT id FROM dbo.t OFFSET 1 ROWS", "OFFSET needs an ORDER BY, as in T-SQL."),
    ("SELECT TOP (-1) id FROM dbo.t ORDER BY id", "The number of rows in a TOP clause has to be 0 or more"),
])
def test_tsqls_error(engine, sql: str, says: str) -> None:
    with pytest.raises(Exception) as caught:
        engine.execute(sql, dialect="tsql")
    assert says in str(caught.value)
    assert "Binder Error" not in str(caught.value) and "Parser Error" not in str(caught.value)


@pytest.mark.parametrize(("sql", "says"), [
    ("SELECT LAG(x, NULL) OVER (ORDER BY id) FROM dbo.t", "LAG with a NULL offset isn't mapped yet"),
    ("SELECT NTILE(NULL) OVER (ORDER BY id) FROM dbo.t", "NTILE (NULL) isn't mapped yet"),
    ("SELECT DISTINCT PERCENTILE_CONT(NULL) WITHIN GROUP (ORDER BY f) OVER () FROM dbo.t",
     "PERCENTILE_CONT (NULL) isn't mapped yet"),
    ("SELECT TOP (NULL) id FROM dbo.t ORDER BY id", "TOP (NULL) isn't mapped yet"),
    ("SELECT id FROM dbo.t ORDER BY id OFFSET NULL ROWS", "OFFSET (NULL) isn't mapped yet"),
    ("SELECT g, COUNT(*) FROM dbo.t GROUP BY ALL g", "GROUP BY ALL isn't mapped yet"),
    ("SELECT STRING_AGG(CAST('2024-01-02 03:04:05' AS DATETIME2), ',')",
     "STRING_AGG of a time or a date with a time isn't mapped yet"),
])
def test_refused_by_name(engine, sql: str, says: str) -> None:
    with pytest.raises(ValueError) as caught:
        tsql.translate(sql)
    assert str(caught.value).startswith("This T-SQL can't be run on the local engine: ") and says in str(caught.value)
    with pytest.raises(Exception) as ran:
        engine.execute(sql, dialect="tsql")
    assert says in str(ran.value)


def test_a_computed_lag_offset_is_read_once() -> None:
    texts, _ = tsql.translate("SELECT LAG(x, ABS(CAST(y AS INT) - 1)) OVER (ORDER BY y) FROM (VALUES (1, 2)) AS v(x, y)")
    assert texts[-1].count("ABS(") == 1
