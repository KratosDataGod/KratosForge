"""Tables written from this PC get Spark's types for DuckDB's 128-bit integers (EA-004)."""

from __future__ import annotations

import duckdb
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, onelake
from fabric_desktop import publish as publish_mod
from fabric_desktop.notebook import Kernel
from fabric_desktop.spark_local import _spark_types


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
    return out["status"], "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])


def test_sum_of_bigints_in_spark_sql_is_written_as_bigint(lake):
    status, text = run("spark.sql('SELECT k, sum(n) AS t, count(*) AS c FROM VALUES (\\'a\\', 1L), (\\'a\\', 2L) "
                       "AS v(k, n) GROUP BY k').write.mode('overwrite').saveAsTable('dbo.sums')")
    assert status == "ok", text
    schema = DeltaTable(str(lake / "dbo" / "sums")).to_pyarrow_table().schema
    assert schema.field("t").type == pa.int64() and schema.field("c").type == pa.int64()


def test_a_sum_too_big_for_bigint_stops_the_write(lake):
    status, text = run("spark.sql('SELECT sum(n) AS t FROM VALUES (9223372036854775807L), (1L) AS v(n)')"
                       ".write.mode('overwrite').saveAsTable('dbo.big')")
    assert status == "error" and "too big for a long (BIGINT)" in text
    assert not (lake / "dbo" / "big").exists()


def test_only_128_bit_columns_change_and_odd_names_are_quoted():
    con = duckdb.connect()
    rel = con.sql('SELECT CAST(5 AS HUGEINT) AS "we""ird", CAST(1.5 AS DECIMAL(38,0)) AS d, \'x\' AS "it\'s"')
    out = _spark_types(rel)
    assert [str(t) for t in out.types] == ["BIGINT", "DECIMAL(38,0)", "VARCHAR"]
    assert out.columns == ['we"ird', "d", "it's"]
    plain = con.sql("SELECT 1 AS a")
    assert _spark_types(plain) is plain
