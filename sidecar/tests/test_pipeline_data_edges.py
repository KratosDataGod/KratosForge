"""Lookup and Get Metadata in local pipeline runs: the edges the main tests don't reach. The checks
before a run, datasets that aren't a lakehouse table, a dataset without a table, an output that is
too large, fields this PC can't give, and every value and column type a table can hold. A fake engine
service stands in for OneLake; nothing is read from a tenant."""

from __future__ import annotations

import datetime as dt
import decimal
import math

import pyarrow as pa
import pytest
from deltalake import write_deltalake

from fabric_desktop import pipeline_data as pd_
from fabric_desktop.orchestration.adapters import ActivityFailed
from fabric_desktop.orchestration.definition import Activity

LS = {"name": "LH_Sales", "properties": {"type": "Lakehouse", "typeProperties": {"workspaceId": "ws1", "artifactId": "lh1"}}}


def act(type_, **tp):
    return Activity(name="A", type=type_, path="$.properties.activities[0]", raw={"typeProperties": tp})


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


def test_values_are_given_as_fabric_s_json():
    v = {"when": dt.datetime(2026, 1, 2, 3, 4, 5), "day": dt.date(2026, 1, 2), "at": dt.time(1, 2),
         "money": decimal.Decimal("1.50"), "nan": math.nan, "inf": float("inf"), "blob": b"hi",
         "list": [decimal.Decimal("2"), None], "n": 3}
    assert pd_._json_value(v) == {"when": "2026-01-02T03:04:05", "day": "2026-01-02", "at": "01:02:00", "money": 1.5,
                                  "nan": None, "inf": None, "blob": "aGk=", "list": [2.0, None], "n": 3}


@pytest.mark.parametrize("t, name", [
    (pa.bool_(), "Boolean"), (pa.int8(), "SByte"), (pa.int16(), "Int16"), (pa.int32(), "Int32"), (pa.int64(), "Int64"),
    (pa.float32(), "Single"), (pa.float64(), "Double"), (pa.decimal128(10, 2), "Decimal"),
    (pa.timestamp("us"), "DateTime"), (pa.date32(), "Date"), (pa.binary(), "Byte[]"), (pa.large_binary(), "Byte[]"),
    (pa.string(), "String"), (pa.large_string(), "String"), (pa.list_(pa.int32()), "list<item: int32>"),
])
def test_column_types_are_named_as_get_metadata_names_them(t, name):
    assert pd_._type_name(t) == name


def test_a_run_without_lakehouse_reads_asks_for_a_mock(tmp_path):
    r = pd_.LocalData(FakeSvc(tmp_path), "run-1", lakehouse_reads=False)
    (p,) = r.check(act("Lookup", datasetSettings=ds(table="orders")))
    assert p["code"] == "data_needs_engine" and "--read-lakehouse" in p["message"]


@pytest.mark.parametrize("type_, tp, code", [
    ("Lookup", {"datasetSettings": ds(ls={"properties": {"type": "AzureSqlDatabase"}})}, "data_not_lakehouse"),
    ("Lookup", {"datasetSettings": ds(ls={"properties": {"type": "Lakehouse", "typeProperties": {"artifactId": "lh1"}}})},
     "data_workspace_unknown"),
    ("Lookup", {"datasetSettings": ds(table="orders"), "source": {"sqlReaderQuery": "SELECT 1"}}, "data_query"),
    ("Lookup", {"datasetSettings": ds(kind="DelimitedText")}, "data_not_table"),
    ("GetMetadata", {"datasetSettings": ds(kind="AzureSqlTable")}, "data_not_table"),
    ("GetMetadata", {"datasetSettings": ds(table="orders"), "fieldList": ["exists", "size"]}, "data_field"),
])
def test_what_this_pc_cant_read_is_found_before_the_run(tmp_path, type_, tp, code):
    (p,) = reader(tmp_path).check(act(type_, **tp))
    assert p["code"] == code and p["activity"] == "A"


def test_what_this_pc_can_read_passes_the_check(tmp_path):
    r = reader(tmp_path)
    assert r.check(act("Lookup", datasetSettings=ds(table="orders"))) == []
    assert r.check(act("GetMetadata", datasetSettings=ds(kind="Binary"), fieldList=["exists", "childItems"])) == []
    assert r.check(act("GetMetadata", datasetSettings=ds(table="orders"), fieldList="@pipeline().parameters.f")) == []


def _fails(fn, code):
    with pytest.raises(ActivityFailed) as raised:
        fn()
    assert raised.value.error["errorCode"] == code
    return str(raised.value)


def test_a_dataset_that_isnt_on_a_named_lakehouse_fails_the_activity(tmp_path):
    r = reader(tmp_path)
    no_lh = {"properties": {"type": "Lakehouse", "typeProperties": {"workspaceId": "ws1"}}}
    assert "needs its workspace and artifactId" in _fails(
        lambda: r.lookup(act("Lookup"), {"datasetSettings": ds(ls=no_lh, table="orders")}), "LocalDataUnsupported")
    assert "needs a table name" in _fails(
        lambda: r.lookup(act("Lookup"), {"datasetSettings": ds(table="")}), "LocalDataUnsupported")


def test_a_lookup_larger_than_fabric_allows_fails(tmp_path, monkeypatch):
    write_deltalake(str(tmp_path / "Tables" / "dbo" / "orders"), pa.table({"id": [1, 2], "note": ["x" * 50, "y" * 50]}))
    r = reader(tmp_path, pins={"dbo.orders": 0})
    monkeypatch.setattr(pd_, "LOOKUP_BYTES", 64)
    assert "larger than 4 MB" in _fails(
        lambda: r.lookup(act("Lookup"), {"datasetSettings": ds(table="orders"), "firstRowOnly": False}),
        "LocalLookupTooLarge")


def test_get_metadata_refuses_fields_and_datasets_this_pc_cant_give(tmp_path):
    r = reader(tmp_path)
    assert "doesn't give size" in _fails(
        lambda: r.metadata(act("GetMetadata"), {"datasetSettings": ds(table="orders"), "fieldList": ["size"]}),
        "LocalDataUnsupported")
    assert "AzureSqlTable dataset" in _fails(
        lambda: r.metadata(act("GetMetadata"), {"datasetSettings": ds(kind="AzureSqlTable"), "fieldList": ["exists"]}),
        "LocalDataUnsupported")


def test_a_missing_table_exists_false_or_fails(tmp_path):
    r = reader(tmp_path)
    assert r.metadata(act("GetMetadata"), {"datasetSettings": ds(table="gone"), "fieldList": ["exists"]}) == {"exists": False}
    assert "isn't in LH_Sales" in _fails(
        lambda: r.metadata(act("GetMetadata"), {"datasetSettings": ds(table="gone"), "fieldList": ["itemName"]}),
        "LocalDataNotFound")


def test_file_metadata_without_a_modified_time_or_on_a_file_asked_for_children(tmp_path):
    r = reader(tmp_path, file={"is_dir": False, "size": 7})
    settings = ds(kind="Binary", location={"folderPath": "raw", "fileName": "a.csv"})
    out = r.metadata(act("GetMetadata"), {"datasetSettings": settings, "fieldList": ["size", "lastModified"]})
    assert out == {"size": 7}
    assert "childItems needs a folder; Files/raw/a.csv is a file" in _fails(
        lambda: r.metadata(act("GetMetadata"), {"datasetSettings": settings, "fieldList": ["childItems"]}),
        "LocalDataUnsupported")


def test_a_run_reading_another_connection_fails_the_activity(tmp_path):
    sql = {"properties": {"type": "AzureSqlDatabase"}}
    assert "reads AzureSqlDatabase, not a lakehouse" in _fails(
        lambda: reader(tmp_path).lookup(act("Lookup"), {"datasetSettings": ds(ls=sql, table="orders")}),
        "LocalDataUnsupported")


@pytest.mark.parametrize("location", [{"folderPath": "raw/../secret"}, {"folderPath": "raw", "fileName": "../x.csv"}])
def test_a_file_path_that_goes_up_a_folder_is_refused(tmp_path, location):
    r = reader(tmp_path, file={"is_dir": True, "size": 0})
    assert "can't go up a folder" in _fails(
        lambda: r.metadata(act("GetMetadata"), {"datasetSettings": ds(kind="Binary", location=location),
                                                "fieldList": ["exists"]}), "LocalDataUnsupported")


def test_file_metadata_gives_the_modified_time_when_there_is_one(tmp_path):
    r = reader(tmp_path, file={"is_dir": False, "size": 7, "modified": "2026-10-22T00:00:00Z"})
    settings = ds(kind="Binary", location={"folderPath": "raw", "fileName": "a.csv"})
    out = r.metadata(act("GetMetadata"), {"datasetSettings": settings, "fieldList": ["itemType", "lastModified"]})
    assert out == {"itemType": "File", "lastModified": "2026-10-22T00:00:00Z"}
