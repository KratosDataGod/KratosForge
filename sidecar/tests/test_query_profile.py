"""Query profile (Databricks/Fabric Spark UI parity): the query behind a cell's spark or %%sql
result, run again under DuckDB's profiler (spark_local.query_profile)."""

import pytest

from fabric_desktop.frames import FrameGone
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel


def _frame(run):
    assert run["status"] == "ok", run
    return next(o for o in run["outputs"] if o["type"] == "table")["frame"]


def test_a_spark_result_has_its_steps_rows_and_time():
    k = Kernel()
    fid = _frame(k.run("spark.range(200000).selectExpr('id % 7 AS k').groupBy('k').count()"))
    p = k.frame_query_profile(fid)
    assert p["rows"] == 7 and p["rows_scanned"] == 200000 and p["seconds"] > 0 and p["more_steps"] == 0
    names = [s["name"] for s in p["steps"]]
    assert names[0] == "HASH_GROUP_BY" and names[-1] == "RANGE"
    assert [s["depth"] for s in p["steps"]] == sorted(s["depth"] for s in p["steps"])
    assert abs(sum(s["share"] for s in p["steps"]) - 1) < 0.01
    assert p["steps"][0]["details"]["Aggregates"] == "count_star()"


def test_a_sql_cell_result_and_a_dataframe_from_arrow_profile_too():
    k = Kernel()
    fid = _frame(k.run("%%sql\nSELECT id % 3 AS m, count(*) AS c FROM range(1000) t(id) GROUP BY 1"))
    assert k.frame_query_profile(fid)["rows"] == 3
    fid = _frame(k.run("import pyarrow as pa\nspark.createDataFrame(pa.table({'a': list(range(100))})).filter('a > 89')"))
    p = k.frame_query_profile(fid)
    assert p["rows"] == 10 and p["steps"][-1]["details"].get("Filters") == "a>89"


def test_profiling_leaves_the_session_as_it_was(capfd):
    k = Kernel()
    fid = _frame(k.run("spark.range(10)"))
    k.frame_query_profile(fid)
    run = k.run("print(spark.sql('SELECT current_setting(\\'enable_profiling\\')').collect()[0][0])")
    assert run["outputs"][0]["text"].strip() != "json"  # profiling is off again
    assert "EXPLAIN_ANALYZE" not in capfd.readouterr().out


def test_only_spark_results_offer_a_profile():
    k = Kernel()
    spark = next(o for o in k.run("spark.range(3)")["outputs"] if o["type"] == "table")
    sql = next(o for o in k.run("%%sql\nSELECT 1 AS a")["outputs"] if o["type"] == "table")
    other = next(o for o in k.run("import polars as pl\npl.DataFrame({'a': [1]})")["outputs"] if o["type"] == "table")
    assert spark.get("spark") is True and sql.get("spark") is True and "spark" not in other


def test_other_results_say_what_a_profile_is_for():
    k = Kernel()
    fid = _frame(k.run("import polars as pl\npl.DataFrame({'a': [1]})"))
    with pytest.raises(ValueError, match="result of spark or %%sql"):
        k.frame_query_profile(fid)
    with pytest.raises(FrameGone):
        k.frame_query_profile("nope")


def test_the_notebooks_own_process_profiles_too():
    k = ProcessKernel()
    try:
        fid = _frame(k.run("spark.range(50).filter('id > 9')"))
        assert k.frame_query_profile(fid)["rows"] == 40
    finally:
        k.close()
