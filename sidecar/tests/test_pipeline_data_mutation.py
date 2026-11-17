"""Lookup and Get Metadata in local pipeline runs: what mutation testing (batch 83,
docs/quality/mutation-testing.md) showed no test checked. Fabric's 4 MB Lookup limit at its exact
edge, values only known at run time, the activity named in a check's message, which runs sign in
first, metadata that needs no table read, and a missing file. A fake engine service stands in for
OneLake; nothing is read from a tenant."""

from __future__ import annotations

import json

import pyarrow as pa
import pytest
from deltalake import write_deltalake

from fabric_desktop import pipeline_data as pd_
from fabric_desktop.orchestration.adapters import ActivityFailed
from fabric_desktop.orchestration.definition import Activity

LS = {"name": "LH_Sales", "properties": {"type": "Lakehouse", "typeProperties": {"workspaceId": "ws1", "artifactId": "lh1"}}}


def act(type_, name="A", **tp):
    return Activity(name=name, type=type_, path="$.properties.activities[0]", raw={"typeProperties": tp})


def ds(kind="LakehouseTable", ls=LS, **tp):
    return {"linkedService": ls, "type": kind, "typeProperties": tp}


class FakeSvc:
    def __init__(self, root, pins=None, file=None):
        self.root, self.pins, self.file = root, pins or {}, file

    def _schemas_enabled(self, ws, lh):
        return True

    def _proof_tables(self, ref):
        pins = self.pins
        return type("T", (), {"versions": lambda self: pins})()

    def _proof_location(self, ref):
        return str(self.root), lambda: {}

    def _pipeline_file_info(self, ref, target):
        return self.file


def reader(tmp_path, **kw):
    return pd_.LocalData(FakeSvc(tmp_path, **kw), "run-1")


def _fails(fn, code):
    with pytest.raises(ActivityFailed) as raised:
        fn()
    assert raised.value.error["errorCode"] == code
    return str(raised.value)


def test_the_lookup_limit_is_exactly_4_mb(tmp_path):
    assert pd_.LOOKUP_BYTES == 4 * 1024 * 1024
    overhead = len(json.dumps({"firstRow": {"s": ""}}).encode())
    at_limit = "x" * (4 * 1024 * 1024 - overhead)
    write_deltalake(str(tmp_path / "Tables" / "dbo" / "fits"), pa.table({"s": [at_limit]}))
    write_deltalake(str(tmp_path / "Tables" / "dbo" / "over"), pa.table({"s": [at_limit + "x"]}))
    r = reader(tmp_path, pins={"dbo.fits": 0, "dbo.over": 0})
    assert r.lookup(act("Lookup"), {"datasetSettings": ds(table="fits")}) == {"firstRow": {"s": at_limit}}
    assert "larger than 4 MB" in _fails(
        lambda: r.lookup(act("Lookup"), {"datasetSettings": ds(table="over")}), "LocalLookupTooLarge")


def test_a_connection_type_known_only_at_run_time_passes_the_check(tmp_path):
    ls = {"name": "LH", "properties": {"type": "@pipeline().parameters.kind",
                                       "typeProperties": {"workspaceId": "ws1", "artifactId": "lh1"}}}
    r = reader(tmp_path)
    assert r.check(act("Lookup", datasetSettings=ds(ls=ls, table="orders"))) == []
    ls["properties"]["type"] = {"type": "Expression", "value": "@pipeline().parameters.kind"}
    assert r.check(act("Lookup", datasetSettings=ds(ls=ls, table="orders"))) == []
    ls["properties"]["type"] = "@@literal"  # @@ is text that starts with @, not an expression
    (p,) = r.check(act("Lookup", datasetSettings=ds(ls=ls, table="orders")))
    assert p["code"] == "data_not_lakehouse"


@pytest.mark.parametrize(("type_", "what"), [("Lookup", "Lookup"), ("GetMetadata", "Get Metadata")])
def test_the_check_names_the_activity_kind(tmp_path, type_, what):
    sql = {"properties": {"type": "AzureSqlDatabase"}}
    (p,) = reader(tmp_path).check(act(type_, datasetSettings=ds(ls=sql)))
    assert p["message"] == f"A reads AzureSqlDatabase; {what} runs on this PC only on a lakehouse. Give a mock for it."


def test_only_runs_with_a_lakehouse_read_sign_in_first(tmp_path):
    r = reader(tmp_path)
    r.scripts.on_lakehouse = lambda a: a.name == "OnLake"
    assert r.lakehouses([act("Wait"), act("Lookup")]) is True
    assert r.lakehouses([act("GetMetadata")]) is True
    assert r.lakehouses([act("Script", name="OnLake")]) is True
    assert r.lakehouses([act("Script", name="OnSql"), act("Wait")]) is False
    assert r.lakehouses([act("Wait", name="OnLake")]) is False
    assert r.lakehouses([]) is False


def test_exists_and_name_alone_dont_read_the_table(tmp_path):
    # The table is pinned but not on disk: reading it would fail, so these fields mustn't read it.
    r = reader(tmp_path, pins={"dbo.orders": 3})
    out = r.metadata(act("GetMetadata"), {"datasetSettings": ds(table="orders"), "fieldList": ["exists", "itemName"]})
    assert out == {"exists": True, "itemName": "orders"}
    assert "couldn't read its lakehouse" in _fails(
        lambda: r.metadata(act("GetMetadata"), {"datasetSettings": ds(table="orders"), "fieldList": ["columnCount"]}),
        "LocalDataReadFailed")


def test_table_structure_and_column_count_each_read_the_schema(tmp_path):
    write_deltalake(str(tmp_path / "Tables" / "dbo" / "orders"), pa.table({"id": [1], "note": ["x"]}))
    r = reader(tmp_path, pins={"dbo.orders": 0})
    meta = lambda fields: r.metadata(act("GetMetadata"), {"datasetSettings": ds(table="orders"), "fieldList": fields})  # noqa: E731
    assert meta(["columnCount"]) == {"columnCount": 2}
    assert meta(["structure"]) == {"structure": [{"name": "id", "type": "Int64"}, {"name": "note", "type": "String"}]}


def test_a_missing_file_fails_unless_exists_is_asked(tmp_path):
    r = reader(tmp_path, file=None)
    settings = ds(kind="Binary", location={"folderPath": "raw", "fileName": "a.csv"})
    assert r.metadata(act("GetMetadata"), {"datasetSettings": settings, "fieldList": ["exists"]}) == {"exists": False}
    assert "Files/raw/a.csv isn't in LH_Sales" in _fails(
        lambda: r.metadata(act("GetMetadata"), {"datasetSettings": settings, "fieldList": ["itemName"]}),
        "LocalDataNotFound")


def test_a_connection_without_a_type_is_named_a_connection(tmp_path):
    (p,) = reader(tmp_path).check(act("Lookup", datasetSettings=ds(ls={"properties": {}})))
    assert p["message"] == "A reads a connection; Lookup runs on this PC only on a lakehouse. Give a mock for it."


def test_a_dataset_without_a_type_is_named_a_dataset(tmp_path):
    (p,) = reader(tmp_path).check(act("GetMetadata", datasetSettings=ds(kind=None)))
    assert p["message"] == "A reads a dataset dataset, which Get Metadata on this PC can't read. Give a mock for it."


def test_a_missing_table_without_a_schema_is_named_in_dbo(tmp_path):
    r = reader(tmp_path)
    assert "A: table dbo.gone isn't in LH_Sales." in _fails(
        lambda: r.lookup(act("Lookup"), {"datasetSettings": ds(table="gone")}), "LocalDataNotFound")
    assert "A: table dbo.gone isn't in LH_Sales." in _fails(
        lambda: r.metadata(act("GetMetadata"), {"datasetSettings": ds(table="gone"), "fieldList": ["itemName"]}),
        "LocalDataNotFound")
    assert "A: table sales.gone isn't in LH_Sales." in _fails(
        lambda: r.lookup(act("Lookup"), {"datasetSettings": ds(table="gone", schema="sales")}), "LocalDataNotFound")
