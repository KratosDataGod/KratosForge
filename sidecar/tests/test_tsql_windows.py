"""WR-212: local T-SQL ordering and window functions give a Fabric Warehouse's answers where NULLs and ties are.

T-SQL sorts NULL as the smallest value: first in ascending order and last in descending order, in
ORDER BY, TOP, OFFSET/FETCH, a window's ORDER BY and WITHIN GROUP. These tests cover that, plus:
- ROW_NUMBER, RANK, DENSE_RANK, NTILE, CUME_DIST and PERCENT_RANK with ties;
- LAG and LEAD with and without a default;
- the default frame of an aggregate OVER (ORDER BY ...), which is RANGE, so ties share a running total;
- PERCENTILE_CONT and PERCENTILE_DISC, ascending and descending.

Each value is what T-SQL's reference gives for the rows below.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("windows") / "app" / "local-data")
    e = local.engine(local.create("Windows")["id"])
    # x has two NULLs and a tie at 10; s has a NULL; g splits the rows in two.
    e.execute("CREATE TABLE dbo.t AS SELECT * FROM (VALUES (1, 10, 'a', 'p'), (2, NULL, 'a', NULL), (3, 20, 'b', 'r'), "
              "(4, 10, 'b', 'q'), (5, NULL, 'b', 's')) v(id, x, g, s)")
    e.execute("CREATE TABLE dbo.d AS SELECT * FROM (VALUES (CAST(1.25 AS DECIMAL(5, 2)), CAST(-2147483648 AS INTEGER), "
              "DATE '2024-01-01'), (CAST(2.50 AS DECIMAL(5, 2)), CAST(7 AS INTEGER), DATE '2024-02-01'), "
              "(CAST(4.00 AS DECIMAL(5, 2)), CAST(9 AS INTEGER), DATE '2024-03-01')) v(m, i, dt)")
    return e


def rows(engine, sql):
    r = engine.execute(sql, dialect="tsql")
    return [[None if v is None else str(v) for v in row] for row in r["rows"]], \
        (r.get("translation") or {}).get("notes") or []


# (T-SQL query, T-SQL's rows, each value as text)
CASES = [
 # NULL sorts first ascending, last descending
 ("SELECT id FROM dbo.t ORDER BY x, id", [["2"], ["5"], ["1"], ["4"], ["3"]]),
 ("SELECT id FROM dbo.t ORDER BY x DESC, id", [["3"], ["1"], ["4"], ["2"], ["5"]]),
 ("SELECT id FROM dbo.t ORDER BY s", [["2"], ["1"], ["4"], ["3"], ["5"]]),
 ("SELECT id FROM dbo.t ORDER BY s DESC", [["5"], ["3"], ["4"], ["1"], ["2"]]),
 ("SELECT TOP 2 id FROM dbo.t ORDER BY x, id", [["2"], ["5"]]),
 ("SELECT TOP 1 id FROM dbo.t ORDER BY x DESC", [["3"]]),
 ("SELECT TOP 1 WITH TIES id FROM dbo.t ORDER BY x", [["2"], ["5"]]),
 ("SELECT id FROM dbo.t ORDER BY x, id OFFSET 1 ROWS FETCH NEXT 2 ROWS ONLY", [["5"], ["1"]]),
 ("SELECT id FROM dbo.t ORDER BY x DESC, id OFFSET 3 ROWS", [["2"], ["5"]]),
 ("SELECT x, COUNT(*) FROM dbo.t GROUP BY x ORDER BY x", [[None, "2"], ["10", "2"], ["20", "1"]]),
 ("SELECT v FROM (SELECT 1 AS v UNION SELECT NULL UNION SELECT 0) u ORDER BY v", [[None], ["0"], ["1"]]),
 ("SELECT STRING_AGG(id, ',') WITHIN GROUP (ORDER BY x, id) FROM dbo.t", [["2,5,1,4,3"]]),
 ("SELECT STRING_AGG(id, ',') WITHIN GROUP (ORDER BY x DESC, id) FROM dbo.t", [["3,1,4,2,5"]]),
 # ranking: NULLs rank first, ties share a rank
 (("SELECT id, ROW_NUMBER() OVER (ORDER BY x, id), RANK() OVER (ORDER BY x), DENSE_RANK() OVER (ORDER BY x) "
  "FROM dbo.t ORDER BY id"),
  [["1", "3", "3", "2"], ["2", "1", "1", "1"], ["3", "5", "5", "3"], ["4", "4", "3", "2"], ["5", "2", "1", "1"]]),
 ("SELECT id, ROW_NUMBER() OVER (ORDER BY x DESC, id), RANK() OVER (ORDER BY x DESC) FROM dbo.t ORDER BY id",
  [["1", "2", "2"], ["2", "4", "4"], ["3", "1", "1"], ["4", "3", "2"], ["5", "5", "4"]]),
 ("SELECT id, ROW_NUMBER() OVER (PARTITION BY x ORDER BY id) FROM dbo.t ORDER BY id",
  [["1", "1"], ["2", "1"], ["3", "1"], ["4", "2"], ["5", "2"]]),
 ("SELECT id, NTILE(3) OVER (ORDER BY id) FROM dbo.t ORDER BY id",
  [["1", "1"], ["2", "1"], ["3", "2"], ["4", "2"], ["5", "3"]]),
 ("SELECT id, CUME_DIST() OVER (ORDER BY x), PERCENT_RANK() OVER (ORDER BY x) FROM dbo.t ORDER BY id",
  [["1", "0.8", "0.5"], ["2", "0.4", "0.0"], ["3", "1.0", "1.0"], ["4", "0.8", "0.5"], ["5", "0.4", "0.0"]]),
 # LAG and LEAD: NULL past the edge, or the default given
 (("SELECT id, LAG(x) OVER (ORDER BY id), LAG(x, 1, 0) OVER (ORDER BY id), LEAD(x, 2, -1) OVER (ORDER BY id) "
  "FROM dbo.t ORDER BY id"),
  [["1", None, "0", "20"], ["2", "10", "10", "10"], ["3", None, None, None], ["4", "20", "20", "-1"],
   ["5", "10", "10", "-1"]]),
 ("SELECT id, LEAD(id) OVER (PARTITION BY g ORDER BY id) FROM dbo.t ORDER BY id",
  [["1", "2"], ["2", None], ["3", "4"], ["4", "5"], ["5", None]]),
 # the default frame with ORDER BY is RANGE: rows that tie share a running total
 ("SELECT id, SUM(x) OVER (ORDER BY x), COUNT(*) OVER (ORDER BY x) FROM dbo.t ORDER BY id",
  [["1", "20", "4"], ["2", None, "2"], ["3", "40", "5"], ["4", "20", "4"], ["5", None, "2"]]),
 ("SELECT id, SUM(x) OVER (ORDER BY x, id ROWS UNBOUNDED PRECEDING) FROM dbo.t ORDER BY id",
  [["1", "10"], ["2", None], ["3", "40"], ["4", "20"], ["5", None]]),
 ("SELECT id, MAX(x) OVER (PARTITION BY g ORDER BY id), COUNT(x) OVER (), AVG(x) OVER () FROM dbo.t ORDER BY id",
  [["1", "10", "3", "13"], ["2", "10", "3", "13"], ["3", "20", "3", "13"], ["4", "20", "3", "13"],
   ["5", "20", "3", "13"]]),
 ("SELECT id, FIRST_VALUE(x) OVER (ORDER BY x, id), LAST_VALUE(x) OVER (ORDER BY x) FROM dbo.t ORDER BY id",
  [["1", None, "10"], ["2", None, None], ["3", None, "20"], ["4", None, "10"], ["5", None, None]]),
 # PERCENTILE_CONT is a float; NULLs are left out; DESC counts from the largest value
 (("SELECT DISTINCT RTRIM(g), PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY x) OVER (PARTITION BY g), "
  "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY x) OVER (PARTITION BY g) FROM dbo.t ORDER BY 1"),
  [["a", "10.0", "10"], ["b", "15.0", "10"]]),
 (("SELECT DISTINCT RTRIM(g), PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY x DESC) OVER (PARTITION BY g), "
  "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY x DESC) OVER (PARTITION BY g) FROM dbo.t ORDER BY 1"),
  [["a", "10.0", "10"], ["b", "17.5", "20"]]),
 (("SELECT DISTINCT PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY id) OVER (), "
  "PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY id DESC) OVER () FROM dbo.t"), [["2.0", "4.0"]]),
 (("SELECT DISTINCT PERCENTILE_DISC(0.3) WITHIN GROUP (ORDER BY id) OVER (), "
  "PERCENTILE_DISC(0.3) WITHIN GROUP (ORDER BY id DESC) OVER (), "
  "PERCENTILE_DISC(0.4) WITHIN GROUP (ORDER BY id) OVER (), "
  "PERCENTILE_DISC(0.4) WITHIN GROUP (ORDER BY id DESC) OVER () FROM dbo.t"), [["2", "4", "2", "4"]]),
 (("SELECT DISTINCT PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY m) OVER (), "
  "PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY m DESC) OVER (), "
  "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY m DESC) OVER () FROM dbo.d"), [["1.875", "3.25", "2.50"]]),
 (("SELECT DISTINCT PERCENTILE_DISC(0) WITHIN GROUP (ORDER BY i DESC) OVER (), "
  "PERCENTILE_DISC(1) WITHIN GROUP (ORDER BY i DESC) OVER () FROM dbo.d"), [["9", "-2147483648"]]),
 ("SELECT DISTINCT PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY dt) OVER () FROM dbo.d", [["2024-02-01"]]),
]


@pytest.mark.parametrize(("sql", "want"), CASES,
                         ids=[c[0] if len(c[0]) <= 150 else f"{c[0][:60]}... case {i}" for i, c in enumerate(CASES)])
def test_each_query_gives_tsqls_rows(engine, sql, want):
    got, notes = rows(engine, sql)
    assert got == want
    assert notes == [], "a checked case runs without a caveat"


def test_percentile_disc_keeps_its_values_type(engine):
    r = engine.execute("SELECT DISTINCT PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY i DESC) OVER () AS v FROM dbo.d",
                       dialect="tsql")
    assert [list(x) for x in r["rows"]] == [[7]]
    assert type(r["rows"][0][0]) is int


def test_a_descending_percentile_disc_on_dates_or_text_is_refused_by_name(engine):
    for column, table in (("dt", "dbo.d"), ("s", "dbo.t")):
        with pytest.raises(ValueError, match=r"PERCENTILE_DISC with ORDER BY \.\.\. DESC on a value that isn't a "
                                             r"number isn't mapped yet"):
            engine.execute(f"SELECT DISTINCT PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY {column} DESC) OVER () "
                           f"FROM {table}", dialect="tsql")


def test_a_descending_percentile_whose_type_cant_be_told_still_counts_from_the_largest():
    from fabric_desktop.localdata.tsql import translate
    texts, _ = translate("SELECT PERCENTILE_DISC(0.25) WITHIN GROUP (ORDER BY a DESC) OVER () FROM q")
    assert texts == ["SELECT -(QUANTILE_DISC(-(a), 0.25 ORDER BY -(a) ASC NULLS FIRST) OVER ()) FROM q"]
