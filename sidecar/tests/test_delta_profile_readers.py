"""A refused write names the feature and the readers that can't read it (docs/delta-reader-matrix.md),
and the profile still refuses exactly what it refused before.
"""

from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import delta_profile as P
from fabric_desktop.meta.store import LocalStore


@pytest.fixture(autouse=True)
def _conservative(monkeypatch):
    """These cases pin the conservative (Fabric-safe) profile's rules. The default is the modern profile
    since 2026-11-17; test_delta_profile_modern.py covers it."""
    monkeypatch.setattr(P, "DEFAULT_PROFILE", P.CONSERVATIVE)


# Each setting outside the profile, what problems() said about it before readers were named (so the
# check refuses the same tables for the same reasons), and the words the refusal now adds.
CASES = {
    "deletion_vectors": ({"delta.enableDeletionVectors": "true"},
                         ["reader version 3 > 1", "writer version 7 > 2", "delta.enableDeletionVectors=true"],
                         "deletion vectors, which Fabric Python notebooks, pipelines and eventstreams can't read"),
    "column_mapping_name": ({"delta.columnMapping.mode": "name"},
                            ["reader version 2 > 1", "writer version 5 > 2", "delta.columnMapping.mode=name"],
                            ("column mapping by name, which Fabric Python notebooks, pipelines and eventstreams "
                             "can't read")),
    "column_mapping_id": ({"delta.columnMapping.mode": "id"},
                          ["reader version 2 > 1", "writer version 5 > 2", "delta.columnMapping.mode=id"],
                          ("column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, "
                           "Dataflows Gen2, KQL databases, Python notebooks, pipelines and eventstreams can't read")),
    "v2_checkpoints": ({"delta.checkpointPolicy": "v2"},
                       ["delta.checkpointPolicy=v2"],
                       "V2 checkpoints, which users report Fabric's SQL analytics endpoint refusing"),
}


def _table_at(uri: str, config: dict[str, str]) -> DeltaTable:
    write_deltalake(uri, pa.table({"id": [1], "v": ["a"]}), configuration=config)
    return DeltaTable(uri)


@pytest.mark.parametrize("case", CASES, ids=list(CASES))
def test_the_refusal_names_the_feature_and_who_cant_read_it(tmp_path, case):
    config, before, words = CASES[case]
    table = _table_at(str(tmp_path / "t"), config)
    found = P.problems(table)
    assert all(reason in found for reason in before), found
    with pytest.raises(P.ProfileViolation) as refused:
        P.check_table(table)
    text = str(refused.value)
    assert text.startswith("Table is outside the Fabric-safe Delta profile: " + "; ".join(found) + ". It uses ")
    assert words in text


@pytest.mark.parametrize("case", CASES, ids=list(CASES))
@pytest.mark.parametrize("write", [
    lambda s, rows: s.write("LH_Bronze", "dbo", "events", rows, mode="append"),
    lambda s, rows: s.write("LH_Bronze", "dbo", "events", rows, mode="overwrite"),
    lambda s, rows: s.upsert("LH_Bronze", "dbo", "events", rows, on=["id"]),
], ids=["append", "overwrite", "upsert"])
def test_every_feature_outside_the_profile_still_stops_the_write(tmp_path, case, write):
    store = LocalStore(tmp_path, "dev")
    uri = store.uri("LH_Bronze", "dbo", "events")
    _table_at(uri, CASES[case][0])
    with pytest.raises(P.ProfileViolation):
        write(store, pl.DataFrame({"id": [1, 2], "v": ["b", "b"]}))
    table = DeltaTable(uri)
    assert table.version() == 0  # nothing committed
    assert len(table.file_uris()) == 1  # and no data file added


def _stub(reader=1, writer=2, reader_features=None, writer_features=None, config=None):
    protocol = SimpleNamespace(min_reader_version=reader, min_writer_version=writer,
                               reader_features=reader_features, writer_features=writer_features)
    return SimpleNamespace(protocol=lambda: protocol,
                           metadata=lambda: SimpleNamespace(configuration=config or {}))


def test_a_version_alone_is_refused_as_before_with_no_reader_named():
    for table, reason in ((_stub(writer=3), "writer version 3 > 2"), (_stub(reader=2), "reader version 2 > 1")):
        with pytest.raises(P.ProfileViolation) as refused:
            P.check_table(table)
        assert str(refused.value) == "Table is outside the Fabric-safe Delta profile: " + reason


def test_a_feature_named_only_in_the_protocol_still_names_its_readers():
    table = _stub(reader=3, writer=7, reader_features=["v2Checkpoint"], writer_features=["v2Checkpoint"])
    assert P.unreadable_by(table) == [CASES["v2_checkpoints"][2] + " (Microsoft's documentation doesn't say)"]
    both = _stub(reader=3, writer=7, reader_features=["deletionVectors"], writer_features=["deletionVectors"],
                 config={"delta.enableDeletionVectors": "true"})
    assert P.unreadable_by(both) == [CASES["deletion_vectors"][2]]


def test_a_table_inside_the_profile_is_not_refused(tmp_path):
    table = _table_at(str(tmp_path / "t"), {})
    assert P.problems(table) == [] and P.unreadable_by(table) == []
    P.check_table(table)
