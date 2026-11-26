"""WR-708, WR-709 and WR-710: views named #v, the aggregate PERCENTILE_CONT and PERCENTILE_DISC, and the clauses the
two functions take.

- WR-708: ``CREATE VIEW #v`` made a DuckDB temporary view and ran. SQL Server refuses it with error 4103, severity
  15, '"#v": Temporary views are not allowed.' (Database Engine events and errors 4000 to 4999).
- WR-709: with no ``OVER``, PERCENTILE_CONT and PERCENTILE_DISC are aggregates. SQL Server and Azure SQL Database
  need OVER, but a Fabric Warehouse takes both forms (the Fabric note on functions/percentile-cont-transact-sql and
  percentile-disc-transact-sql, and data-warehouse/analytical-functions), so they run, with T-SQL's values.
- WR-710: one ORDER BY expression in WITHIN GROUP (10751), and no ORDER BY (10758) or frame (10752) in OVER,
  each with severity 15 (Database Engine events and errors 10000 to 10999).

Each value is worked out here from T-SQL's rules: CONT interpolates at row 1 + p * (n - 1), DISC is the first value
whose CUME_DIST is at least p, NULLs are left out, and DESC counts from the largest value.
"""

from __future__ import annotations

import math

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import translate


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("percentiles") / "app" / "local-data")
    e = local.engine(local.create("Percentiles")["id"])
    # x has two NULLs and a tie at 10; m is a decimal with NULLs; g splits the rows in two.
    e.execute("CREATE TABLE dbo.t AS SELECT * FROM (VALUES "
              "(1, 10, 'a', CAST(1.25 AS DECIMAL(5, 2)), DATE '2024-01-01', 'p'), "
              "(2, NULL, 'a', CAST(2.50 AS DECIMAL(5, 2)), DATE '2024-02-01', 'r'), "
              "(3, 20, 'b', CAST(4.00 AS DECIMAL(5, 2)), DATE '2024-03-01', 'q'), "
              "(4, 10, 'b', NULL, NULL, NULL), (5, NULL, 'b', NULL, NULL, 's')) v(id, x, g, m, dt, s)")
    return e


def rows(engine, sql):
    r = engine.execute(sql, dialect="tsql")
    return [[None if v is None else str(v) for v in row] for row in r["rows"]]


# --- WR-709: the aggregate form runs, with T-SQL's values -------------------------------------------------------

AGGREGATES = [
    # CONT interpolates; DISC is a value from the column
    (("SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY id), PERCENTILE_CONT(0.3) WITHIN GROUP (ORDER BY id), "
     "PERCENTILE_DISC(0.3) WITHIN GROUP (ORDER BY id), PERCENTILE_DISC(0.4) WITHIN GROUP (ORDER BY id) FROM dbo.t"),
     [["3.0", "2.2", "2", "2"]]),
    # DESC counts from the largest value
    (("SELECT PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY id DESC), PERCENTILE_DISC(0.3) WITHIN GROUP "
     "(ORDER BY id DESC), PERCENTILE_DISC(0.25) WITHIN GROUP (ORDER BY dt DESC), PERCENTILE_DISC(0.25) WITHIN GROUP "
     "(ORDER BY s DESC) FROM dbo.t"), [["4.0", "4", "2024-03-01", "s"]]),
    # 0 and 1 are the ends, DESC too (DuckDB 1.5's aggregate gave the smallest value for 0 DESC)
    (("SELECT PERCENTILE_DISC(0) WITHIN GROUP (ORDER BY x DESC), PERCENTILE_DISC(1) WITHIN GROUP (ORDER BY x DESC), "
     "PERCENTILE_CONT(0) WITHIN GROUP (ORDER BY id DESC), PERCENTILE_CONT(1.0) WITHIN GROUP (ORDER BY id DESC), "
     "PERCENTILE_DISC(0.0) WITHIN GROUP (ORDER BY dt DESC), PERCENTILE_DISC(0) WITHIN GROUP (ORDER BY id) FROM dbo.t"),
     [["20", "10", "5.0", "1.0", "2024-03-01", "1"]]),
    # NULLs are left out, per group
    (("SELECT RTRIM(g), PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY x), PERCENTILE_DISC(0.5) WITHIN GROUP "
     "(ORDER BY x DESC), PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY x DESC) FROM dbo.t GROUP BY g ORDER BY 1"),
     [["a", "10.0", "10", "10.0"], ["b", "15.0", "20", "12.5"]]),
    # A decimal gives a float: 1.875, not the decimal 1.87
    (("SELECT PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY m), PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY m DESC), "
     "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY m DESC) FROM dbo.t"), [["1.875", "3.25", "2.50"]]),
    # An empty group, or one of NULLs only, is NULL
    (("SELECT COUNT(*), PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY id), PERCENTILE_DISC(0.5) WITHIN GROUP "
     "(ORDER BY id DESC) FROM dbo.t WHERE id > 99"), [["0", None, None]]),
    (("SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY x), PERCENTILE_DISC(0) WITHIN GROUP (ORDER BY x DESC) "
     "FROM dbo.t WHERE x IS NULL"), [[None, None]]),
]


@pytest.mark.parametrize(("sql", "want"), AGGREGATES, ids=[f"aggregate {i}" for i in range(len(AGGREGATES))])
def test_the_aggregate_form_gives_tsqls_values(engine, sql, want):
    assert rows(engine, sql) == want


def test_the_aggregate_percentile_cont_of_a_decimal_is_a_float(engine):
    r = engine.execute("SELECT PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY m) AS v FROM dbo.t", dialect="tsql")
    assert r["rows"] == [[1.875]] and type(r["rows"][0][0]) is float


def test_the_aggregate_percentile_disc_keeps_its_values_type(engine):
    r = engine.execute("SELECT PERCENTILE_DISC(0) WITHIN GROUP (ORDER BY id DESC) AS v FROM dbo.t", dialect="tsql")
    assert r["rows"] == [[5]] and type(r["rows"][0][0]) is int


def _cont(values: list[int], p: float) -> float:
    row = 1 + p * (len(values) - 1)
    low, high = math.floor(row), math.ceil(row)
    return values[low - 1] + (row - low) * (values[high - 1] - values[low - 1])


def _disc(values: list[int], p: float) -> int:
    return values[max(1, math.ceil(p * len(values))) - 1]  # the first whose CUME_DIST (k / n) is at least p


PS = [0, 0.05, 0.1, 0.2, 0.25, 1 / 3, 0.4, 0.5, 0.6, 2 / 3, 0.75, 0.9, 0.95, 1]


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 10])
def test_each_percentile_of_1_to_n_is_tsqls_in_both_forms_and_orders(engine, n):
    values = list(range(1, n + 1))
    columns, want = [], []
    for p in PS:
        for desc, ordered in (("", values), (" DESC", values[::-1])):
            for function, rule in (("PERCENTILE_CONT", _cont), ("PERCENTILE_DISC", _disc)):
                columns.append(f"{function}({p!r}) WITHIN GROUP (ORDER BY value{desc})")
                want.append(float(rule(ordered, p)))
    aggregate = engine.execute(f"SELECT {', '.join(columns)} FROM GENERATE_SERIES(1, {n})",
                               dialect="tsql")["rows"]
    windowed = engine.execute(f"SELECT DISTINCT {', '.join(c + ' OVER ()' for c in columns)} "
                              f"FROM GENERATE_SERIES(1, {n})", dialect="tsql")["rows"]
    for got in (aggregate, windowed):
        assert len(got) == 1
        assert [float(v) for v in got[0]] == pytest.approx(want, abs=1e-9)


# --- WR-708: a view named #v is refused before it runs -----------------------------------------------------------

@pytest.mark.parametrize("sql", ["CREATE VIEW #v AS SELECT 1 AS a", "CREATE OR ALTER VIEW #v AS SELECT 1 AS a",
                                 "ALTER VIEW #v AS SELECT 1 AS a", "CREATE VIEW dbo.#v AS SELECT 1 AS a",
                                 "CREATE VIEW [#v] (a) AS SELECT 1"])
def test_a_view_named_with_a_hash_is_refused_with_sql_servers_4103(engine, sql):
    with pytest.raises(ValueError) as err:
        engine.execute(sql, dialect="tsql")
    assert str(err.value) == 'Msg 4103, Level 15: "#v": Temporary views are not allowed.'
    with pytest.raises(ValueError, match="Temporary views are not allowed"):
        translate(sql)  # by name, before anything runs


def test_nothing_of_a_script_with_a_temp_view_runs(engine):
    with pytest.raises(ValueError, match=r"Statement 2 of 2 .* Msg 4103, Level 15: \"#w\": Temporary views are "
                                         r"not allowed\. None of the script ran"):
        engine.execute("CREATE TABLE dbo.before_view (a INT); CREATE VIEW #w AS SELECT 1 AS a", dialect="tsql")
    tables = engine.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'before_view'",
                            dialect="tsql")["rows"]
    assert tables == [[0]]


def test_views_and_temp_tables_with_other_names_still_run(engine):
    engine.execute("CREATE VIEW dbo.v708 AS SELECT 1 AS a", dialect="tsql")
    engine.execute("CREATE OR ALTER VIEW dbo.v708 AS SELECT 2 AS a", dialect="tsql")
    assert rows(engine, "SELECT a FROM dbo.v708") == [["2"]]
    engine.execute("CREATE TABLE #t708 (a INT)", dialect="tsql")
    engine.execute("INSERT INTO #t708 VALUES (3)", dialect="tsql")
    assert rows(engine, "SELECT a FROM #t708") == [["3"]]


def test_a_global_temp_view_keeps_the_global_temp_refusal(engine):
    with pytest.raises(ValueError, match=r"global temporary tables \(##g\)"):
        engine.execute("CREATE VIEW ##g AS SELECT 1 AS a", dialect="tsql")


# --- WR-710: one ORDER BY expression, and OVER with PARTITION BY only ---------------------------------------------

@pytest.mark.parametrize("function", ["PERCENTILE_CONT", "PERCENTILE_DISC"])
@pytest.mark.parametrize(("clause", "number", "words"), [
    ("WITHIN GROUP (ORDER BY id, s) OVER ()", 10751,
     "The ORDER BY in WITHIN GROUP clause of '{f}' function must have exactly one expression."),
    ("WITHIN GROUP (ORDER BY id, s)", 10751,
     "The ORDER BY in WITHIN GROUP clause of '{f}' function must have exactly one expression."),
    ("WITHIN GROUP (ORDER BY id) OVER (ORDER BY id)", 10758,
     "The function '{f}' may not have ORDER BY in OVER clause."),
    (("WITHIN GROUP (ORDER BY id) OVER (PARTITION BY g ORDER BY id ROWS BETWEEN UNBOUNDED PRECEDING AND "
      "UNBOUNDED FOLLOWING)"), 10758, "The function '{f}' may not have ORDER BY in OVER clause."),
    ("WITHIN GROUP (ORDER BY id) OVER (PARTITION BY g ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)",
     10752, "The function '{f}' may not have a window frame."),
    ("WITHIN GROUP (ORDER BY id) OVER (RANGE UNBOUNDED PRECEDING)", 10752,
     "The function '{f}' may not have a window frame."),
])
def test_what_sql_server_refuses_in_the_clauses_is_refused_with_its_number(engine, function, clause, number, words):
    with pytest.raises(ValueError) as err:
        engine.execute(f"SELECT {function}(0.5) {clause} FROM dbo.t", dialect="tsql")
    assert str(err.value) == f"Msg {number}, Level 15: " + words.format(f=function)


@pytest.mark.parametrize("function", ["PERCENTILE_CONT", "PERCENTILE_DISC"])
@pytest.mark.parametrize("p", ["1.5", "-0.1", "2"])
@pytest.mark.parametrize("over", ["", " OVER ()", " OVER (PARTITION BY g)"])
def test_a_percentile_outside_0_to_1_is_refused_by_name_without_a_guessed_number(engine, function, p, over):
    with pytest.raises(ValueError) as err:
        engine.execute(f"SELECT {function}({p}) WITHIN GROUP (ORDER BY id){over} FROM dbo.t", dialect="tsql")
    assert str(err.value) == (f"{function} takes a percentile from 0.0 to 1.0, and {p} isn't one. "
                              "A Fabric Warehouse refuses it too.")


def test_partition_by_alone_still_runs(engine):
    assert rows(engine, "SELECT DISTINCT RTRIM(g), PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY x) OVER (PARTITION BY g), "
                        "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY x) OVER (PARTITION BY g) FROM dbo.t ORDER BY 1") \
        == [["a", "10.0", "10"], ["b", "15.0", "10"]]


def test_a_named_window_with_order_by_is_refused_too():
    with pytest.raises(ValueError, match="may not have ORDER BY in OVER clause"):
        translate("SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY a) OVER w FROM q WINDOW w AS (ORDER BY a)")
