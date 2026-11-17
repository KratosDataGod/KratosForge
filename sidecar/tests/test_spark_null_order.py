"""NULLs in ORDER BY on this PC, as Spark orders them (WR-110).

Spark puts NULLs first in an ascending sort and last in a descending one, unless the query says
NULLS FIRST or NULLS LAST. The notebook's DuckDB session uses the same default, so an explicit NULLS
LAST must reach DuckDB: sqlglot used to leave it out, as DuckDB's own default. Expected values are
Spark 3.5's documented ORDER BY behaviour (sql-ref-syntax-qry-select-orderby). None of this was run
on a Fabric cluster: UNVERIFIED live.
"""

from __future__ import annotations

import json

import pytest

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel

_ROWS = "VALUES (1), (NULL), (2) AS t(g)"

CASES = [
    (f"SELECT g FROM {_ROWS} ORDER BY g", [None, 1, 2]),
    (f"SELECT g FROM {_ROWS} ORDER BY g ASC", [None, 1, 2]),
    (f"SELECT g FROM {_ROWS} ORDER BY g NULLS LAST", [1, 2, None]),
    (f"SELECT g FROM {_ROWS} ORDER BY g ASC NULLS LAST", [1, 2, None]),
    (f"SELECT g FROM {_ROWS} ORDER BY g DESC", [2, 1, None]),
    (f"SELECT g FROM {_ROWS} ORDER BY g DESC NULLS FIRST", [None, 2, 1]),
    (f"SELECT g FROM {_ROWS} ORDER BY g DESC NULLS LAST", [2, 1, None]),
    (f"SELECT g FROM (SELECT g, row_number() OVER (ORDER BY g ASC NULLS LAST) AS r FROM {_ROWS}) ORDER BY r",
     [1, 2, None]),
    (f"SELECT g FROM (SELECT g, row_number() OVER (ORDER BY g) AS r FROM {_ROWS}) ORDER BY r", [None, 1, 2]),
    (f"SELECT collect_list(g) FROM (SELECT g FROM {_ROWS} ORDER BY g NULLS LAST LIMIT 2)", [[1, 2]]),
]


@pytest.fixture(scope="module")
def kernel() -> Kernel:
    return Kernel()


def run(kernel: Kernel, code: str) -> str:
    out = kernel.run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    return text


def test_spark_sql_orders_nulls_as_spark(kernel: Kernel) -> None:
    text = run(kernel, f"""
import json
print('RESULT' + json.dumps([[r[0] for r in spark.sql(q).collect()] for q in {[q for q, _ in CASES]!r}]))
""")
    got = json.loads(text.split("RESULT", 1)[1])
    wrong = [(q, want, g) for (q, want), g in zip(CASES, got, strict=True) if g != want]
    assert not wrong, wrong


def test_dataframe_sort_orders_nulls_as_spark(kernel: Kernel) -> None:
    text = run(kernel, """
import json
from pyspark.sql import functions as F
df = spark.createDataFrame([(1,), (None,), (2,)], "g int")
out = [[r.g for r in df.orderBy(c).collect()] for c in
       (F.col("g"), F.col("g").asc_nulls_last(), F.col("g").desc(), F.col("g").desc_nulls_first())]
print('RESULT' + json.dumps(out))
""")
    assert json.loads(text.split("RESULT", 1)[1]) == [[None, 1, 2], [1, 2, None], [2, 1, None], [None, 2, 1]]


@pytest.mark.parametrize(("query", "written"), [
    ("SELECT g FROM t ORDER BY g NULLS LAST", "ORDER BY g NULLS LAST"),
    ("SELECT g FROM t ORDER BY g ASC NULLS LAST", "ORDER BY g ASC NULLS LAST"),
    ("SELECT g FROM t ORDER BY g", "ORDER BY g NULLS FIRST"),
    ("SELECT g FROM t ORDER BY g DESC", "ORDER BY g DESC"),
])
def test_an_ascending_nulls_last_is_written_out(query: str, written: str) -> None:
    assert sql.duckdb_sql(sql.parse(query)[0]).endswith(written)


def test_databricks_sql_keeps_nulls_last() -> None:
    import duckdb

    from fabric_desktop.engines.local_sql import _databricks
    con = duckdb.connect()
    con.execute("SET default_null_order = 'nulls_first_on_asc_last_on_desc'")
    rows = con.execute(sql.duckdb_sql(_databricks(f"SELECT g FROM {_ROWS} ORDER BY g NULLS LAST")[-1])).fetchall()
    assert [r[0] for r in rows] == [1, 2, None]
