"""df.write and SQL INSERT on this PC never drop an option and write more than asked (QA-001).

Each test writes real local Delta tables through lakehouse.write_table, so what is checked is
the table afterwards, not which call was made."""

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, onelake
from fabric_desktop import publish as publish_mod
from fabric_desktop.notebook import Kernel


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    write_deltalake(str(root / "dbo" / "sales"), pa.table({"id": [1, 2, 3], "region": ["EU", "US", "EU"]}))
    yield root
    lakehouse.attach(None)


def rows(lake, table="sales"):
    return sorted(DeltaTable(str(lake / "dbo" / table)).to_pyarrow_table().to_pylist(), key=lambda r: r["id"])


def run(code):
    out = Kernel().run("df = spark.createDataFrame([(9, 'EU')], ['id', 'region'])\n" + code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    return out["status"], text


ORIGINAL = [{"id": 1, "region": "EU"}, {"id": 2, "region": "US"}, {"id": 3, "region": "EU"}]


@pytest.mark.parametrize("option, value", [("replaceWhere", "region = 'EU'"), ("partitionOverwriteMode", "dynamic"),
                                           ("path", "abfss://elsewhere"), ("txnAppId", "x")])
def test_an_option_that_isnt_honored_is_refused_and_the_table_is_untouched(lake, option, value):
    status, text = run(f"df.write.mode('overwrite').option({option!r}, {value!r}).saveAsTable('dbo.sales')")
    assert status == "error" and option.lower() in text.lower().replace(" ", "")
    assert rows(lake) == ORIGINAL


@pytest.mark.parametrize("setter", [
    "spark.conf.set('spark.sql.sources.partitionOverwriteMode', 'dynamic')",
    "spark.conf.set('spark.sql.sources.partitionOverwriteMode', 'DYNAMIC')",
    "spark.sql(\"SET spark.sql.sources.partitionOverwriteMode=dynamic\")",
    "from pyspark.sql import SparkSession\n"
    "SparkSession.builder.config('spark.sql.sources.partitionOverwriteMode', 'dynamic').getOrCreate()",
])
def test_dynamic_partition_overwrite_set_on_the_session_is_refused(lake, setter):
    # Fabric would overwrite only region EU; here the whole table would go, so the cell must stop.
    status, text = run(setter + "\ndf.write.mode('overwrite').partitionBy('region').saveAsTable('dbo.sales')")
    assert status == "error" and "partitionoverwritemode" in text.lower().replace(" ", "")
    assert rows(lake) == ORIGINAL
    assert run("spark.conf.get('spark.sql.sources.partitionOverwriteMode', 'static')")[1] == "'static'"


def test_an_overwrite_is_refused_when_the_session_conf_says_dynamic(lake):
    # Whatever stores spark.conf, the writer reads it before an overwrite.
    status, text = run("spark.conf._set['spark.sql.sources.partitionOverwriteMode'] = 'dynamic'\n"
                       "df.write.mode('overwrite').saveAsTable('dbo.sales')")
    assert status == "error" and "spark.conf.unset" in text
    assert rows(lake) == ORIGINAL
    status, _ = run("spark.conf.unset('spark.sql.sources.partitionOverwriteMode')\n"
                    "spark.conf.set('spark.sql.sources.partitionOverwriteMode', 'static')\n"
                    "df.write.mode('append').saveAsTable('dbo.sales')")
    assert status == "ok" and len(rows(lake)) == 4


def test_options_given_as_keywords_are_checked_too(lake):
    status, _ = run("df.write.saveAsTable('dbo.sales', mode='overwrite', replaceWhere=\"region = 'EU'\")")
    assert status == "error" and rows(lake) == ORIGINAL
    status, _ = run("df.write.options(replaceWhere=\"region = 'EU'\").mode('overwrite').saveAsTable('dbo.sales')")
    assert status == "error" and rows(lake) == ORIGINAL


def test_overwrite_keeps_the_schema_unless_overwriteSchema(lake):
    new = "spark.createDataFrame([(1, 2.5)], ['id', 'amount']).write.mode('overwrite')"
    status, text = run(f"{new}.saveAsTable('dbo.sales')")
    assert status == "error" and "overwriteSchema" in text
    assert rows(lake) == ORIGINAL
    status, text = run(f"{new}.option('overwriteSchema', 'true').saveAsTable('dbo.sales')")
    assert status == "ok", text
    assert rows(lake) == [{"id": 1, "amount": 2.5}]


@pytest.mark.parametrize("data, mode, kind", [
    ("[(1.7, 'EU')], ['id', 'region']", "overwrite", "id is a floating-point number here but a whole number"),
    ("[(1, 5)], ['id', 'region']", "overwrite", "region is a whole number here but text"),
    ("[(1.7, 'EU')], ['id', 'region']", "append", "id is a floating-point number here but a whole number"),
])
def test_a_column_type_change_is_refused_unless_overwriteSchema(lake, data, mode, kind):
    """HD-040 (DI-8): Delta in Fabric refuses a changed column type; writing it here would cast
    silently, and 1.7 would land in the table as 1."""
    write = f"spark.createDataFrame({data}).write.mode({mode!r})"
    status, text = run(f"{write}.saveAsTable('dbo.sales')")
    assert status == "error" and kind in text and "Nothing was written" in text
    assert rows(lake) == ORIGINAL
    status, text = run(f"{write}.option('mergeSchema', 'true').saveAsTable('dbo.sales')")
    assert status == "error" and kind in text and rows(lake) == ORIGINAL  # mergeSchema only adds columns
    if mode == "overwrite":
        status, text = run(f"{write}.option('overwriteSchema', 'true').saveAsTable('dbo.sales')")
        assert status == "ok", text
        assert len(rows(lake)) == 1


def test_a_narrower_type_of_the_same_kind_still_writes(lake):
    status, text = run("spark.sql('SELECT CAST(4 AS INT) AS id, \\'US\\' AS region')"
                       ".write.mode('append').saveAsTable('dbo.sales')")
    assert status == "ok", text
    assert rows(lake)[-1] == {"id": 4, "region": "US"}


def test_same_columns_overwrite_as_before(lake):
    status, text = run("df.write.mode('overwrite').saveAsTable('dbo.sales')")
    assert status == "ok", text
    assert rows(lake) == [{"id": 9, "region": "EU"}]


def test_mergeSchema_adds_a_column_on_append(lake):
    code = "spark.createDataFrame([(4, 'US', 7.0)], ['id', 'region', 'amount']).write.mode('append')"
    status, text = run(f"{code}.saveAsTable('dbo.sales')")
    assert status == "error" and "mergeSchema" in text and rows(lake) == ORIGINAL
    status, text = run(f"{code}.option('mergeSchema', True).saveAsTable('dbo.sales')")
    assert status == "ok", text
    assert rows(lake)[-1] == {"id": 4, "region": "US", "amount": 7.0} and rows(lake)[0]["amount"] is None


def test_insertInto_appends_by_position_and_keeps_the_schema(lake):
    status, text = run("spark.createDataFrame([(5, 'APAC')], ['n', 'r']).write.insertInto('dbo.sales')")
    assert status == "ok", text
    assert rows(lake) == ORIGINAL + [{"id": 5, "region": "APAC"}]
    status, text = run("spark.createDataFrame([(6, 'US')], ['n', 'r']).write.insertInto('dbo.sales', overwrite=True)")
    assert status == "ok", text
    assert rows(lake) == [{"id": 6, "region": "US"}]


def test_insertInto_needs_an_existing_table_with_as_many_columns(lake):
    status, text = run("df.write.insertInto('dbo.missing')")
    assert status == "error" and "saveAsTable" in text
    status, text = run("spark.createDataFrame([(1,)], ['id']).write.insertInto('dbo.sales')")
    assert status == "error" and "position" in text and rows(lake) == ORIGINAL


@pytest.mark.parametrize("sql", ["INSERT OVERWRITE dbo.sales PARTITION (region = 'EU') SELECT 9 AS id",
                                 "INSERT INTO dbo.sales REPLACE WHERE region = 'EU' SELECT 9 AS id, 'EU' AS region"])
def test_sql_insert_into_part_of_a_table_is_refused(lake, sql):
    out = Kernel().run(f"spark.sql({sql!r})")
    assert out["status"] == "error"
    assert rows(lake) == ORIGINAL


def test_sql_insert_matches_columns_by_position_like_spark(lake):
    k = Kernel()
    out = k.run("spark.sql(\"INSERT INTO dbo.sales SELECT 7 AS region_code, 'NA' AS id\")")
    assert out["status"] == "ok", out
    assert rows(lake)[-1] == {"id": 7, "region": "NA"}
    out = k.run("spark.sql(\"INSERT INTO dbo.sales (region, id) SELECT 'SA', 8\")")
    assert out["status"] == "ok", out
    assert rows(lake)[-1] == {"id": 8, "region": "SA"}
    out = k.run("spark.sql(\"INSERT OVERWRITE dbo.sales SELECT 1 AS a, 'x' AS b\")")
    assert out["status"] == "ok", out
    assert rows(lake) == [{"id": 1, "region": "x"}]


def test_layout_options_are_accepted_with_a_note(lake):
    status, text = run("df.write.mode('append').option('optimizeWrite', 'true').saveAsTable('dbo.sales')")
    assert status == "ok" and "file layout" in text
    assert len(rows(lake)) == 4


def test_csv_files_keep_the_header_option_and_refuse_others(lake, tmp_path):
    out = tmp_path / "out.csv"
    status, text = run(f"df.write.option('header', 'false').csv({str(out)!r})")
    assert status == "ok", text
    assert out.read_text().strip() == "9,EU"
    status, text = run(f"df.write.option('escapeQuotes', 'false').csv({str(tmp_path / 'b.csv')!r})")
    assert status == "error" and "escapequotes" in text.lower()


def test_read_schema_is_applied_or_refused(tmp_path):
    data = tmp_path / "p.csv"
    data.write_bytes(b"id,amount\n1,2.50\n2,oops\n")
    k = Kernel()
    out = k.run("from pyspark.sql.types import StructType, StructField, IntegerType, DoubleType\n"
                "s = StructType([StructField('key', IntegerType()), StructField('value', DoubleType())])\n"
                f"spark.read.schema(s).csv({str(data)!r}, header=True).collect()")
    assert out["status"] == "ok", out
    # Names and types from the schema, by position; a value that doesn't fit is null (PERMISSIVE).
    assert out["outputs"][-1]["text"] == "[Row(key=1, value=2.5), Row(key=2, value=None)]"
    out = k.run("spark.read.schema(s).table('dbo.sales')")
    assert out["status"] == "error" and "cast the columns after reading" in str(out["outputs"])
    out = k.run(f"spark.read.schema('id INT').csv({str(data)!r})")
    assert out["status"] == "error" and "StructType" in str(out["outputs"])


def test_a_table_written_in_one_cell_is_read_as_it_is_now_in_the_next(lake):
    # spark kept each table as it was when first read, so a later read missed the session's own writes.
    k = Kernel()
    count = lambda: k.run("print(spark.table('dbo.sales').count(), spark.sql('SELECT count(*) FROM dbo.sales').first()[0])")
    assert count()["outputs"][-1]["text"].split() == ["3", "3"]
    assert k.run("spark.createDataFrame([(9, 'EU')], ['id', 'region']).write.mode('append').saveAsTable('dbo.sales')")["status"] == "ok"
    assert count()["outputs"][-1]["text"].split() == ["4", "4"]
    # INSERT ... VALUES writes through the lakehouse too, rather than failing on the registered table.
    out = k.run("spark.sql(\"INSERT INTO dbo.sales VALUES (10, 'US'), (11, 'EU')\")")
    assert out["status"] == "ok", out
    assert count()["outputs"][-1]["text"].split() == ["6", "6"]
    assert [r["id"] for r in rows(lake)] == [1, 2, 3, 9, 10, 11]
    # Time travel still reads the version asked for.
    out = k.run("print(spark.sql('SELECT count(*) FROM dbo.sales VERSION AS OF 0').first()[0])")
    assert out["outputs"][-1]["text"].strip() == "3", out


def test_older_versions_views_are_dropped_unless_a_dataframe_still_reads_them(lake):
    # Each new version of a table registered another view on the session's connection, and none were dropped.
    k = Kernel()
    views = "len(spark._views)"
    last = lambda out: out["outputs"][-1]["text"].strip().splitlines()[-1].split()
    out = k.run("before = spark.table('dbo.sales')\nbefore.createOrReplaceTempView('old')\n"
                "for i in range(5):\n"
                "    spark.createDataFrame([(100 + i, 'EU')], ['id', 'region']).write.mode('append').saveAsTable('dbo.sales')\n"
                "    n = spark.table('dbo.sales').count()\n"
                f"print(n, before.count(), spark.table('old').count(), {views})")
    assert out["status"] == "ok", out
    # 8 rows now; `before` and the temp view still read the version they were made from, so its view
    # stays, with the newest one; the four in between are dropped.
    assert last(out) == ["8", "3", "3", "2"]
    out = k.run("del before\nimport gc; gc.collect()\nspark.sql('DROP VIEW old')\n"
                "spark.createDataFrame([(200, 'US')], ['id', 'region']).write.mode('append').saveAsTable('dbo.sales')\n"
                f"print(spark.table('dbo.sales').count(), {views})")
    assert last(out) == ["9", "1"], out


def test_drop_and_truncate_of_a_lakehouse_table_stop_with_a_clear_message(lake):
    k = Kernel()
    out = k.run("spark.sql('DROP TABLE dbo.sales')")
    assert out["status"] == "error" and "DROP TABLE removes dbo.sales from the lakehouse, which runs in Fabric" in str(out), out
    out = k.run("spark.sql('TRUNCATE TABLE sales')")
    assert out["status"] == "error" and "TRUNCATE TABLE empties sales in place, which runs in Fabric" in str(out), out
    assert len(rows(lake)) == 3
    # DROP TABLE IF EXISTS of a table that isn't there changes nothing, and a temp view is still dropped.
    assert k.run("spark.sql('DROP TABLE IF EXISTS dbo.not_there')")["status"] == "ok"
    out = k.run("spark.range(2).createOrReplaceTempView('tmp')\nspark.sql('DROP TABLE tmp')\n"
                "print(spark.catalog.tableExists('tmp'))")
    assert out["status"] == "ok" and out["outputs"][-1]["text"].strip() == "False", out
    # The way round the message works: overwrite with no rows.
    out = k.run("spark.table('dbo.sales').limit(0).write.mode('overwrite').saveAsTable('dbo.sales')\n"
                "print(spark.table('dbo.sales').count())")
    assert out["outputs"][-1]["text"].strip().splitlines()[-1] == "0", out


@pytest.mark.parametrize("code", ["df.write.format('parquet').save()", "df.write.format('csv').save()",
                                  "spark.read.format('csv').load()", "spark.read.format('delta').load()",
                                  "spark.read.load()"])
def test_save_and_load_without_a_path_say_what_they_need(lake, code):
    status, text = run(code)
    assert status == "error"
    assert "Give save a path" in text or "Give load a path" in text
