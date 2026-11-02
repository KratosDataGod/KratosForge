"""Characterization tests for audit findings EA-003 and EA-004 (docs/audits/REMEDIATION_BACKLOG.md): local Spark
must give Spark's values and Delta types, or refuse. Each is xfail(strict=True) on main at b75dcf5; the fixing PR
removes the marker. Drop into sidecar/tests/. Local Delta tables only."""

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
    write_deltalake(str(root / "dbo" / "seed"), pa.table({"id": [1]}))
    yield root
    lakehouse.attach(None)


def run(code):
    out = Kernel().run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    return out["status"], text


@pytest.mark.parametrize("code", [
    "print(spark.sql('SELECT CAST(3.7 AS INT) a, CAST(-3.7 AS INT) b').collect())",
    "from pyspark.sql import functions as F\n"
    "print(spark.createDataFrame([(3.7,), (-3.7,)], 'x double').select(F.col('x').cast('int').alias('a')).collect())",
])
def test_float_to_int_casts_truncate_as_in_spark(lake, code):
    status, text = run(code)
    assert status == "ok", text
    assert "4" not in text and "3" in text


def test_sum_of_ints_is_written_as_bigint(lake):
    status, text = run("from pyspark.sql import functions as F\n"
                       "spark.createDataFrame([('k', 1), ('k', 2)], 'k string, n int').groupBy('k')"
                       ".agg(F.sum('n').alias('t')).write.mode('overwrite').saveAsTable('dbo.sums')")
    assert status == "ok", text
    assert DeltaTable(str(lake / "dbo" / "sums")).to_pyarrow_table().schema.field("t").type == pa.int64()


def test_decimal_division_stays_decimal(lake):
    status, text = run("spark.sql('SELECT CAST(10 AS DECIMAL(10,2)) / CAST(3 AS DECIMAL(10,2)) AS q')"
                       ".write.mode('overwrite').saveAsTable('dbo.q')")
    assert status == "ok", text
    assert DeltaTable(str(lake / "dbo" / "q")).to_pyarrow_table().schema.field("q").type == pa.decimal128(23, 13)


@pytest.mark.parametrize("expr", ["F.col('s').cast('timestamp')", "F.col('s').cast(TimestampType())",
                                  "F.expr('CAST(s AS TIMESTAMP)')"])
def test_a_cast_to_timestamp_is_written_as_delta_timestamp_not_ntz(lake, expr):
    status, text = run("from pyspark.sql import functions as F\n"
                       "from pyspark.sql.types import TimestampType\n"
                       "spark.createDataFrame([('2024-01-02 03:04:05',)], 's string')"
                       f".select({expr}.alias('ts')).write.mode('overwrite').saveAsTable('dbo.stamps')")
    assert status == "ok", text
    table = DeltaTable(str(lake / "dbo" / "stamps"))
    field = next(f for f in table.schema().fields if f.name == "ts")
    assert str(field.type.type) == "timestamp"
    assert table.to_pyarrow_table().column("ts").to_pylist()[0].isoformat() == "2024-01-02T03:04:05+00:00"
