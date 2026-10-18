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
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: str(root / self.schema / self.table)))
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
