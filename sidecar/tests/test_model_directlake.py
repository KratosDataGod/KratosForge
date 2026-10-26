"""The Direct Lake check: guardrails, schema drift, relationship keys, and the service that reads OneLake."""

import base64
import copy
import json

import polars as pl
import pyarrow as pa
import pytest
from deltalake import write_deltalake

from fabric_desktop import model_directlake as dl
from fabric_desktop import semantic
from fabric_desktop.meta.store import OneLakeStore
from fabric_desktop.model_directlake_service import duplicate_values, table_stats, unmatched_rows
from fabric_desktop.service import Service


def _bim(behavior=None, source='let\n    database = Sql.Database("abc", "SQL1")\nin\n    database'):
    model = {
        "tables": [
            {"name": "FactSales", "columns": [
                {"name": "customer_sk", "dataType": "int64", "sourceColumn": "customer_sk"},
                {"name": "amount", "dataType": "double", "sourceColumn": "amount"},
                {"name": "discount_pct", "dataType": "double", "sourceColumn": "discount_pct"},
                {"name": "RowNumber-1", "dataType": "int64", "type": "rowNumber"}],
             "partitions": [{"name": "FactSales", "mode": "directLake", "source": {
                 "type": "entity", "entityName": "fact_sales", "schemaName": "gold", "expressionSource": "DatabaseQuery"}}]},
            {"name": "DimCustomer", "columns": [
                {"name": "customer_sk", "dataType": "int64", "sourceColumn": "customer_sk"},
                {"name": "country", "dataType": "string", "sourceColumn": "country"}],
             "partitions": [{"name": "DimCustomer", "mode": "directLake", "source": {
                 "type": "entity", "entityName": "dim_customer", "schemaName": "gold", "expressionSource": "DatabaseQuery"}}]},
            {"name": "Targets", "columns": [{"name": "x", "dataType": "int64", "sourceColumn": "x"}],
             "partitions": [{"name": "Targets", "mode": "import", "source": {"type": "m", "expression": "..."}}]}],
        "relationships": [{"fromTable": "FactSales", "fromColumn": "customer_sk",
                           "toTable": "DimCustomer", "toColumn": "customer_sk"}],
        "expressions": [{"name": "DatabaseQuery", "kind": "m", "expression": source}]}
    if behavior:
        model["directLakeBehavior"] = behavior
    return {"name": "SM_Gold", "model": model}


def _stats(**kw):
    base = {"FactSales": dl.TableStats([("customer_sk", "long"), ("amount", "double"), ("photo", "binary"),
                                        ("_run_id", "string"), ("channel", "string")],
                                       rows=1000, files=10, bytes=10_000, row_groups=10),
            "DimCustomer": dl.TableStats([("customer_sk", "long"), ("country", "string")],
                                         rows=50, files=1, bytes=500, row_groups=1)}
    return base | kw


def _by(out, check):
    return [f for f in out["findings"] if f["check"] == check]


# ---- the pure checks

def test_limits_follow_microsofts_guardrails_and_premium_aliases():
    assert dl.limits("f32") == {"sku": "F32", "files": 1000, "row_groups": 1000, "rows": 300_000_000,
                                "model_bytes": 40 * 1024 ** 3}
    assert dl.limits("P1") == dl.limits("F64") and dl.limits("F64")["model_bytes"] is None
    assert dl.limits("") is None and dl.limits("A4") is None


def test_only_direct_lake_partitions_are_checked_and_row_numbers_skipped():
    tables = dl.direct_lake_tables(_bim())
    assert [t.name for t in tables] == ["FactSales", "DimCustomer"]
    assert [c["name"] for c in tables[0].columns] == ["customer_sk", "amount", "discount_pct"]
    assert dl.source(_bim(), "DatabaseQuery") == {"kind": "sql", "endpoint": "sql1"}
    onelake = _bim(source='AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/'
                          '11111111-1111-1111-1111-111111111111/22222222-2222-2222-2222-222222222222", '
                          '[HierarchicalNavigation=true])')
    assert dl.source(onelake, "DatabaseQuery") == {"kind": "onelake", "workspace": "11111111-1111-1111-1111-111111111111",
                                                  "item": "22222222-2222-2222-2222-222222222222"}


def test_drift_finds_missing_new_and_unreadable_columns():
    out = dl.check(_bim(), _stats(), dl.limits("F64"), ours=True)
    missing = _by(out, "Column missing")
    assert [(f["severity"], f["column"], f["fix"]) for f in missing] == [("fail", "discount_pct", "update_model")]
    assert [f["column"] for f in _by(out, "New column")] == ["channel"]  # _run_id is the framework's, not news
    assert [f["column"] for f in _by(out, "Type left out")] == ["photo"]
    assert out["counts"] == {"fail": 1, "warn": 0, "info": 2}
    assert out["findings"][0]["severity"] == "fail", "the worst comes first"
    # A model this app didn't build gets no Update model button.
    assert {f["fix"] for f in dl.check(_bim(), _stats(), None)["findings"]} == {None}


def test_a_model_column_direct_lake_cant_read_fails_and_a_changed_type_warns():
    bim = _bim()
    bim["model"]["tables"][0]["columns"].append({"name": "photo", "dataType": "binary", "sourceColumn": "photo"})
    bim["model"]["tables"][1]["columns"][1]["dataType"] = "int64"
    out = dl.check(bim, _stats(), None)
    assert [f["column"] for f in _by(out, "Type Direct Lake can't read")] == ["photo"]
    changed = _by(out, "Type changed")
    assert changed[0]["severity"] == "warn" and "now stores it as string" in changed[0]["message"]


def test_guardrails_warn_past_and_near_a_limit_and_estimates_say_about():
    stats = _stats(FactSales=dl.TableStats([("customer_sk", "long"), ("amount", "double"), ("discount_pct", "double")],
                                           rows=200, files=950, bytes=10, row_groups=1240, row_groups_estimated=True))
    out = dl.check(_bim(), stats, dl.limits("F32"))
    groups, files = _by(out, "Row groups")[0], _by(out, "Parquet files")[0]
    assert groups["severity"] == "warn" and groups["message"].startswith("about 1,240 row groups; F32 allows 1,000")
    assert "fall back to DirectQuery" in groups["message"] and groups["fix"] == "compact"
    assert "a few more loads will pass it" in files["message"]
    assert not _by(out, "Rows")


def test_past_a_limit_fails_when_fallback_is_off_or_the_model_reads_onelake():
    stats = _stats(FactSales=dl.TableStats([("customer_sk", "long"), ("amount", "double"), ("discount_pct", "double")],
                                           rows=400_000_000, files=1, bytes=1, row_groups=1))
    assert "fall back" in _by(dl.check(_bim(), stats, dl.limits("F32")), "Rows")[0]["message"]
    off = dl.check(_bim("DirectLakeOnly"), stats, dl.limits("F32"))
    assert "fail until the table is compacted, because fallback is off" in _by(off, "Rows")[0]["message"]
    assert _by(off, "Rows")[0]["fix"] is None, "compacting doesn't cut rows"


def test_model_size_counts_only_on_small_capacities():
    stats = _stats(DimCustomer=dl.TableStats([("customer_sk", "long"), ("country", "string")], rows=1, files=1,
                                             bytes=41 * 1024 ** 3, row_groups=1))
    assert _by(dl.check(_bim(), stats, dl.limits("F32")), "Model size")
    assert not _by(dl.check(_bim(), stats, dl.limits("F64")), "Model size")


def test_relationship_keys_must_match_in_type_and_be_unique():
    stats = _stats(DimCustomer=dl.TableStats([("customer_sk", "string"), ("country", "string")], rows=1, files=1,
                                             bytes=1, row_groups=1))
    out = dl.check(_bim(), stats, None, duplicates={"DimCustomer.customer_sk": 3})
    assert "int64" in _by(out, "Relationship types differ")[0]["message"]
    dup = _by(out, "Key not unique")[0]
    assert dup["table"] == "DimCustomer" and "3 values that appear more than once" in dup["message"]
    assert dl.unique_keys(_bim()) == [("DimCustomer", "customer_sk")]


def test_fact_rows_with_no_match_show_under_blank():
    out = dl.check(_bim(), _stats(), None, unmatched={"FactSales.customer_sk": {"rows": 12, "values": 2, "empty": 1}})
    f = _by(out, "Rows with no match")[0]
    assert (f["severity"], f["table"], f["column"], f["fix"]) == ("info", "FactSales", "customer_sk", "show_rows")
    assert "12 rows with a customer_sk that isn't in DimCustomer (2 values) and 1 row with no customer_sk" in f["message"]
    assert "(Blank)" in f["message"]
    assert not _by(dl.check(_bim(), _stats(), None, unmatched={"FactSales.customer_sk": {"rows": 0, "values": 0, "empty": 0}}),
                   "Rows with no match")


def test_a_missing_delta_table_fails_and_unread_tables_are_skipped():
    out = dl.check(_bim(), {"FactSales": None}, None)
    assert [f["check"] for f in out["findings"]] == ["Table missing"]
    assert "SQL view" in out["findings"][0]["message"]
    assert [t["table"] for t in out["tables"]] == ["FactSales"]


def test_check_does_not_change_the_model():
    bim = _bim()
    before = copy.deepcopy(bim)
    dl.check(bim, _stats(), dl.limits("F2"), {"DimCustomer.customer_sk": 1})
    assert bim == before


def test_the_model_builder_leaves_binary_out():
    assert semantic.tmdl_type("binary") is None
    t = semantic.ModelTable("t", "silver.t", "silver", "t", columns=[("a", "long"), ("b", "binary")])
    assert semantic.left_out(t) == [{"table": "t", "column": "b", "type": "binary"}]


# ---- reading Delta tables

def test_table_stats_reads_rows_files_and_row_groups(tmp_path):
    uri = str(tmp_path / "t")
    for i in range(3):
        write_deltalake(uri, pa.table({"k": list(range(i * 10, i * 10 + 10)), "v": ["x"] * 10}), mode="append")
    s = table_stats(uri, None)
    assert (s.rows, s.files, s.row_groups, s.row_groups_estimated) == (30, 3, 3, False)
    assert s.columns == [("k", "long"), ("v", "string")] and s.bytes > 0
    est = table_stats(uri, None, footers=2)
    assert est.row_groups == 3 and est.row_groups_estimated
    assert table_stats(str(tmp_path / "none"), None) is None


def test_duplicate_values_counts_repeated_non_empty_keys(tmp_path):
    uri = str(tmp_path / "d")
    write_deltalake(uri, pa.table({"k": [1, 1, 2, 3, 3, 3, None, None]}))
    assert duplicate_values(uri, None, "k") == 2
    assert duplicate_values(str(tmp_path / "none"), None, "k") == 0


def test_unmatched_rows_reads_both_keys(tmp_path):
    fact, dim = str(tmp_path / "f"), str(tmp_path / "d")
    write_deltalake(fact, pa.table({"k": [1, 2, 3, 3, 4, None]}))
    write_deltalake(dim, pa.table({"k": [1, 2, None]}))
    assert unmatched_rows((fact, None, "k"), (dim, None, "k")) == {"rows": 3, "values": 2, "empty": 1}
    assert unmatched_rows((fact, None, "k"), (str(tmp_path / "none"), None, "k")) == {"rows": 0, "values": 0, "empty": 0}


# ---- the service

def _payload(text):
    return {"payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"}


class FakeFabric:
    def __init__(self):
        self.bim = _bim()
        self.lakehouse_calls = 0

    def list_semantic_models(self, ws):
        return [{"id": "m1", "displayName": "SM_Gold"}, {"id": "m2", "displayName": "Import model"}]

    def get_item_definition(self, ws, item_id, fmt=None):
        bim = self.bim if item_id == "m1" else {"name": "x", "model": {"tables": []}}
        return {"definition": {"parts": [_payload(json.dumps(bim)) | {"path": "model.bim"}]}}

    def list_lakehouses(self, ws):
        self.lakehouse_calls += 1
        return [{"id": "lh-other", "properties": {"sqlEndpointProperties": {"id": "sql0"}}},
                {"id": "lh-gold", "properties": {"sqlEndpointProperties": {"id": "sql1"}}}]


class DLService(Service):
    fake = None

    @property
    def fabric(self):
        return self.fake


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = DLService(root=tmp_path / "app")
    s.fake = FakeFabric()
    s.meta_init(str(tmp_path / "proj"), "3")
    project = s._project()
    store = OneLakeStore("test", "ws-test", {l.lakehouse: f"id-{l.lakehouse}" for l in project.layers},
                         lambda: "tok", tmp_path / "cache")
    real_cfg = type(s)._env_cfg
    monkeypatch.setattr(s, "_env_cfg", lambda p, env: {"workspace_id": "ws-test", "workspace_name": "WS Test",
                                                       "target": "onelake"} if env == "test" else real_cfg(s, p, env))
    monkeypatch.setattr(s, "_store", lambda p, env, create=False: store)
    lake = tmp_path / "lake"
    monkeypatch.setattr(s, "_directlake_uri", lambda ws, lh, schema, table: (str(lake / lh / schema / table), None))
    monkeypatch.setattr(s, "capacity_health", lambda refresh=False: {"environments": [
        {"env": "test", "capacity": {"sku": "F2"}}]})
    write_deltalake(str(lake / "lh-gold" / "gold" / "fact_sales"),
                    pa.table({"customer_sk": [1, 2, 3], "amount": [1.0, 2.0, 3.0], "channel": ["a", "b", "c"]}))
    write_deltalake(str(lake / "lh-gold" / "gold" / "dim_customer"),
                    pa.table({"customer_sk": [1, 2, 2], "country": ["DE", "FR", "FR"]}))
    return s


def test_setup_offers_the_environments_capacity(svc):
    out = svc.meta_model_directlake_setup("test")
    assert out["sku"] == "F2" and "F64" in out["skus"] and "P1" in out["skus"]


def test_the_check_reads_onelake_and_reports_drift_and_duplicate_keys(svc):
    out = svc.meta_model_directlake_check("test", "SM_Gold", keys=True)
    assert out["direct_lake"] and out["sku"] == "F2" and out["keys_checked"] and out["unread"] == []
    checks = {(f["check"], f["column"]) for f in out["findings"]}
    assert ("Column missing", "discount_pct") in checks and ("New column", "channel") in checks
    assert ("Key not unique", "customer_sk") in checks
    blank = next(f for f in out["findings"] if f["check"] == "Rows with no match")
    assert blank["table"] == "FactSales" and "1 row with a customer_sk that isn't in DimCustomer (1 value)" in blank["message"]
    assert all(f["fix"] != "update_model" or f["check"] in ("Column missing", "New column") for f in out["findings"])
    assert {t["table"]: t["rows"] for t in out["tables"]} == {"FactSales": 3, "DimCustomer": 3}
    without = svc.meta_model_directlake_check("test", "SM_Gold", sku="F64")
    assert not without["keys_checked"]
    assert not [f for f in without["findings"] if f["check"] in ("Key not unique", "Rows with no match")]
    assert without["sku"] == "F64"


def test_a_model_without_direct_lake_tables_or_a_lakehouse_elsewhere_says_so(svc):
    assert svc.meta_model_directlake_check("test", "Import model")["direct_lake"] is False
    svc.fake.bim = _bim(source='let database = Sql.Database("abc", "sql-elsewhere") in database')
    out = svc.meta_model_directlake_check("test", "SM_Gold")
    assert [u["table"] for u in out["unread"]] == ["FactSales", "DimCustomer"] and out["findings"] == []
    with pytest.raises(LookupError):
        svc.meta_model_directlake_check("test", "Nope")


def test_the_check_writes_nothing(svc, tmp_path):
    before = {p: p.stat().st_mtime_ns for p in (tmp_path / "lake").rglob("*") if p.is_file()}
    svc.meta_model_directlake_check("test", "SM_Gold", keys=True)
    after = {p: p.stat().st_mtime_ns for p in (tmp_path / "lake").rglob("*") if p.is_file()}
    assert before == after


def test_dev_on_this_pc_has_no_models_to_check(svc):
    with pytest.raises(ValueError, match="Switch to Test or Prod"):
        svc.meta_model_directlake_check("dev", "SM_Gold")


def test_methods_are_registered_and_read_only():
    from fabric_desktop import audit, server
    for m in ("meta_model_directlake_setup", "meta_model_directlake_check"):
        assert m in server.METHODS and m in audit.READ_ONLY
    assert pl  # polars reads the key column
