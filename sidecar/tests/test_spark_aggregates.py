"""Spark SQL's and the DataFrame API's aggregates on this PC, value-checked against Spark 3.5 (WR-108).

Each expected value is Spark's, ANSI off as Fabric runs it, from the functions' documented results
(sql-ref-functions-builtin, the aggregate functions' source): collect_list and collect_set leave out NULLs
and give [] for no values; first and last take NULLs unless ignoreNulls; count(a, b) counts rows where
none is NULL; percentile, median and percentile_cont are doubles; grouping() is a tinyint; count_if and
sum of ints are bigints; stddev_samp and var_samp of one row are NULL. They run through the notebook's
spark.sql and pyspark.sql.functions. What still differs is listed in docs/sql-compatibility.md
("Aggregates on this PC"). None of this was run on a Fabric cluster: UNVERIFIED live.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlglot import exp

from fabric_desktop import spark_aggregates, sql, sql_dialects
from fabric_desktop.notebook import Kernel

DOCS = Path(__file__).resolve().parents[2] / "docs" / "sql-compatibility.md"

_VIEW = ("CREATE OR REPLACE TEMP VIEW t AS SELECT * FROM VALUES (1, 'a', 10.0D), (1, NULL, NULL), (1, 'b', 30.0D), "
         "(2, 'c', 5.0D), (2, 'c', NULL), (3, NULL, 7.0D), (NULL, 'd', 1.0D) AS t(g, s, x)")

_RUN = """
import json
spark.sql({view!r})
out = []
for q in {cases!r}:
    try:
        r = spark.sql(q)
        out.append([[d[1] for d in r.dtypes], [[v if v is None or isinstance(v, (list, bool, int, float, str)) else str(v)
                                               for v in row] for row in r.collect()]])
    except Exception as e:
        out.append(['error', str(e)])
print('RESULT' + json.dumps(out))
"""


@pytest.fixture(scope="module")
def kernel() -> Kernel:
    return Kernel()


def run(kernel: Kernel, code: str) -> str:
    out = kernel.run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    return text


def check(kernel: Kernel, table: list[tuple[str, list[str], list[list[object]]]]) -> None:
    text = run(kernel, _RUN.format(view=_VIEW, cases=[q for q, _, _ in table]))
    got = json.loads(text.split("RESULT", 1)[1])
    wrong = [(q, (t, v), g) for (q, t, v), g in zip(table, got, strict=True) if g != [t, v]]
    assert not wrong, "\n".join(f"{q}: want {w}, got {g}" for q, w, g in wrong)


AGGREGATES = [
    # collect_list and collect_set: NULLs left out, [] when no row has a value (sorted: no fixed order, LOC-006)
    ("SELECT g, array_sort(collect_list(s)), array_sort(collect_set(s)) FROM t GROUP BY g ORDER BY g",
     ["int", "array<string>", "array<string>"],
     [[None, ["d"], ["d"]], [1, ["a", "b"], ["a", "b"]], [2, ["c", "c"], ["c"]], [3, [], []]]),
    ("SELECT collect_list(x), collect_set(s), array_agg(s) FROM t WHERE g = 99",
     ["array<double>", "array<string>", "array<string>"], [[[], [], []]]),
    ("SELECT g, collect_list(s) OVER (PARTITION BY g ORDER BY x ROWS UNBOUNDED PRECEDING) FROM t WHERE g = 1 ORDER BY x",
     ["int", "array<string>"], [[1, []], [1, ["a"]], [1, ["a", "b"]]]),
    ("SELECT collect_list(s) FILTER (WHERE g = 3) FROM t", ["array<string>"], [[[]]]),
    # first and last: NULLs count unless ignoreNulls
    ("SELECT first(s), last(s), first(s, true), last(s, true) FROM VALUES (1, NULL), (1, 'a'), (1, NULL) AS v(g, s)",
     ["string", "string", "string", "string"], [[None, None, "a", "a"]]),
    ("SELECT first(s) IGNORE NULLS, last(s) IGNORE NULLS FROM VALUES (NULL), ('a'), ('b'), (NULL) AS v(s)",
     ["string", "string"], [["a", "b"]]),
    # count of several columns, and DISTINCT
    ("SELECT count(g, s), count(DISTINCT g, s), count(DISTINCT s), count(s) FROM t",
     ["bigint", "bigint", "bigint", "bigint"], [[4, 3, 4, 5]]),
    # approximate, documented as approximate: exact on these few values
    ("SELECT approx_count_distinct(s), approx_count_distinct(s, 0.01), approx_count_distinct(g, 0.2) FROM t",
     ["bigint", "bigint", "bigint"], [[4, 4, 3]]),
    ("SELECT percentile_approx(x, 0.5), percentile_approx(x, array(0.25, 0.75)), percentile_approx(x, 0.5, 100) FROM t",
     ["double", "array<double>", "double"], [[7.0, [5.0, 10.0], 7.0]]),
    ("SELECT percentile_approx(g, 0.5) FROM t", ["int"], [[1]]),
    # percentile, median and percentile_cont interpolate and are doubles; percentile_disc keeps the type
    ("SELECT g, percentile(x, 0.5), median(x), percentile(x, 0.3) FROM t GROUP BY g ORDER BY g",
     ["int", "double", "double", "double"],
     [[None, 1.0, 1.0, 1.0], [1, 20.0, 20.0, 16.0], [2, 5.0, 5.0, 5.0], [3, 7.0, 7.0, 7.0]]),
    ("SELECT percentile(g, 0.5), median(g), percentile(g, array(0.25, 0.75)) FROM t",
     ["double", "double", "array<double>"], [[1.5, 1.5, [1.0, 2.0]]]),
    ("SELECT percentile(CAST(x AS DECIMAL(5, 1)), 0.5) FROM t", ["double"], [[7.0]]),
    (("SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY col), percentile_disc(0.5) WITHIN GROUP (ORDER BY col) "
      "FROM VALUES (0), (6), (6), (7), (9), (10) AS tab(col)"), ["double", "int"], [[6.5, 6]]),
    # stddev and variance on small groups: sample ones NULL for one row, population ones 0.0
    ("SELECT g, stddev(x), stddev_samp(x), stddev_pop(x), variance(x), var_samp(x), var_pop(x) FROM t GROUP BY g ORDER BY g",
     ["int", "double", "double", "double", "double", "double", "double"],
     [[None, None, None, 0.0, None, None, 0.0], [1, 14.142135623730951, 14.142135623730951, 10.0, 200.0, 200.0, 100.0],
      [2, None, None, 0.0, None, None, 0.0], [3, None, None, 0.0, None, None, 0.0]]),
    ("SELECT stddev(x), variance(x), std(x) FROM t WHERE g = 99", ["double", "double", "double"], [[None, None, None]]),
    # types: Spark's long, tinyint
    ("SELECT sum(g), sum(DISTINCT g), count_if(g = 1), typeof(sum(g)), typeof(count_if(g = 1)) FROM t",
     ["bigint", "bigint", "bigint", "string", "string"], [[10, 6, 3, "bigint", "bigint"]]),
    ("SELECT regr_count(g, x) FROM t", ["bigint"], [[4]]),
    # NULL groups in ROLLUP, CUBE and GROUPING SETS: grouping() tells a data NULL from a subtotal
    ("SELECT g, sum(x), grouping(g), grouping_id(g) FROM t GROUP BY ROLLUP(g) ORDER BY g, 3",
     ["int", "double", "tinyint", "bigint"],
     [[None, 1.0, 0, 0], [None, 53.0, 1, 1], [1, 40.0, 0, 0], [2, 5.0, 0, 0], [3, 7.0, 0, 0]]),
    ("SELECT g, count(*), grouping(g) FROM t GROUP BY g WITH ROLLUP ORDER BY g, 3",
     ["int", "bigint", "tinyint"], [[None, 1, 0], [None, 7, 1], [1, 3, 0], [2, 2, 0], [3, 1, 0]]),
    ("SELECT g, count(*), grouping(g) FROM t GROUP BY g WITH CUBE ORDER BY g, 3",
     ["int", "bigint", "tinyint"], [[None, 1, 0], [None, 7, 1], [1, 3, 0], [2, 2, 0], [3, 1, 0]]),
    ("SELECT g, s, count(*) FROM t WHERE g IN (1, 2) GROUP BY g, s WITH ROLLUP ORDER BY 1, 2, 3",
     ["int", "string", "bigint"],
     [[None, None, 5], [1, None, 1], [1, None, 3], [1, "a", 1], [1, "b", 1], [2, None, 2], [2, "c", 2]]),
    ("SELECT g, s, grouping_id(g, s), count(*) FROM t WHERE g = 1 GROUP BY CUBE(g, s) ORDER BY 3, 2, 4",
     ["int", "string", "bigint", "bigint"],
     [[1, None, 0, 1], [1, "a", 0, 1], [1, "b", 0, 1], [1, None, 1, 3], [None, None, 2, 1], [None, "a", 2, 1],
      [None, "b", 2, 1], [None, None, 3, 3]]),
    ("SELECT g, s, count(*) FROM t WHERE g = 2 GROUP BY GROUPING SETS ((g), (s), ()) ORDER BY 1, 2",
     ["int", "string", "bigint"], [[None, None, 2], [None, "c", 2], [2, None, 2]]),
]


def test_aggregates_answer_as_in_spark(kernel: Kernel) -> None:
    check(kernel, AGGREGATES)


def test_column_names_are_sparks(kernel: Kernel) -> None:
    text = run(kernel, f"spark.sql({_VIEW!r})\n"
                       "print(spark.sql('SELECT collect_list(s), count(g, s), percentile(x, 0.5), "
                       "approx_count_distinct(s, 0.02), grouping(g), count_if(g = 1) FROM t GROUP BY ROLLUP(g)').columns)")
    assert text.strip() == ("['collect_list(s)', 'count(g, s)', 'percentile(x, 0.5, 1)', 'approx_count_distinct(s)', "
                            "'grouping(g)', 'count_if((g = 1))']")


def test_dataframe_aggregates(kernel: Kernel) -> None:
    text = run(kernel, """
import json
from pyspark.sql import functions as F
df = spark.createDataFrame([(1, 'a', 10.0), (1, None, None), (1, 'b', 30.0), (2, 'c', 5.0), (3, None, 7.0)],
                           "g int, s string, x double")
out = []
for frame in (
        df.groupBy("g").agg(F.array_sort(F.collect_list("s")), F.array_sort(F.collect_set("s"))).orderBy("g"),
        df.where("g = 99").agg(F.collect_list("s"), F.collect_set("x")),
        df.agg(F.approx_count_distinct("s", 0.01), F.sum("g"), F.count_if(F.col("g") == 1)),
        df.groupBy("g").agg(F.first("s", ignorenulls=True), F.stddev("x")).orderBy("g")):
    out.append([frame.dtypes, [list(r) for r in frame.collect()]])
print('RESULT' + json.dumps(out))
""")
    got = json.loads(text.split("RESULT", 1)[1])
    assert got[0] == [[["g", "int"], ["array_sort(collect_list(s))", "array<string>"],
                       ["array_sort(collect_set(s))", "array<string>"]],
                      [[1, ["a", "b"], ["a", "b"]], [2, ["c"], ["c"]], [3, [], []]]]
    assert got[1][1] == [[[], []]]
    assert got[2] == [[["approx_count_distinct(s)", "bigint"], ["sum(g)", "bigint"], ["count_if((g = 1))", "bigint"]],
                      [[3, 8, 3]]]
    assert got[3][1] == [[1, "a", 14.142135623730951], [2, "c", None], [3, None, None]]


def _rewrite(text: str) -> str:
    tree = sql_dialects.parse_one(text, read="spark")
    return spark_aggregates.spark_aggregate_results(tree).sql("duckdb")


def test_rewrite_units() -> None:
    assert _rewrite("SELECT a FROM t GROUP BY a, b WITH ROLLUP") == "SELECT a FROM t GROUP BY ROLLUP (a, b)"
    assert _rewrite("SELECT a FROM t GROUP BY a WITH CUBE") == "SELECT a FROM t GROUP BY CUBE (a)"
    assert _rewrite("SELECT a FROM t GROUP BY ROLLUP(a)") == "SELECT a FROM t GROUP BY ROLLUP (a)"
    assert "COUNT(CASE WHEN NOT a IS NULL AND NOT b IS NULL THEN 1 END)" in _rewrite("SELECT count(a, b) FROM t")
    # count(DISTINCT a, b) is already Spark's (sqlglot leaves out a row with a NULL)
    assert _rewrite("SELECT count(DISTINCT a, b) FROM t") == \
        "SELECT COUNT(DISTINCT CASE WHEN a IS NULL THEN NULL WHEN b IS NULL THEN NULL ELSE (a, b) END) FROM t"
    assert _rewrite("SELECT approx_count_distinct(a, 0.1) FROM t") == "SELECT APPROX_COUNT_DISTINCT(a) FROM t"
    # Each is fixed once, however many times the rewrite runs.
    tree = sql_dialects.parse_one("SELECT collect_list(a), grouping(b) FROM t GROUP BY ROLLUP(b)", read="spark")
    twice = spark_aggregates.spark_aggregate_results(spark_aggregates.spark_aggregate_results(tree))
    assert len(list(twice.find_all(exp.Coalesce))) == 1
    assert len(list(twice.find_all(exp.Cast))) == 1


def test_databricks_sql_path_too() -> None:
    import duckdb

    from fabric_desktop import sql
    from fabric_desktop.engines.local_sql import _databricks
    con = duckdb.connect()
    query = "SELECT collect_list(s), count(s, x), median(x) FROM VALUES (NULL, 1), (NULL, 2) AS v(s, x)"
    assert con.execute(sql.duckdb_sql(_databricks(query)[-1])).fetchone() == ([], 0, 1.5)


STILL_DIFFERS = ["approx_count_distinct", "percentile_approx", "collect_list", "GROUP BY a, b GROUPING SETS", "grouping__id",
                 "sum", "histogram_numeric"]


def test_docs_list_what_still_differs() -> None:
    text = DOCS.read_text(encoding="utf-8")
    assert "## Aggregates on this PC" in text
    section = text.split("## Aggregates on this PC", 1)[1].split("\n## ", 1)[0]
    missing = [name for name in STILL_DIFFERS if f"`{name}" not in section]
    assert not missing, missing


# PercentileCont/PercentileDisc.sql in Spark 3.5.1 (read from spark-catalyst's class constants):
# "<name>(<fraction>) WITHIN GROUP (ORDER BY <column>[ DESC])"; NULLS FIRST/LAST isn't shown.
@pytest.mark.parametrize(("query", "name"), [
    ("percentile_cont(0.5) WITHIN GROUP (ORDER BY x)", "percentile_cont(0.5) WITHIN GROUP (ORDER BY x)"),
    ("percentile_disc(0.25) WITHIN GROUP (ORDER BY x DESC)", "percentile_disc(0.25) WITHIN GROUP (ORDER BY x DESC)"),
    ("percentile_cont(0.5) WITHIN GROUP (ORDER BY x ASC NULLS LAST)", "percentile_cont(0.5) WITHIN GROUP (ORDER BY x)"),
    ("percentile_disc(0.5) WITHIN GROUP (ORDER BY x + 1)", "percentile_disc(0.5) WITHIN GROUP (ORDER BY (x + 1))"),
    ("percentile_cont(0.5) WITHIN GROUP (ORDER BY x) FILTER (WHERE g = 1)",
     "percentile_cont(0.5) WITHIN GROUP (ORDER BY x) FILTER (WHERE (g = 1))"),
])
def test_percentile_cont_and_disc_are_named_as_spark(query: str, name: str) -> None:
    assert str(sql.parse(f"SELECT {query} FROM t")[0].expressions[0].alias_or_name) == name
