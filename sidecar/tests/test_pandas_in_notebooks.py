"""pandas is in the engine, as in Fabric and Databricks notebooks: import pandas, toPandas(),
pandas data frames shown as tables, and times with a time zone handed back by DuckDB."""

from fabric_desktop.notebook import Kernel


def test_pandas_works_in_a_notebook_cell():
    k = Kernel()
    out = k.run("import pandas as pd\n"
                "df = spark.createDataFrame([(1, 'EU'), (2, 'US')], ['id', 'region'])\n"
                "pdf = df.toPandas()\n"
                "print(type(pdf).__name__, len(pdf), pd.__version__.split('.')[0])\n"
                "pdf")
    assert out["status"] == "ok", out
    assert out["outputs"][0]["text"].split() == ["DataFrame", "2", "2"]
    assert out["outputs"][1]["type"] == "table"


def test_a_time_with_a_zone_comes_back_from_spark():
    out = Kernel().run("spark.sql(\"SELECT TIMESTAMPTZ '2026-10-20 13:00:00+00' AS t\").collect()[0]['t'].year")
    assert out["status"] == "ok", out
    assert out["outputs"] == [{"type": "text", "text": "2026"}]
