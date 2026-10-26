"""Semantic models to and from Apache Ossie: conversion, the update merge, the change plan and the service."""

import base64
import copy
import json
import os
from pathlib import Path

import pytest
import yaml

from fabric_desktop import model_ossie
from fabric_desktop.meta.store import OneLakeStore
from fabric_desktop.service import Service


def _bim():
    """A Direct Lake model like the Semantic model tab builds, plus what people add in Fabric."""
    return {"name": "SM_Gold", "compatibilityLevel": 1604, "model": {
        "culture": "en-US",
        "tables": [
            {"name": "fact_sales", "lineageTag": "t-fact", "columns": [
                {"name": "customer_sk", "dataType": "int64", "sourceColumn": "customer_sk", "isHidden": True,
                 "summarizeBy": "none", "lineageTag": "c-fact-sk"},
                {"name": "amount", "dataType": "decimal", "sourceColumn": "amount", "formatString": "#,0.00",
                 "summarizeBy": "none", "lineageTag": "c-amount"}],
             "measures": [
                {"name": "fact_sales rows", "expression": "COUNTROWS('fact_sales')", "lineageTag": "m-rows"},
                {"name": "fact_sales total amount", "expression": "SUM('fact_sales'[amount])", "lineageTag": "m-total"}],
             "partitions": [{"name": "fact_sales", "mode": "directLake", "source": {
                 "type": "entity", "entityName": "fact_sales", "schemaName": "dbo", "expressionSource": "DatabaseQuery"}}]},
            {"name": "dim_customer", "lineageTag": "t-cust", "columns": [
                {"name": "customer_sk", "dataType": "int64", "sourceColumn": "customer_sk", "isKey": True, "lineageTag": "c-cust-sk"},
                {"name": "country", "dataType": "string", "sourceColumn": "country", "lineageTag": "c-country"},
                {"name": "city", "dataType": "string", "sourceColumn": "city", "lineageTag": "c-city"}],
             "hierarchies": [{"name": "Geo", "levels": [{"name": "country", "ordinal": 0, "column": "country"},
                                                        {"name": "city", "ordinal": 1, "column": "city"}]}],
             "partitions": [{"name": "dim_customer", "mode": "directLake", "source": {
                 "type": "entity", "entityName": "dim_customer", "schemaName": "dbo", "expressionSource": "DatabaseQuery"}}]}],
        "relationships": [{"name": "r1", "fromTable": "fact_sales", "fromColumn": "customer_sk",
                           "toTable": "dim_customer", "toColumn": "customer_sk"}],
        "roles": [{"name": "EU only", "modelPermission": "read",
                   "tablePermissions": [{"name": "dim_customer", "filterExpression": "[country] = \"DE\""}]}],
        "expressions": [{"name": "DatabaseQuery", "kind": "m",
                         "expression": "let\n    database = Sql.Database(\"abc\", \"sql1\")\nin\n    database"}]}}


def _ossie(extra_metric=None):
    """An Ossie file another tool wrote: no Power BI section."""
    doc = {"version": model_ossie.OSSIE_VERSION, "name": "orders", "datasets": [
        {"name": "fact_sales", "source": "dbo.fact_sales", "fields": [
            {"name": "customer_sk", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "customer_sk"}]},
             "datatype": "Integer"},
            {"name": "amount", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "amount"}]},
             "datatype": "Decimal", "description": "Order amount"},
            {"name": "channel_sk", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "channel_sk"}]},
             "datatype": "Integer"}],
         "metrics": [{"name": "net revenue", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(amount)"}]}}]},
        {"name": "dim_channel", "source": "dbo.dim_channel", "primary_key": ["channel_sk"], "fields": [
            {"name": "channel_sk", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "channel_sk"}]},
             "datatype": "Integer"},
            {"name": "channel", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "channel"}]},
             "datatype": "String"}]}],
        "relationships": [{"name": "sales_channel", "from": "fact_sales", "to": "dim_channel",
                           "from_columns": ["channel_sk"], "to_columns": ["channel_sk"]}]}
    if extra_metric:
        doc["datasets"][0]["metrics"].append(extra_metric)
    return yaml.safe_dump(doc, sort_keys=False)


SOURCE = {"workspaceId": "ws-test", "itemId": "id-LH_Gold"}


# ---- conversion

def test_a_model_sent_to_ossie_and_back_is_the_same_model():
    out = model_ossie.export(_bim(), translate_dax=False)
    back, _ = model_ossie.to_tmsl(out["yaml"])
    assert back["model"] == _bim()["model"]


def test_export_reports_what_only_fabric_understands_and_writes_simple_measures_as_sql():
    out = model_ossie.export(_bim())
    assert out["counts"] == {"datasets": 2, "fields": 5, "metrics": 2, "portable_metrics": 1, "relationships": 1}
    kept = {r["object"]: r["kind"] for r in out["report"]}
    assert kept == {"model": "kept", "table 'dim_customer'": "kept", "table 'fact_sales' measure 'fact_sales rows'": "kept"}
    assert any("row-level security" in r["message"] for r in out["report"])
    doc = yaml.safe_load(out["yaml"])
    total = next(m for m in doc["metrics"] if m["name"] == "fact_sales total amount")
    assert [d["dialect"] for d in total["expression"]["dialects"]] == ["OSSIE_SQL_2026", "DAX"]


@pytest.mark.parametrize("text, says", [
    ("a: [", "valid YAML"),
    ("- just\n- a list\n", "one object"),
    ("semantic_model: []\n", "older Ossie layout"),
    ("version: 0.1.0\nname: x\ndatasets: [{name: a, source: b}]\n", "version 0.1.0"),
    (f"version: {model_ossie.OSSIE_VERSION}\nname: x\ndatasets: []\n", "at least one dataset"),
    # Nested aliases: a few hundred bytes that would expand to a model of about a gigabyte.
    ("a0: &a0 [x, x, x, x, x, x, x, x, x, x]\n"
     + "".join(f"a{i}: &a{i} [{', '.join([f'*a{i-1}'] * 10)}]\n" for i in range(1, 8))
     + f"version: {model_ossie.OSSIE_VERSION}\nname: x\ndatasets: [{{name: t, source: s.t, ai_context: *a7}}]\n",
     "YAML aliases"),
])
def test_files_that_arent_ossie_are_refused_with_a_reason(text, says):
    with pytest.raises((ValueError, TypeError), match=says):
        model_ossie.parse(text)


def test_a_file_from_another_tool_reads_through_the_lakehouse_given():
    bim, report = model_ossie.to_tmsl(_ossie(), source=SOURCE)
    expr = next(e for e in bim["model"]["expressions"] if e["name"] == "DatabaseQuery")
    assert "onelake.dfs.fabric.microsoft.com/ws-test/id-LH_Gold" in "".join(expr["expression"])
    assert not any("placeholder" in r["message"] for r in report)
    measure = bim["model"]["tables"][0]["measures"][0]
    assert measure["expression"] == "SUM('fact_sales'[amount])"


# ---- the update merge and the change plan

def test_update_keeps_roles_hierarchies_lineage_tags_and_fabric_only_measures():
    incoming, _ = model_ossie.to_tmsl(_ossie(), source=SOURCE)
    final = model_ossie.merge(_bim(), incoming)
    model = final["model"]
    tables = {t["name"]: t for t in model["tables"]}
    assert set(tables) == {"fact_sales", "dim_channel", "dim_customer"}
    assert [r["name"] for r in model["roles"]] == ["EU only"]
    assert tables["dim_customer"]["hierarchies"][0]["name"] == "Geo"
    fact = tables["fact_sales"]
    assert fact["lineageTag"] == "t-fact" and fact["partitions"] == _bim()["model"]["tables"][0]["partitions"]
    cols = {c["name"]: c for c in fact["columns"]}
    assert cols["amount"]["lineageTag"] == "c-amount" and cols["amount"]["description"] == "Order amount"
    assert {m["name"] for m in fact["measures"]} == {"net revenue", "fact_sales rows", "fact_sales total amount"}
    # The new table reads through the model's own source, not a second Direct Lake connection.
    assert tables["dim_channel"]["partitions"][0]["source"]["expressionSource"] == "DatabaseQuery"
    assert [e["name"] for e in model["expressions"]] == ["DatabaseQuery"]
    assert "Sql.Database" in model["expressions"][0]["expression"]
    assert final["name"] == "SM_Gold" and final["compatibilityLevel"] == 1604
    assert len(model["relationships"]) == 2


def test_update_takes_out_only_what_the_caller_names():
    incoming, _ = model_ossie.to_tmsl(_ossie(), source=SOURCE)
    assert set(model_ossie.removable(_bim(), incoming)) >= {"fact_sales[fact_sales rows]", "dim_customer"}
    final = model_ossie.merge(_bim(), incoming, remove=["fact_sales[fact_sales rows]", "dim_customer"])
    tables = {t["name"]: t for t in final["model"]["tables"]}
    assert "dim_customer" not in tables
    assert "fact_sales rows" not in {m["name"] for m in tables["fact_sales"]["measures"]}
    # A relationship to a table that's gone goes with it.
    assert [r["toTable"] for r in final["model"]["relationships"]] == ["dim_channel"]


def test_plan_lists_adds_changes_removals_and_measures_that_need_dax():
    case = {"name": "repeat rate", "expression": {"dialects": [
        {"dialect": "ANSI_SQL", "expression": "SUM(CASE WHEN amount > 0 THEN 1 ELSE 0 END)"}]}}
    incoming, report = model_ossie.to_tmsl(_ossie(case), source=SOURCE)
    assert any(r["kind"] == "lost" for r in report)
    final = model_ossie.merge(_bim(), incoming)
    rows = {(r["change"], r["object"]) for r in model_ossie.plan(_bim(), final)}
    assert ("add", "dim_channel") in rows
    assert ("add", "fact_sales[net revenue]") in rows
    assert ("needs_dax", "fact_sales[repeat rate]") in rows
    assert ("change", "fact_sales[amount]") in rows
    assert ("add", "fact_sales.channel_sk -> dim_channel.channel_sk") in rows
    assert not any(change == "remove" for change, _ in rows)
    assert {r["change"] for r in model_ossie.plan(None, incoming)} <= {"add", "needs_dax"}


def test_parts_are_tmsl():
    parts = model_ossie.bim_parts(_bim())
    assert json.loads(parts["model.bim"])["model"]["roles"][0]["name"] == "EU only"
    assert set(parts) == {"model.bim", "definition.pbism"}


# ---- through the service

def _payload(text):
    return {"path": None, "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"}


class FakeFabric:
    def __init__(self):
        self.models = [{"id": "m1", "displayName": "SM_Gold"}]
        self.bim = _bim()
        self.saved = []

    def list_semantic_models(self, ws):
        return self.models

    def get_item_definition(self, ws, item_id, fmt=None):
        if fmt == "TMSL":
            parts = {"model.bim": json.dumps(self.bim), "definition.pbism": "{}"}
        else:
            parts = {"definition/model.tmdl": "model Model\n", "definition/tables/fact_sales.tmdl": "table fact_sales\n",
                     "definition.pbism": "{}"}
        return {"definition": {"parts": [_payload(t) | {"path": p} for p, t in parts.items()]}}

    def save_semantic_model(self, ws, name, parts, description="", replace=True):
        if not replace and any(m["displayName"] == name for m in self.models):
            raise FileExistsError(f"{name} already exists", "m1")
        self.saved.append((name, parts, replace))
        if not any(m["displayName"] == name for m in self.models):
            self.models = self.models + [{"id": "m2", "displayName": name}]
            return {"id": "m2", "created": True}
        return {"id": "m1", "created": False}


class OssieService(Service):
    fake = None

    @property
    def fabric(self):
        return self.fake


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = OssieService(root=tmp_path / "app")
    s.fake = FakeFabric()
    s.meta_init(str(tmp_path / "proj"), "3")
    project = s._project()
    store = OneLakeStore("test", "ws-test", {l.lakehouse: f"id-{l.lakehouse}" for l in project.layers},
                         lambda: "tok", tmp_path / "cache")
    real_cfg = type(s)._env_cfg
    monkeypatch.setattr(s, "_env_cfg", lambda p, env: {"workspace_id": "ws-test", "workspace_name": "WS Test",
                                                       "target": "onelake"} if env == "test" else real_cfg(s, p, env))
    monkeypatch.setattr(s, "_store", lambda p, env, create=False: store)
    return s


def test_export_reads_the_model_as_tmsl_and_suggests_a_path(svc):
    assert [m["name"] for m in svc.meta_model_ossie_models("test")["models"]] == ["SM_Gold"]
    out = svc.meta_model_ossie_export("test", "SM_Gold")
    assert out["path"] == "semantic/SM_Gold.ossie.yaml" and out["counts"]["datasets"] == 2
    saved = svc.meta_model_ossie_save(out["path"], out["yaml"])
    assert saved["path"] == "semantic/SM_Gold.ossie.yaml"
    assert svc.meta_model_ossie_read(saved["path"])["text"] == out["yaml"]
    with pytest.raises(LookupError):
        svc.meta_model_ossie_export("test", "Nope")


@pytest.mark.parametrize("path", ["../out.ossie.yaml", "/etc/x.ossie.yaml", "semantic/model.yaml", ""])
def test_save_stays_inside_the_project_and_names_ossie_files(svc, path):
    out = svc.meta_model_ossie_export("test", "SM_Gold")
    with pytest.raises(ValueError):
        svc.meta_model_ossie_save(path, out["yaml"])


def test_dev_on_this_pc_has_no_models_to_convert(svc):
    with pytest.raises(ValueError, match="Test or Prod"):
        svc.meta_model_ossie_models("dev")


def test_import_previews_then_writes_only_when_confirmed(svc):
    text = _ossie()
    preview = svc.meta_model_ossie_preview("test", text, "SM_Gold")
    assert preview["exists"] and preview["mode"] == "update"
    assert ("add", "dim_channel") in {(r["change"], r["object"]) for r in preview["changes"]}
    assert "dim_customer" in preview["removable"]
    asked = svc.meta_model_ossie_import("test", text, "SM_Gold")
    assert asked["confirm"]["name"] == "SM_Gold" and asked["confirm"]["counts"]["add"] >= 3
    assert svc.fake.saved == []
    done = svc.meta_model_ossie_import("test", text, "SM_Gold", confirmed=True)
    assert done["created"] is False and done["needs_dax"] == []
    name, parts, replace = svc.fake.saved[-1]
    written = json.loads(parts["model.bim"])
    assert name == "SM_Gold" and replace is True
    assert [r["name"] for r in written["model"]["roles"]] == ["EU only"]


def test_import_modes_check_whether_the_model_exists(svc):
    with pytest.raises(FileExistsError):
        svc.meta_model_ossie_preview("test", _ossie(), "SM_Gold", mode="new")
    with pytest.raises(LookupError):
        svc.meta_model_ossie_preview("test", _ossie(), "Orders", mode="update")
    with pytest.raises(ValueError):
        svc.meta_model_ossie_preview("test", _ossie(), "SM_Gold", mode="merge")
    created = svc.meta_model_ossie_import("test", _ossie(), "", mode="new", confirmed=True)
    assert created["name"] == "orders" and created["created"] is True
    replaced = svc.meta_model_ossie_import("test", _ossie(), "SM_Gold", mode="replace", confirmed=True)
    assert "roles" not in json.loads(svc.fake.saved[-1][1]["model.bim"])["model"]
    assert replaced["mode"] == "replace"


def test_tmdl_save_writes_the_power_bi_project_layout_and_removes_stale_files(svc):
    project = svc._project()
    stale = project.root / "semantic" / "SM_Gold.SemanticModel" / "definition" / "tables" / "old.tmdl"
    stale.parent.mkdir(parents=True)
    stale.write_text("table old\n")
    out = svc.meta_model_tmdl_save("test", "SM_Gold")
    assert out["folder"] == "semantic/SM_Gold.SemanticModel" and out["files"] == 3
    assert out["removed"] == ["semantic/SM_Gold.SemanticModel/definition/tables/old.tmdl"]
    assert (project.root / out["folder"] / "definition" / "tables" / "fact_sales.tmdl").read_text() == "table fact_sales\n"


def test_a_model_that_cant_be_read_is_not_imported_over(svc, monkeypatch):
    def fail(*a, **k):
        raise OSError("throttled")
    monkeypatch.setattr(svc.fake, "get_item_definition", fail)
    with pytest.raises(RuntimeError, match="Couldn't read SM_Gold"):
        svc.meta_model_ossie_import("test", _ossie(), "SM_Gold", confirmed=True)
    assert svc.fake.saved == []


def test_merge_does_not_change_its_inputs():
    current, (incoming, _) = _bim(), model_ossie.to_tmsl(_ossie(), source=SOURCE)
    before = (copy.deepcopy(current), copy.deepcopy(incoming))
    model_ossie.merge(current, incoming, remove=["dim_customer"])
    assert (current, incoming) == before


def test_a_file_too_large_to_be_a_model_is_refused_before_parsing(monkeypatch):
    monkeypatch.setattr(model_ossie, "MAX_CHARS", 100)
    with pytest.raises(ValueError, match="at most"):
        model_ossie.parse(_ossie())


def test_reading_a_project_file_refuses_what_import_would(svc):
    project = svc._project()
    (project.root / "semantic").mkdir()
    (project.root / "semantic" / "notes.yaml").write_text("just: notes\n")
    with pytest.raises(ValueError, match="version"):
        svc.meta_model_ossie_read("semantic/notes.yaml")


def test_a_model_kept_in_git_exports_offline(svc, monkeypatch):
    from fabric_desktop import semantic
    root = Path(svc._project().root)
    assert svc.meta_model_ossie_folders() == {"folders": []}
    svc.meta_model_tmdl_save("test", "SM_Gold")  # what Save TMDL to Git writes
    fact = semantic.ModelTable("fact_sales", "gold.fact_sales", "dbo", "fact_sales", [("amount", "double")])
    for path, text in semantic.build([fact], [], "srv", "ep").items():
        (root / "semantic" / "SM_Gold.SemanticModel" / path).parent.mkdir(parents=True, exist_ok=True)
        (root / "semantic" / "SM_Gold.SemanticModel" / path).write_text(text, encoding="utf-8")
    (root / "semantic" / "Old.SemanticModel").mkdir()
    (root / "semantic" / "Old.SemanticModel" / "model.bim").write_text(json.dumps(FakeFabric().bim), encoding="utf-8")
    assert [f["name"] for f in svc.meta_model_ossie_folders()["folders"]] == ["Old", "SM_Gold"]
    monkeypatch.setattr(svc, "fake", None)  # no Fabric at all
    out = svc.meta_model_ossie_export_folder("semantic/SM_Gold.SemanticModel")
    assert out["name"] == "SM_Gold" and out["path"] == "semantic/SM_Gold.ossie.yaml"
    assert out["counts"]["datasets"] == 1 and out["counts"]["portable_metrics"] == 1
    old = svc.meta_model_ossie_export_folder("semantic\\Old.SemanticModel")
    assert old["name"] == "Old" and old["counts"]["datasets"] == 2


@pytest.mark.parametrize("path", ["semantic/../semantic/SM_Gold.SemanticModel", "/etc", "semantic/Nope.SemanticModel",
                                  "semantic", "SM_Gold.SemanticModel"])
def test_export_folder_reads_only_model_folders_under_semantic(svc, path):
    (Path(svc._project().root) / "semantic" / "SM_Gold.SemanticModel" / "definition").mkdir(parents=True)
    with pytest.raises(LookupError):
        svc.meta_model_ossie_export_folder(path)

def test_export_folder_refuses_links_junctions_and_a_bim_that_isnt_a_model(svc, tmp_path, monkeypatch):
    from fabric_desktop import model_ossie_service
    root = Path(svc._project().root)
    outside = tmp_path / "outside"
    (outside / "definition" / "tables").mkdir(parents=True)
    (outside / "definition" / "tables" / "Secret.tmdl").write_text(
        "table Secret\n\tpartition Secret = entity\n\t\tsource\n\t\t\tentityName: s\n", encoding="utf-8")
    good = root / "semantic" / "Good.SemanticModel" / "definition"
    (good / "tables").mkdir(parents=True)
    (good / "model.tmdl").write_text("model Model\n", encoding="utf-8")
    table = "table {0}\n\tcolumn id\n\t\tdataType: int64\n\t\tsourceColumn: id\n\tpartition {0} = entity\n\t\tmode: directLake\n\t\tsource\n\t\t\tentityName: {0}\n"
    (good / "tables" / "Sales.tmdl").write_text(table.format("Sales"), encoding="utf-8")
    (good / "more").mkdir()
    (good / "more" / "Keep.tmdl").write_text(table.format("Keep"), encoding="utf-8")
    (good / "linked").symlink_to(outside / "definition" / "tables", target_is_directory=True)
    (root / "semantic" / "Linked.SemanticModel").symlink_to(outside, target_is_directory=True)
    assert [f["name"] for f in svc.meta_model_ossie_folders()["folders"]] == ["Good"]
    out = svc.meta_model_ossie_export_folder("semantic/Good.SemanticModel")
    assert "Secret" not in out["yaml"] and "Sales" in out["yaml"]
    # A junction isn't a symlink to pathlib: _is_link sees its reparse tag.
    real = model_ossie_service._is_link
    monkeypatch.setattr(model_ossie_service, "_is_link", lambda st: real(st) or st.st_ino == os.stat(good / "tables").st_ino)
    out = svc.meta_model_ossie_export_folder("semantic/Good.SemanticModel")
    assert "Sales" not in out["yaml"] and "Keep" in out["yaml"]
    monkeypatch.setattr(model_ossie_service, "_is_link", lambda st: st.st_ino == os.stat(root / "semantic").st_ino)
    assert svc.meta_model_ossie_folders() == {"folders": []}
    monkeypatch.setattr(model_ossie_service, "_is_link", real)
    (root / "semantic" / "List.SemanticModel").mkdir()
    (root / "semantic" / "List.SemanticModel" / "model.bim").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="isn't a semantic model"):
        svc.meta_model_ossie_export_folder("semantic/List.SemanticModel")

def test_ai_tools_only_read_and_pass_their_arguments_through():
    from fabric_desktop.integrations.capabilities import register_core
    from fabric_desktop.integrations.core import CallContext, Caller
    from fabric_desktop.integrations.registry import ReadOnlyAuthorizer, Registry

    class Svc:
        def __init__(self):
            self.calls = []

        def meta_model_ossie_export(self, env, name, translate_dax):
            self.calls.append(("export", env, name, translate_dax))
            return {"yaml": "", "counts": {}, "report": []}

        def meta_model_ossie_preview(self, env, text, name, mode, remove):
            self.calls.append(("preview", env, text, name, mode, remove))
            return {"changes": [], "report": []}

    reg = Registry(ReadOnlyAuthorizer())
    register_core(reg)
    ids = ("fabric_desktop_list_semantic_models", "fabric_desktop_export_semantic_model_ossie",
           "fabric_desktop_preview_ossie_import")
    caps = {c.id: c for c in reg.all()}
    assert all(caps[i].read_only and caps[i].approval == "never" and caps[i].permission == "READ_METADATA" for i in ids)
    assert not any("import" in c.id and "ossie" in c.id and not c.read_only for c in reg.all())  # writes stay in the app
    svc, ctx = Svc(), CallContext(Caller("c", "test"), timeout=10)
    reg.invoke(svc, ids[1], {"env": "prod", "name": "SM_Gold"}, ctx)
    reg.invoke(svc, ids[2], {"env": "test", "text": "name: m", "mode": "replace"}, ctx)
    assert svc.calls == [("export", "prod", "SM_Gold", True), ("preview", "test", "name: m", "", "replace", [])]
