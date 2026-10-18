"""A write to a table outside the Fabric-safe Delta profile is refused before it commits.

Checking only afterwards called committed rows a failed write, and every rerun wrote them again.
"""

import logging

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import delta_profile, publish
from fabric_desktop.meta.store import LocalStore

OUTSIDE = {"delta.checkpointPolicy": "v2"}


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path, "dev")


def outside_table(store: LocalStore) -> str:
    uri = store.uri("LH_Bronze", "dbo", "events")
    write_deltalake(uri, pa.table({"id": [1], "v": ["a"]}), configuration=OUTSIDE)
    return uri


def rows(*ids: int) -> pl.DataFrame:
    return pl.DataFrame({"id": list(ids), "v": ["b"] * len(ids)})


@pytest.mark.parametrize("call", [
    lambda s: s.write("LH_Bronze", "dbo", "events", rows(2), mode="append"),
    lambda s: s.write("LH_Bronze", "dbo", "events", rows(2), mode="overwrite"),
    lambda s: s.write("LH_Bronze", "dbo", "events", rows(2), mode="overwrite", read_version=0),
    lambda s: s.write_stream("LH_Bronze", "dbo", "events", rows(2).to_arrow().to_reader(), read_version=0),
    lambda s: s.upsert("LH_Bronze", "dbo", "events", rows(1, 2), on=["id"]),
    lambda s: s.merge_insert("LH_Bronze", "dbo", "events", rows(2), on=["id"]),
], ids=["append", "overwrite", "overwrite_at_version", "stream", "upsert", "merge_insert"])
def test_a_write_to_a_table_outside_the_profile_changes_nothing(store, call):
    uri = outside_table(store)
    with pytest.raises(delta_profile.ProfileViolation):
        call(store)
    table = DeltaTable(uri)
    assert table.version() == 0
    assert table.to_pyarrow_table().column("id").to_pylist() == [1]


def test_publish_refuses_before_writing(tmp_path):
    uri = str(tmp_path / "events")
    write_deltalake(uri, pa.table({"id": [1]}), configuration=OUTSIDE)

    class Ref:  # publish() only needs the table's address
        pass
    ref = Ref()
    ref.uri = uri
    with pytest.raises(delta_profile.ProfileViolation):
        publish.publish(pa.table({"id": [2]}), ref, "append", "token")
    assert DeltaTable(uri).version() == 0


def test_writes_inside_the_profile_still_work(store):
    assert store.write("LH_Bronze", "dbo", "events", rows(1)) == 0
    assert store.write("LH_Bronze", "dbo", "events", rows(2), mode="append") == 1
    version, _ = store.upsert("LH_Bronze", "dbo", "events", rows(2, 3), on=["id"])
    assert version == 2
    assert sorted(store.read("LH_Bronze", "dbo", "events")["id"].to_list()) == [1, 2, 3]


def test_a_table_taken_outside_by_the_write_is_reported_not_failed(tmp_path, caplog):
    uri = str(tmp_path / "t")
    write_deltalake(uri, pa.table({"id": [1]}), configuration=OUTSIDE)
    with caplog.at_level(logging.WARNING):
        found = delta_profile.note_after_write(DeltaTable(uri))
    assert found and "committed" in caplog.text


def test_no_table_yet_is_fine(tmp_path):
    delta_profile.check_target(str(tmp_path / "missing"))
