"""Spark's window functions on this PC (WR-111).

Expected values are Spark 3.5's documented behaviour (sql-ref-syntax-qry-select-window, and the
IntegerType of RowNumberLike, RankLike and NTile in spark-catalyst 3.5.1); the refusals follow
ResolveWindowOrder and ResolveWindowFrame. None of this was run on a Fabric cluster: UNVERIFIED live.
"""

from __future__ import annotations

import json

import pytest

from fabric_desktop import spark_windows, sql
from fabric_desktop.notebook import Kernel

_ROWS = "VALUES (1, 1, 'a'), (1, NULL, 'b'), (1, 2, 'c'), (1, 2, 'd'), (1, 3, 'f'), (1, 4, 'g'), (2, 5, 'e') AS t(p, v, n)"
_W = "OVER (PARTITION BY p ORDER BY v)"


@pytest.fixture(scope="module")
def kernel() -> Kernel:
    return Kernel()


def run(kernel: Kernel, code: str) -> object:
    out = kernel.run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    return json.loads(text.split("RESULT", 1)[1])


def rows_of(kernel: Kernel, query: str) -> dict:
    return run(kernel, f"""
import json
df = spark.sql({query!r})
print('RESULT' + json.dumps({{'types': df.dtypes, 'rows': [list(r) for r in df.collect()]}}))
""")


def test_ranks_ties_and_types(kernel: Kernel) -> None:
    got = rows_of(kernel, f"SELECT n, row_number() {_W} AS r, rank() {_W} AS rk, dense_rank() {_W} AS dr, "
                          f"percent_rank() {_W} AS pr, cume_dist() {_W} AS cd, ntile(4) {_W} AS nt "
                          f"FROM {_ROWS} ORDER BY p, v, n")
    assert got["types"] == [["n", "string"], ["r", "int"], ["rk", "int"], ["dr", "int"], ["pr", "double"],
                            ["cd", "double"], ["nt", "int"]]
    assert [r[0] for r in got["rows"]] == ["b", "a", "c", "d", "f", "g", "e"]  # NULL first ascending
    assert [r[2] for r in got["rows"]] == [1, 2, 3, 3, 5, 6, 1]
    assert [r[3] for r in got["rows"]] == [1, 2, 3, 3, 4, 5, 1]
    assert [r[4] for r in got["rows"]] == [0.0, 0.2, 0.4, 0.4, 0.8, 1.0, 0.0]
    assert [round(r[5], 4) for r in got["rows"]] == [0.1667, 0.3333, 0.6667, 0.6667, 0.8333, 1.0, 1.0]
    assert [r[6] for r in got["rows"]] == [1, 1, 2, 2, 3, 4, 1]  # 6 rows in 4 groups: 2, 2, 1, 1


def test_ntile_gives_the_extra_rows_to_the_first_groups(kernel: Kernel) -> None:
    got = rows_of(kernel, f"SELECT ntile(3) OVER (ORDER BY n) AS t FROM {_ROWS} ORDER BY n")
    assert [r[0] for r in got["rows"]] == [1, 1, 1, 2, 2, 3, 3]


def test_lag_and_lead_defaults(kernel: Kernel) -> None:
    got = rows_of(kernel, f"SELECT n, lag(v) OVER (ORDER BY n) AS a, lead(v, 2) OVER (ORDER BY n) AS b, "
                          f"lag(v, 1, -1) OVER (ORDER BY n) AS c, lead(v, 1, -1) OVER (ORDER BY n) AS d "
                          f"FROM {_ROWS} ORDER BY n")
    assert got["rows"] == [["a", None, 2, -1, None], ["b", 1, 2, 1, 2], ["c", None, 5, None, 2],
                           ["d", 2, 3, 2, 5], ["e", 2, 4, 2, 3], ["f", 5, None, 5, 4], ["g", 3, None, 3, -1]]


def test_default_frames(kernel: Kernel) -> None:
    got = rows_of(kernel, f"SELECT n, sum(v) OVER (ORDER BY v) AS s, sum(v) OVER () AS s2, "
                          f"last_value(n) OVER (ORDER BY v) AS l, sum(v) OVER (ORDER BY v DESC) AS d "
                          f"FROM {_ROWS} ORDER BY v, n")
    assert got["rows"] == [["b", None, 17, "b", 17], ["a", 1, 17, "a", 17], ["c", 5, 17, "d", 16],
                           ["d", 5, 17, "d", 16], ["f", 8, 17, "f", 12], ["g", 12, 17, "g", 9],
                           ["e", 17, 17, "e", 5]]


def test_names_write_the_frame_out(kernel: Kernel) -> None:
    got = rows_of(kernel, "SELECT row_number() OVER (ORDER BY v), rank() OVER (ORDER BY v), "
                          "lag(v) OVER (ORDER BY v), cume_dist() OVER (ORDER BY v) FROM VALUES (1) AS t(v)")
    assert [t[0] for t in got["types"]] == [
        "row_number() OVER (ORDER BY v ASC NULLS FIRST ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
        "RANK() OVER (ORDER BY v ASC NULLS FIRST ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
        "lag(v, 1, NULL) OVER (ORDER BY v ASC NULLS FIRST ROWS BETWEEN -1 FOLLOWING AND -1 FOLLOWING)",
        "cume_dist() OVER (ORDER BY v ASC NULLS FIRST RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"]
    assert [t[1] for t in got["types"]] == ["int", "int", "int", "double"]


@pytest.mark.parametrize(("query", "says"), [
    ("SELECT row_number() OVER (PARTITION BY p) FROM t", "row_number needs an ORDER BY"),
    ("SELECT rank() OVER () FROM t", "rank needs an ORDER BY"),
    ("SELECT ntile(2) OVER (PARTITION BY p) FROM t", "ntile needs an ORDER BY"),
    ("SELECT cume_dist() OVER () FROM t", "cume_dist needs an ORDER BY"),
    ("SELECT lag(v) OVER (PARTITION BY p) FROM t", "lag needs an ORDER BY"),
    ("SELECT lead(v) IGNORE NULLS OVER () FROM t", "lead needs an ORDER BY"),
    ("SELECT lag(v) OVER (ORDER BY v ROWS BETWEEN 1 PRECEDING AND 1 PRECEDING) FROM t", "lag can't be given"),
    ("SELECT row_number() OVER (ORDER BY v ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) FROM t",
     "row_number works over ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW only"),
    ("SELECT cume_dist() OVER (ORDER BY v ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM t",
     "cume_dist works over RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW only"),
])
def test_spark_sql_refuses_what_spark_stops_on(query: str, says: str) -> None:
    with pytest.raises(ValueError, match=says):
        sql.spark_fixes(sql.parse(query)[0])


@pytest.mark.parametrize("query", [
    "SELECT ntile(2) OVER (ORDER BY v ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM t",
    "SELECT cume_dist() OVER (ORDER BY v RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM t",
    "SELECT sum(v) OVER (PARTITION BY p) FROM t",
    "SELECT first_value(v) OVER () FROM t",
    "SELECT row_number() OVER w FROM t WINDOW w AS (ORDER BY v)",
    "SELECT monotonically_increasing_id() FROM t",
])
def test_spark_sql_keeps_what_spark_runs(query: str) -> None:
    sql.spark_fixes(sql.parse(query)[0])


def test_only_the_querys_own_ranks_are_cast() -> None:
    out = sql.duckdb_sql(sql.spark_fixes(sql.parse("SELECT monotonically_increasing_id(), rank() OVER (ORDER BY v) FROM t")[0]))
    assert out.count("AS INT)") == 1 and "CAST(RANK() OVER" in out


def test_databricks_sql_ranks_are_ints() -> None:
    import duckdb

    from fabric_desktop.engines.local_sql import _databricks
    con = duckdb.connect()
    rel = con.sql(sql.duckdb_sql(_databricks("SELECT dense_rank() OVER (ORDER BY v) AS r FROM VALUES (1), (2) AS t(v)")[-1]))
    assert [str(t) for t in rel.types] == ["INTEGER"]


def test_dataframe_windows(kernel: Kernel) -> None:
    got = run(kernel, """
import json
from pyspark.sql import functions as F, Window as W
df = spark.createDataFrame([(1, 1), (1, None), (1, 2)], "p int, v int")
w = W.partitionBy("p").orderBy("v")
d = df.select("v", F.row_number().over(w), F.rank().over(w), F.dense_rank().over(w), F.ntile(2).over(w),
              F.lag("v").over(w), F.lead("v", 2, 0).over(w), F.percent_rank().over(w), F.cume_dist().over(w),
              F.sum("v").over(w), F.sum("v").over(W.partitionBy("p")), F.sum("v").over(w.rowsBetween(-1, 1)),
              F.max("v").over(W.partitionBy("p").orderBy(F.col("v").desc())))
print('RESULT' + json.dumps({'types': d.dtypes, 'rows': [list(r) for r in d.orderBy("v").collect()],
                             'with': df.withColumn("r", F.row_number().over(w)).dtypes}))
""")
    assert got["types"] == [
        ["v", "int"],
        ["row_number() OVER (PARTITION BY p ORDER BY v ASC NULLS FIRST ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)", "int"],
        ["RANK() OVER (PARTITION BY p ORDER BY v ASC NULLS FIRST ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)", "int"],
        ["DENSE_RANK() OVER (PARTITION BY p ORDER BY v ASC NULLS FIRST ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)", "int"],
        ["ntile(2) OVER (PARTITION BY p ORDER BY v ASC NULLS FIRST ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)", "int"],
        ["lag(v, 1, NULL) OVER (PARTITION BY p ORDER BY v ASC NULLS FIRST ROWS BETWEEN -1 FOLLOWING AND -1 FOLLOWING)", "int"],
        ["lead(v, 2, 0) OVER (PARTITION BY p ORDER BY v ASC NULLS FIRST ROWS BETWEEN 2 FOLLOWING AND 2 FOLLOWING)", "int"],
        ["PERCENT_RANK() OVER (PARTITION BY p ORDER BY v ASC NULLS FIRST ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)", "double"],
        ["cume_dist() OVER (PARTITION BY p ORDER BY v ASC NULLS FIRST RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)", "double"],
        ["sum(v) OVER (PARTITION BY p ORDER BY v ASC NULLS FIRST RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)", "bigint"],
        ["sum(v) OVER (PARTITION BY p ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)", "bigint"],
        ["sum(v) OVER (PARTITION BY p ORDER BY v ASC NULLS FIRST ROWS BETWEEN -1 FOLLOWING AND 1 FOLLOWING)", "bigint"],
        ["max(v) OVER (PARTITION BY p ORDER BY v DESC NULLS LAST RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)", "int"],
    ]
    assert got["rows"] == [[None, 1, 1, 1, 1, None, 2, 0.0, 1 / 3, None, 3, 1, 2],
                           [1, 2, 2, 2, 1, None, 0, 0.5, 2 / 3, 1, 3, 3, 2],
                           [2, 3, 3, 3, 2, 1, 0, 1.0, 1.0, 3, 3, 3, 2]]  # max descending: 2 from the first row
    assert got["with"] == [["p", "int"], ["v", "int"], ["r", "int"]]


@pytest.mark.parametrize(("make", "says"), [
    ("F.row_number().over(W.partitionBy('p'))", "row_number needs an ORDER BY"),
    ("F.lag('v').over(W.partitionBy('p'))", "lag needs an ORDER BY"),
    ("F.lag('v').over(W.orderBy('v').rowsBetween(-1, -1))", "lag can't be given"),
    ("F.rank().over(W.orderBy('v').rowsBetween(-1, 1))", "rank works over"),
])
def test_dataframe_refuses_what_spark_stops_on(kernel: Kernel, make: str, says: str) -> None:
    got = run(kernel, f"""
import json
from pyspark.sql import functions as F, Window as W
try:
    {make}
    said = ''
except ValueError as e:
    said = str(e)
print('RESULT' + json.dumps(said))
""")
    assert says in str(got)


def test_refusal_rules() -> None:
    assert spark_windows.refusal("sum", False, None) is None
    assert spark_windows.refusal("rank", True, None) is None
    assert spark_windows.refusal("rank", True, "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW") is None
    assert spark_windows.refusal("cume_dist", True, "RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW") is None
    assert spark_windows.refusal("lead", True, "ROWS BETWEEN 1 FOLLOWING AND 1 FOLLOWING")
