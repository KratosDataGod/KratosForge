"""Delta change data feed reads on this PC (WR-317), as Databricks documents them:
https://docs.databricks.com/aws/en/delta/delta-change-data-feed (readChangeFeed with startingVersion /
startingTimestamp and endingVersion / endingTimestamp; _change_type, _commit_version, _commit_timestamp)."""

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import delta_cdf, lakehouse, onelake
from fabric_desktop import publish as publish_mod
from fabric_desktop.notebook import Kernel


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    path = str(root / "dbo" / "orders")
    write_deltalake(path, pa.table({"id": [1, 2, 3], "v": ["a", "b", "c"]}),
                    configuration={"delta.enableChangeDataFeed": "true"})  # version 0
    table = DeltaTable(path)
    table.update({"v": "'B'"}, predicate="id = 2")  # 1
    table.delete("id = 3")  # 2
    write_deltalake(path, pa.table({"id": [4], "v": ["d"]}), mode="append")  # 3
    write_deltalake(str(root / "dbo" / "plain"), pa.table({"id": [1]}))
    yield root
    lakehouse.attach(None)


def _changes(kernel, reader):
    out = kernel.run(f"rows = sorted(({reader}).select('id', 'v', '_change_type', '_commit_version').collect(), "
                     "key=lambda r: (r[3], r[0], r[2]))\n[tuple(r) for r in rows]")
    assert out["status"] == "ok", out
    return out["outputs"][-1]["text"]


def test_read_changes_from_a_version(lake):
    k = Kernel()
    text = _changes(k, "spark.read.option('readChangeFeed', 'true').option('startingVersion', 1).table('orders')")
    assert text == ("[(2, 'B', 'update_postimage', 1), (2, 'b', 'update_preimage', 1), (3, 'c', 'delete', 2), "
                    "(4, 'd', 'insert', 3)]")


def test_read_changes_between_versions_with_format_delta(lake):
    k = Kernel()
    text = _changes(k, "spark.read.format('delta').option('readChangeFeed', 'true').option('startingVersion', 0)"
                       ".option('endingVersion', 0).table('orders')")
    assert text == "[(1, 'a', 'insert', 0), (2, 'b', 'insert', 0), (3, 'c', 'insert', 0)]"


def test_read_changes_by_path_and_the_column_types(lake):
    k = Kernel()
    path = (lake / "dbo" / "orders").as_posix()
    out = k.run(f"df = spark.read.format('delta').option('readChangeFeed', 'true').option('startingVersion', 2)"
                f".load({path!r})\n[(f.name, f.dataType.simpleString()) for f in df.schema.fields]")
    assert out["status"] == "ok", out
    assert out["outputs"][-1]["text"] == ("[('id', 'bigint'), ('v', 'string'), ('_change_type', 'string'), "
                                          "('_commit_version', 'bigint'), ('_commit_timestamp', 'timestamp')]")


def test_starting_timestamp(lake):
    k = Kernel()
    text = _changes(k, "spark.read.option('readChangeFeed', 'true').option('startingTimestamp', '2000-01-01')"
                       ".table('orders')")
    assert text.count("'insert', 0") == 3


def test_a_read_without_a_start_stops_as_databricks_does(lake):
    out = Kernel().run("spark.read.option('readChangeFeed', 'true').table('orders')")
    assert out["outputs"][-1]["evalue"].startswith("[DELTA_NO_START_FOR_CDC_READ] No startingVersion or "
                                                   "startingTimestamp provided for CDC read")


def test_a_table_without_change_data_names_the_property(lake):
    out = Kernel().run("spark.read.option('readChangeFeed', 'true').option('startingVersion', 0).table('plain')")
    evalue = out["outputs"][-1]["evalue"]
    assert evalue.startswith("[DELTA_MISSING_CHANGE_DATA] Error getting change data for range [0 , latest]"), evalue
    assert "delta.enableChangeDataFeed = true" in evalue


def test_the_option_is_never_ignored(lake):
    # Before WR-317 the option was dropped and the whole table came back as if it were the changes.
    out = Kernel().run("spark.read.option('readChangeFeed', 'true').option('startingVersion', 3).table('orders')"
                       ".count()")
    assert out["outputs"][-1]["text"] == "1"


@pytest.mark.parametrize("options, said", [
    ({"readchangefeed": "true", "startingversion": "x"}, "startingVersion must be a whole number"),
    ({"readchangefeed": "true", "startingversion": -1}, "startingVersion can't be negative"),
    ({"readchangefeed": "true", "startingversion": "latest"}, "startingVersion latest is for a stream"),
    ({"readchangefeed": "true", "startingversion": 3, "endingversion": 1}, "[DELTA_INVALID_CDC_RANGE]"),
    ({"readchangefeed": "true", "startingversion": 0, "startingtimestamp": "2026-01-01"},
     "Give startingVersion or startingTimestamp"),
    ({"readchangefeed": "true", "startingtimestamp": "soon"}, "startingTimestamp must be a date"),
])
def test_bad_bounds_are_refused_before_reading(options, said):
    with pytest.raises(ValueError, match=None) as raised:
        delta_cdf.bounds(options)
    assert said in str(raised.value)


def test_timestamps_without_a_zone_are_utc():
    assert delta_cdf.bounds({"startingtimestamp": "2026-10-20 13:00:00"}) == {
        "starting_timestamp": "2026-10-20T13:00:00Z"}
    assert delta_cdf.bounds({"startingtimestamp": "2026-10-20T08:00:00-05:00"}) == {
        "starting_timestamp": "2026-10-20T13:00:00Z"}


def test_asked_reads_true_only():
    assert delta_cdf.asked({"readchangefeed": "true"}) and delta_cdf.asked({"readchangefeed": True})
    assert not delta_cdf.asked({"readchangefeed": "false"}) and not delta_cdf.asked({})


def test_time_travel_with_the_feed_is_refused(lake):
    out = Kernel().run("spark.read.option('readChangeFeed', 'true').option('startingVersion', 0)"
                       ".option('versionAsOf', 1).table('orders')")
    assert "with versionAsOf or timestampAsOf" in out["outputs"][-1]["evalue"]


def test_column_mapped_tables_are_refused(lake):
    path = str(lake / "dbo" / "mapped")
    write_deltalake(path, pa.table({"id": [1]}), configuration={"delta.enableChangeDataFeed": "true"})

    class Mapped:
        def metadata(self):
            class M:
                configuration = {"delta.columnMapping.mode": "name"}
            return M()

    with pytest.raises(NotImplementedError, match="uses column mapping"):
        delta_cdf.read(Mapped(), "mapped", {"readchangefeed": "true", "startingversion": 0})


def test_a_stream_of_changes_is_refused(lake):
    out = Kernel().run("spark.readStream.option('readChangeFeed', 'true').table('orders')")
    assert "A stream of a table's change data feed (readChangeFeed) runs in Fabric" in out["outputs"][-1]["evalue"]


def test_commit_timestamps_collect(lake):
    out = Kernel().run("r = spark.read.option('readChangeFeed', 'true').option('startingVersion', 3).table('orders')"
                       ".collect()[0]\ntype(r['_commit_timestamp']).__name__")
    assert out["outputs"][-1]["text"] == "'datetime'", out


# https://docs.databricks.com/aws/en/sql/language-manual/functions/table_changes
def test_table_changes_in_spark_sql(lake):
    k = Kernel()
    text = _changes(k, "spark.sql(\"SELECT * FROM table_changes('orders', 2, 3) AS c WHERE c._change_type <> 'x'\")")
    assert text == "[(3, 'c', 'delete', 2), (4, 'd', 'insert', 3)]"
    text = _changes(k, "spark.sql(\"SELECT * FROM table_changes('orders', '2000-01-01')\")")
    assert text.count("'insert', 0") == 3


def test_table_changes_needs_literals_and_a_start(lake):
    out = Kernel().run("spark.sql(\"SELECT * FROM table_changes('orders')\")")
    assert "table_changes takes a table name, a start and an optional end" in out["outputs"][-1]["evalue"]
    out = Kernel().run("spark.sql(\"SELECT * FROM table_changes('orders', 1 + 1)\")")
    assert "written as literals" in out["outputs"][-1]["evalue"]
