"""spark_api paths that mutation testing (batch 10, docs/quality/mutation-testing.md) showed no test checked,
apart from the docs-page comparisons (which the mutation runs can't read): the coverage table's states, the
"not implemented" detection, sparkContext's numbers, global temp views in tableExists, and the refusals of
crosstab, colRegex, toJSON's first() and sampleBy."""

from __future__ import annotations

import os
import time

import pytest
from duckdb.experimental.spark.exception import ContributionsAcceptedError

from fabric_desktop import spark_api
from fabric_desktop.notebook import Kernel


@pytest.fixture(autouse=True)
def no_lakehouse(monkeypatch):
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", None)


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


def run(kernel, code):
    out = kernel.run(code)
    assert out["status"] == "ok", out["outputs"]
    return "".join(o.get("text", "") for o in out["outputs"]).strip()


# ---- the coverage table (docs/notebook-spark-on-this-pc.md is generated from it)

def test_each_method_has_the_state_and_reason_it_has_on_this_pc():
    state = {(r["area"], r["name"]): (r["state"], r["why"]) for r in spark_api.coverage()}
    assert state[("DataFrame", "rdd")] == ("Fabric", "RDDs run in Fabric")
    assert state[("SparkSession", "streams")] == ("Fabric", "streaming runs in Fabric")
    assert state[("SparkSession", "udf")] == ("works", "")  # spark_udf.py: Python UDFs run on this PC
    assert state[("DataFrame", "cache")] == ("no-op", "")
    assert state[("DataFrame", "hint")] == ("no-op", "")
    assert state[("DataFrame", "toJSON")] == ("works", "")
    assert state[("DataFrame", "isStreaming")] == ("works", "")  # this PC's streaming adds it
    assert state[("SparkSession", "readStream")] == ("works", "")
    assert state[("DataFrame", "freqItems")] == ("works", "")  # spark_df_more.py (DuckDB raises ContributionsAcceptedError)
    assert len(state) == len(spark_api.PYSPARK_DATAFRAME) + len(spark_api.PYSPARK_SESSION)


def test_a_method_duckdb_refuses_is_counted_as_not_yet(monkeypatch):
    from duckdb.experimental.spark.sql.dataframe import DataFrame
    monkeypatch.setattr(DataFrame, "freqItems", lambda self, *a: _refused(), raising=False)
    monkeypatch.setattr(spark_api, "_not_implemented", lambda attr, error: getattr(attr, "__name__", "") == "<lambda>")
    import fabric_desktop.spark_df_more as more
    monkeypatch.setattr(more, "install", lambda: None)  # keep the refusing stand-in in place
    state = {(r["area"], r["name"]): r["state"] for r in spark_api.coverage()}
    assert state[("DataFrame", "freqItems")] == "missing"


def test_the_coverage_tables_count_and_label_each_method():
    rows = spark_api.coverage()
    lines = spark_api.coverage_markdown().splitlines()
    for area in ("DataFrame", "SparkSession"):
        mine = [r for r in rows if r["area"] == area]
        done = sum(r["state"] in ("works", "no-op") for r in mine)
        assert f"### {area}: {done} of {len(mine)}" in lines
    assert "| `rdd` | Runs in Fabric: RDDs run in Fabric |" in lines
    assert "| `cache` | Accepted, does nothing on one PC |" in lines
    assert "| `toJSON` | Works |" in lines
    assert "| `freqItems` | Works |" in lines
    assert "Not yet on this PC" not in "\n".join(lines)  # every PySpark 3.5 DataFrame method is placed


def _refused():
    raise ContributionsAcceptedError("not here")


def _long():
    steps = [
        "step 1",
        "step 2",
        "step 3",
        "step 4",
        "step 5",
        "step 6",
        "step 7",
        "step 8",
        "step 9",
        "step 10",
        "step 11",
        "step 12",
        "step 13",
        "step 14",
        "step 15",
        "step 16",
        "step 17",
        "step 18",
        "step 19",
        "step 20",
        "step 21",
        "step 22",
        "step 23",
        "step 24",
        "step 25",
    ]
    raise ContributionsAcceptedError(", ".join(steps))  # 25 lines and more: a real body, not a stub


def _mentions():
    return ContributionsAcceptedError  # names the error, nothing more


def test_a_short_body_raising_contributions_accepted_is_not_implemented():
    assert spark_api._not_implemented(_refused, ContributionsAcceptedError) is True
    assert spark_api._not_implemented(property(_refused), ContributionsAcceptedError) is True


@pytest.mark.parametrize("fn", [_long, _mentions, len, None])
def test_anything_else_is_implemented(fn):
    # A long body, one that only names the error, a builtin without Python source, and no attribute at all.
    assert spark_api._not_implemented(fn, ContributionsAcceptedError) is False


# ---- spark.sparkContext's numbers

def test_the_context_reports_this_pcs_cores_and_start_time_in_milliseconds(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 6)
    before = time.time() * 1000
    ctx = spark_api.LocalSparkContext(object())
    assert ctx.defaultParallelism == 6
    assert before - 1000 <= ctx.startTime <= time.time() * 1000 + 1000
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert spark_api.LocalSparkContext(object()).defaultParallelism == 1


# ---- global temp views in tableExists, and the refusals

def test_table_exists_finds_a_global_temp_view_only_under_global_temp(kernel):
    assert run(kernel, "spark.range(1).createOrReplaceGlobalTempView('gv_mut')\n"
                       "print(spark.catalog.tableExists('gv_mut', 'global_temp'),"
                       " spark.catalog.tableExists('gv_mut', 'GLOBAL_TEMP'),"
                       " spark.catalog.tableExists('nope_mut', 'global_temp'),"
                       " spark.catalog.tableExists('gv_mut', 'other_db'))") == "True True False False"


def test_crosstab_refuses_more_than_10000_column_values(kernel):
    assert run(kernel, "df = spark.range(10001).selectExpr('1 AS a', 'id AS b')\n"
                       "try:\n    df.crosstab('a', 'b')\n    print('ran')\n"
                       "except ValueError as e:\n    print(e)") == \
        "crosstab: b has more than 10000 distinct values; Spark allows up to 10000."


def test_colregex_refuses_a_regex_it_cannot_read(kernel):
    out = run(kernel, "try:\n    spark.range(1).colRegex('`(id`')\n    print('ran')\n"
                      "except ValueError as e:\n    print(e)")
    assert out.startswith("colRegex: `(id` isn't a regex this PC can read:")


def test_tojson_first_on_no_rows_says_the_rdd_is_empty(kernel):
    assert run(kernel, "try:\n    spark.range(0).toJSON().first()\n    print('ran')\n"
                       "except ValueError as e:\n    print(e)") == "RDD is empty"


def test_sampleby_refuses_fractions_that_are_not_a_dict(kernel):
    assert run(kernel, "try:\n    spark.range(3).sampleBy('id', [0.5])\n    print('ran')\n"
                       "except TypeError as e:\n    print(e)") == \
        "[NOT_DICT] Argument `fractions` should be a dict, got list."


# ---- toJSON's values that need Spark's own spelling

def test_json_writes_nan_and_the_infinities_as_spark_does():
    assert spark_api._json_value(float("nan")) == '"NaN"'
    assert spark_api._json_value(float("inf")) == '"Infinity"'
    assert spark_api._json_value(float("-inf")) == '"-Infinity"'


def test_json_writes_a_timestamp_with_a_zone_in_utc():
    import datetime
    stamp = datetime.datetime(2024, 1, 2, 5, 4, 5, 678901, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    assert spark_api._json_value(stamp) == '"2024-01-02T03:04:05.678Z"'
    assert spark_api._json_value(stamp.replace(tzinfo=None)) == '"2024-01-02T05:04:05.678Z"'
