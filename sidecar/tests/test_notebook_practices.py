"""Notebook best practices: read on this PC, nothing runs."""

import pytest

from fabric_desktop import notebook_practices as np_


def ids(sources, **kw):
    return [f["rule_id"] for f in np_.check(sources, **kw)["findings"]]


def test_secrets_and_ids_in_paths_are_found():
    found = np_.check(['key = "ghp_' + "a" * 36 + '"\n',
                       'df = spark.read.load("abfss://0a1b2c3d-0000-4000-8000-000000000001@onelake.dfs.fabric.microsoft.com/x")'])
    assert [(f["rule_id"], f["cell"], f["line"]) for f in found["findings"]] == [("NB001", 1, 1), ("NB002", 2, 1)]
    assert found["counts"] == {"Error": 1, "Warning": 1, "Info": 0}
    assert ids(['df = spark.read.load("Tables/sales")']) == []


def test_collect_and_topandas_need_a_limit():
    assert ids(["rows = df.collect()", "pdf = df.toPandas()"]) == ["NB003", "NB003"]
    assert ids(["rows = df.limit(10).collect()", "pdf = df.head(5)"]) == []


def test_csv_and_json_reads_need_a_schema():
    assert ids(['a = spark.read.option("header", True).csv("Files/a.csv")',
                'b = spark.read.format("json").load("Files/b")']) == ["NB005", "NB005"]
    assert ids(['a = spark.read.schema(s).csv("Files/a.csv")', 'p = spark.read.parquet("Files/p")',
                'c = pd.read_csv("x.csv")']) == []


def test_select_star_in_sql_cells_and_spark_sql():
    assert ids(["%%sql\nSELECT * FROM sales", 'spark.sql("select distinct * from t")']) == ["NB006", "NB006"]
    assert ids(["%%sql\nSELECT COUNT(*) FROM sales"]) == []


def test_whole_table_overwrite_unless_filtered():
    assert ids(['df.write.mode("overwrite").saveAsTable("t")', 'df.write.saveAsTable("t", mode="overwrite")']) == ["NB007"] * 2
    assert ids(['df.write.mode("overwrite").option("replaceWhere", "d > 1").saveAsTable("t")',
                'df.write.mode("append").saveAsTable("t")']) == []


def test_pip_installs():
    assert ids(["%pip install semantic-link-labs"]) == ["NB009"]
    assert ids(["%pip install -q semantic-link-labs==0.17.1 -r requirements.txt"]) == []
    assert ids(["!pip install --upgrade pandas==2.2.3 numpy"]) == ["NB009"]
    assert ids(["%pip install x==1"], scheduled=True) == ["NB008"]


def test_scheduled_only_rules():
    assert ids(["display(df)"]) == []
    assert ids(["display(df)"], scheduled=True) == ["NB004"]
    assert ids(["display(df.limit(20))"], scheduled=True) == []


def test_writes_in_loops_counts_and_swallowed_errors():
    loop = 'for t in tables:\n    for d in days:\n        df.write.mode("append").saveAsTable(t)\n'
    assert ids([loop]) == ["NB010"]  # nested loops: one finding
    assert ids(["n = df.count()", "m = df.count()", "if df.count():\n    pass"]) == ["NB011"]
    assert ids(["try:\n    go()\nexcept Exception:\n    pass"]) == ["NB012"]


def test_python_udfs_but_not_pandas_udfs_or_user_data_functions():
    assert ids(['@udf("string")\ndef up(s):\n    return s.upper()\n']) == ["NB018"]  # once, not again for the decorator
    assert ids(["@udf\ndef up(s):\n    return s\n"]) == ["NB018"]
    assert ids(["up = F.udf(lambda s: s.upper())", 'spark.udf.register("up", up)']) == ["NB018", "NB018"]
    assert ids(['@pandas_udf("long")\ndef f(s):\n    return s',
                "udf = fn.UserDataFunctions()\n@udf.function()\ndef hello(name: str) -> str:\n    return name",
                'spark.udf.register("f")', "df = df.withColumn('n', F.upper('n'))"]) == []


def test_spark_reading_a_web_address():
    assert ids(['df = spark.read.parquet("https://example.com/a.parquet")',
                'df = spark.read.format("parquet").load("HTTP://example.com/a")']) == ["NB019", "NB019"]
    assert ids(['pdf = pd.read_csv("https://example.com/a.csv")', 'df = spark.read.parquet("Files/a")',
                'df = spark.read.parquet("abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Files/a")']) == []


def test_vacuum_with_short_retention():
    short = ['spark.sql("VACUUM sales RETAIN 0 HOURS")', "%%sql\nSELECT 1;\nvacuum sales retain 24.5 hours",
             'DeltaTable.forName(spark, "t").vacuum(0)', "dt.vacuum(retention_hours=1, enforce_retention_duration=False)"]
    found = np_.check(short)["findings"]
    assert [(f["rule_id"], f["cell"], f["line"]) for f in found] == [("NB020", 1, 1), ("NB020", 2, 3), ("NB020", 3, 1),
                                                                     ("NB020", 4, 1)]
    assert ids(['spark.sql("VACUUM sales RETAIN 168 HOURS")', "%%sql\nVACUUM sales", "dt.vacuum()",
                "dt.vacuum(200)", "dt.vacuum(dry_run=True)", "dt.vacuum(True)"]) == []


def test_magics_and_broken_cells_dont_stop_the_check():
    assert ids(["%run other\nrows = df.collect()"]) == ["NB003"]
    assert ids(["def broken(:\n", "rows = df.collect()"]) == ["NB003"]
    assert ids(["%%spark\nval x = df.collect()"]) == []
    assert ids(["%%pyspark\nrows = df.collect()"]) == ["NB003"]  # a Python cell under its magic


def test_settings_turn_rules_off_and_change_severity():
    out = np_.check(["rows = df.collect()", "pdf = df.toPandas()"],
                    settings={"NB003": {"severity": "Error"}})
    assert {f["severity"] for f in out["findings"]} == {"Error"}
    assert np_.check(["rows = df.collect()"], settings={"NB003": {"enabled": False}})["findings"] == []
    assert ids(["rows = df.collect()"], settings={"NB003": "off"}) == ["NB003"]  # not a dict: ignored


def test_rules_list_and_bad_input():
    listed = np_.rules()
    assert len(listed) == 15 and len({r["id"] for r in listed}) == 15
    assert all(r["kind"] == "notebook" and r["severity"] in np_.SEVERITIES for r in listed)
    with pytest.raises(ValueError):
        np_.check("rows = df.collect()")  # type: ignore[arg-type]
