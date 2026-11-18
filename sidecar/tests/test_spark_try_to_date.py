"""WR-117: try_to_date isn't a Spark 3.5 built-in (spark_builtins.json), so Fabric Runtime 1.3 stops on it with
UNRESOLVED_ROUTINE. The Spark SQL path here refuses it by name, before anything runs, instead of stopping with
DuckDB's own "function does not exist" error; Databricks SQL, where it is a built-in, keeps answering."""
import datetime as dt
import json
from pathlib import Path

import pytest

from fabric_desktop import databricks_coverage, sql
from fabric_desktop.notebook import Kernel

REFUSED = r"try_to_date isn't a Spark 3\.5 function, so nothing ran\. Fabric Runtime 1\.3 stops on it too"


@pytest.mark.parametrize("call", [
    "try_to_date('abc')",                            # bad text
    "try_to_date('2024-02-29')",                     # good text: Fabric still stops
    "try_to_date('2024/02/29', 'yyyy/MM/dd')",       # with a format
    "try_to_date(NULL)",
    "TRY_TO_DATE(CAST(NULL AS STRING), 'yyyy')",
])
def test_try_to_date_is_refused_by_name_on_the_spark_path(call):
    with pytest.raises(ValueError, match=REFUSED):
        sql.run(f"SELECT {call} AS a")


def test_the_refusal_names_to_date_and_to_date_gives_null_for_bad_text():
    with pytest.raises(ValueError, match=r"UNRESOLVED_ROUTINE\)\. Use to_date instead\. With ANSI mode off, as Fabric runs it, to_date gives NULL"):
        sql.run("SELECT 1 AS a FROM (SELECT 1) WHERE try_to_date('x') IS NULL")
    assert sql.run("SELECT to_date('abc') AS a, to_date('2024/02/29', 'yyyy/MM/dd') AS b").to_pylist() == \
        [{"a": None, "b": dt.date(2024, 2, 29)}]


def test_try_to_date_is_not_a_spark_3_5_built_in():
    listed = {f["name"] for f in json.loads(Path(sql.__file__).with_name("spark_builtins.json").read_text())}
    assert "try_to_date" not in listed and "to_date" in listed


def test_a_notebook_spark_sql_cell_gets_the_same_refusal():
    out = Kernel().run("spark.sql(\"SELECT try_to_date('abc') AS a\").collect()")
    assert out["status"] == "error"
    assert "try_to_date isn't a Spark 3.5 function" in json.dumps(out)


@pytest.mark.parametrize(("code", "want"), [
    ("SELECT try_to_date('abc')", None),
    ("SELECT try_to_date('2016-12-31')", "date:2016-12-31"),
    ("SELECT try_to_date('31/12/2016', 'dd/MM/yyyy')", "date:2016-12-31"),
    ("SELECT try_to_date(NULL)", None),
])
def test_databricks_sql_keeps_answering_try_to_date(code, want):
    assert databricks_coverage.run(code, databricks_coverage.connect()) == [[want]]
