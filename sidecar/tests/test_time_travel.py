"""Delta time travel and history from a notebook on this PC: versionAsOf / timestampAsOf,
VERSION AS OF and sales@v3 in SQL, DESCRIBE HISTORY and delta.tables.DeltaTable. All of it only
reads; RESTORE and DeltaTable changes are refused and the table is untouched."""

import time

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, onelake, sql
from fabric_desktop import publish as publish_mod
from fabric_desktop.notebook import Kernel


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    where = str(root / "dbo" / "sales")
    write_deltalake(where, pa.table({"id": [1]}))                   # version 0
    time.sleep(0.05)
    write_deltalake(where, pa.table({"id": [2]}), mode="append")    # version 1
    write_deltalake(where, pa.table({"id": [3]}), mode="overwrite")  # version 2
    yield root
    lakehouse.attach(None)


def run(code):
    out = Kernel().run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    return out["status"], text


def test_lakehouse_reads_a_version_or_a_time(lake):
    assert lakehouse.read_table("dbo.sales")["id"].to_list() == [3]
    assert sorted(lakehouse.read_table("dbo.sales", version=1)["id"].to_list()) == [1, 2]
    first = DeltaTable(str(lake / "dbo" / "sales")).history()[-1]["timestamp"]
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(first / 1000 + 0.01))
    assert lakehouse.read_table("dbo.sales", timestamp=when)["id"].to_list() == [1]
    with pytest.raises(ValueError, match="no version 9"):
        lakehouse.read_table("dbo.sales", version=9)
    with pytest.raises(ValueError, match="not both"):
        lakehouse.read_table("dbo.sales", version=1, timestamp=when)


def test_a_missing_table_is_named_in_plain_words(lake):
    (lake / "tpch" / "region" / "_delta_log").mkdir(parents=True)  # an empty folder reads like OneLake's missing one
    for name in ("tpch.region", "tpch.nation"):
        with pytest.raises(lakehouse.TableNotFoundError) as e:
            lakehouse.open_table(name)
        text = str(e.value)
        assert text.startswith(f"{name} isn't in Sales: there's no Delta table at Tables/{name.replace('.', '/')}. ")
        assert "with Sales attached first" in text and "log segment" not in text and "Os {" not in text


def test_a_replay_records_the_version_a_time_travel_read_used(lake):
    lakehouse.reads_reset()
    assert run("from fabric_desktop import lakehouse\nlakehouse.read_table('dbo.sales', version=1)")[0] == "ok"
    assert [r["version"] for r in lakehouse.reads["tables"].values()] == [1]
    lakehouse.reads_reset()


def test_history_lists_versions_newest_first(lake):
    h = lakehouse.table_history("dbo.sales")
    assert h["version"].to_list() == [2, 1, 0]
    assert h["operation"].to_list()[0] in ("WRITE", "CREATE OR REPLACE TABLE")


def test_spark_and_sql_time_travel(lake):
    status, text = run("spark.read.option('versionAsOf', 0).table('dbo.sales').collect()")
    assert status == "ok" and "id=1" in text and "id=3" not in text
    status, text = run("spark.read.format('delta').option('versionAsOf', 1).load('Tables/dbo/sales').count()")
    assert status == "ok" and text.strip() == "2"
    status, text = run("spark.sql('SELECT count(*) AS n FROM dbo.sales VERSION AS OF 1').collect()")
    assert status == "ok" and "n=2" in text
    status, text = run("spark.sql('SELECT max(id) AS m FROM dbo.sales@v0').collect()")
    assert status == "ok" and "m=1" in text
    # Both versions in one query, as when comparing them.
    status, text = run("spark.sql('SELECT (SELECT count(*) FROM dbo.sales VERSION AS OF 1) AS old, "
                       "(SELECT count(*) FROM dbo.sales) AS new').collect()")
    assert status == "ok" and "old=2" in text and "new=1" in text
    assert sql.run("SELECT count(*) AS n FROM dbo.sales VERSION AS OF 1").to_pylist() == [{"n": 2}]


def test_describe_history_and_delta_tables(lake):
    status, text = run("spark.sql('DESCRIBE HISTORY dbo.sales').select('version').collect()")
    assert status == "ok" and text.index("version=2") < text.index("version=0")
    status, text = run("from delta.tables import DeltaTable\n"
                       "t = DeltaTable.forName(spark, 'dbo.sales')\n"
                       "print(t.history(1).collect()[0]['version'], t.toDF().count(), t.detail().collect()[0]['version'])")
    assert status == "ok" and text.split() == ["2", "1", "2"], text
    assert sql.run("DESCRIBE HISTORY dbo.sales").num_rows == 3


@pytest.mark.parametrize("code", ["spark.sql('RESTORE TABLE dbo.sales TO VERSION AS OF 0')",
                                  "spark.sql('/* undo */ RESTORE TABLE dbo.sales TO VERSION AS OF 0')",
                                  "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').restoreToVersion(0)",
                                  "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').vacuum()"])
def test_changes_through_time_travel_are_refused_and_the_table_is_untouched(lake, code):
    status, text = run(code)
    assert status == "error" and "Fabric" in text
    assert DeltaTable(str(lake / "dbo" / "sales")).version() == 2
