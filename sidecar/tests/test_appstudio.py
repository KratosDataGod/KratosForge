"""App Studio: the canonical specification, patch operations and the project folder (AS-02, AS-03, AS-04)."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from fabric_desktop.appstudio import patch, project, spec
from fabric_desktop.appstudio.project import ProjectError
from fabric_desktop.appstudio.spec import SpecError


def sales() -> dict:
    doc = spec.new("Sales overview", purpose="Revenue, profit and regions for sales leads")
    doc["sources"] = [{"id": "src_sales", "kind": "semantic-model", "name": "Sales", "workspace_id": "w1", "item_id": "m1"}]
    doc["measures"] = [{"id": "m_rev", "name": "Revenue", "source": "src_sales", "expression": "SUM(Sales[Amount])"},
                       {"id": "m_profit", "name": "Profit", "source": "src_sales", "expression": "SUM(Sales[Profit])"}]
    doc["pages"] = [
        {"id": "pg_overview", "title": "Overview", "components": [
            {"id": "c_rev", "type": "card", "title": "Revenue", "measures": ["m_rev"], "fields": [],
             "layout": {"x": 0, "y": 0, "w": 3, "h": 2}, "props": {}},
            {"id": "c_region", "type": "bar", "title": "Revenue by region", "measures": ["m_rev"],
             "fields": ["Region[Name]"], "layout": {"x": 3, "y": 0, "w": 9, "h": 4}, "props": {"sort": "desc"}},
            {"id": "c_slicer", "type": "slicer", "title": "Region", "measures": [], "fields": ["Region[Name]"],
             "layout": {"x": 0, "y": 2, "w": 3, "h": 2}, "props": {}}]},
        {"id": "pg_customers", "title": "Customers", "components": [
            {"id": "c_cust", "type": "table", "title": "Customers", "measures": ["m_rev", "m_profit"],
             "fields": ["'Customer'[Name]"], "layout": {"x": 0, "y": 0, "w": 12, "h": 6}, "props": {}}]}]
    doc["filters"] = [{"id": "f_region", "field": "Region[Name]", "pages": ["pg_overview"]}]
    doc["interactions"] = [{"id": "i_drill", "kind": "drillthrough", "from": "c_region", "to": "pg_customers"}]
    doc["theme"] = {"colors": {"primary": "#1F6FEB"}, "font": "Segoe UI"}
    return doc


# ---------------------------------------------------------------- spec


def test_reference_sales_spec_is_valid():
    assert spec.validate(sales()) == []
    assert spec.validate(spec.new("Empty")) == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda d: d["pages"][0]["components"].append(dict(d["pages"][0]["components"][0])), "already used"),
    (lambda d: d["pages"][0]["components"][0].update(type="hologram"), "isn't available"),
    (lambda d: d["pages"][0]["components"][0].update(measures=["m_missing"]), "must name a measure"),
    (lambda d: d["pages"][0]["components"][0].update(fields=["DROP TABLE x"]), "Table[Column]"),
    (lambda d: d["pages"][0]["components"][0]["layout"].update(x=10, w=4), "12-column grid"),
    (lambda d: d["interactions"][0].update(to="c_rev"), "must name a page"),
    (lambda d: d["filters"][0].update(pages=["nope"]), "must name a page"),
    (lambda d: d["theme"]["colors"].update(primary="blue"), "#RRGGBB"),
    (lambda d: d.update(target="dashboard"), "target must be"),
    (lambda d: d.update(pages=[]), "at least one page"),
    (lambda d: d.update(name=""), "name must be non-empty"),
    (lambda d: d.update(extra=1), "Unknown top-level"),
    (lambda d: d["permissions"].update(writeback=True), "Writeback"),
    (lambda d: d["measures"][0].update(id="1bad"), "must start with a letter"),
])
def test_invalid_specs_are_refused_with_where(mutate, expected):
    doc = sales()
    mutate(doc)
    problems = spec.validate(doc)
    assert any(expected in p for p in problems), problems
    with pytest.raises(SpecError):
        spec.check(doc)


def test_target_capabilities_differ():
    doc = sales()
    doc["target"] = "kratosforge-app"
    problems = spec.validate(doc)
    assert any("drillthrough" in p for p in problems)  # a KratosForge app can't drill through yet: refused, not faked


def test_not_an_object():
    assert spec.validate([]) == ["The specification must be a JSON object."]
    assert spec.validate({"format": 1, "id": "app", "name": "x", "target": "powerbi-report", "pages": "x",
                          "sources": [], "measures": [], "filters": [], "interactions": []})


def test_dependencies_and_dependents():
    doc = sales()
    deps = spec.dependencies(doc)
    assert deps["c_cust"] == ["m_rev", "m_profit", "src_sales"]
    assert deps["m_rev"] == ["src_sales"]
    assert spec.dependents(doc, "m_rev") == ["c_rev", "c_region", "c_cust"]
    assert spec.dependents(doc, "pg_customers") == ["i_drill"]
    assert spec.page_of(doc, "c_slicer") == "pg_overview"


# ---------------------------------------------------------------- patch


def test_set_changes_only_the_named_component_and_inverse_restores():
    doc = sales()
    before = json.dumps(doc, sort_keys=True)
    done = patch.apply(doc, [{"op": "set", "id": "c_region", "field": "type", "value": "waterfall"}])
    assert json.dumps(doc, sort_keys=True) == before  # the input is never changed
    new = done["spec"]
    assert spec.find(new, "c_region")[1]["type"] == "waterfall"
    assert done["changed"] == ["c_region"]
    for cid in ("c_rev", "c_slicer", "c_cust"):  # unrelated components are byte-equal
        assert spec.find(new, cid)[1] == spec.find(doc, cid)[1]
    back = patch.apply(new, done["inverse"])["spec"]
    assert json.dumps(back, sort_keys=True) == before


def test_add_page_component_filter_then_undo_all():
    doc = sales()
    ops = [{"op": "add", "kind": "page", "value": {"id": "pg_profit", "title": "Customer profitability", "components": []}},
           {"op": "add", "kind": "component", "page": "pg_profit", "value": {
               "id": "c_pm", "type": "column", "title": "Profit by customer", "measures": ["m_profit"],
               "fields": ["'Customer'[Name]"], "layout": {"x": 0, "y": 0, "w": 12, "h": 5}, "props": {}}},
           {"op": "set", "id": "f_region", "field": "pages", "value": "all"},
           {"op": "set", "id": "app", "field": "theme.colors.accent", "value": "#FF8800"}]
    done = patch.apply(doc, ops)
    assert [p["id"] for p in done["spec"]["pages"]] == ["pg_overview", "pg_customers", "pg_profit"]
    assert done["spec"]["theme"]["colors"]["accent"] == "#FF8800"
    assert patch.apply(done["spec"], done["inverse"])["spec"] == doc


def test_move_between_pages_and_back():
    doc = sales()
    done = patch.apply(doc, [{"op": "move", "id": "c_slicer", "page": "pg_customers", "index": 0}])
    assert spec.page_of(done["spec"], "c_slicer") == "pg_customers"
    assert patch.apply(done["spec"], done["inverse"])["spec"] == doc


def test_remove_used_measure_is_refused_and_unused_component_removal_undoes():
    doc = sales()
    with pytest.raises(SpecError, match="used by c_rev, c_region, c_cust"):
        patch.apply(doc, [{"op": "remove", "id": "m_rev"}])
    with pytest.raises(SpecError, match="used by i_drill"):
        patch.apply(doc, [{"op": "remove", "id": "pg_customers"}])
    done = patch.apply(doc, [{"op": "remove", "id": "c_slicer"}])
    assert patch.apply(done["spec"], done["inverse"])["spec"] == doc


def test_all_or_nothing():
    doc = sales()
    with pytest.raises(SpecError) as err:
        patch.apply(doc, [{"op": "set", "id": "c_rev", "field": "title", "value": "Sales"},
                          {"op": "set", "id": "c_rev", "field": "type", "value": "hologram"}])
    assert "isn't available" in str(err.value)
    assert spec.find(doc, "c_rev")[1]["title"] == "Revenue"


@pytest.mark.parametrize("op, expected", [
    ({"op": "set", "id": "c_rev", "field": "id", "value": "x"}, "can't be set"),
    ({"op": "set", "id": "pg_overview", "field": "components", "value": []}, "can't be set"),
    ({"op": "set", "id": "app", "field": "format", "value": 2}, "can't be set"),
    ({"op": "set", "id": "app", "field": "__class__", "value": 1}, "no field"),
    ({"op": "set", "id": "c_rev", "field": "a.b.c.d.e", "value": 1}, "isn't a field path"),
    ({"op": "remove", "id": "app"}, "can't be removed"),
    ({"op": "remove", "id": "ghost"}, "nothing with id"),
    ({"op": "add", "kind": "component", "page": "c_rev", "value": {"id": "x"}}, "isn't a page"),
    ({"op": "add", "kind": "measure", "value": {"id": "m_rev"}}, "already used"),
    ({"op": "add", "kind": "widget", "value": {"id": "w"}}, "add kind"),
    ({"op": "move", "id": "c_rev", "index": 99}, "index must be"),
    ({"op": "explode"}, "needs an id"),
    ({"op": "explode", "id": "c_rev"}, "Unknown operation"),
])
def test_bad_operations(op, expected):
    with pytest.raises(SpecError, match=expected):
        patch.apply(sales(), [op])


def test_empty_or_huge_change_refused():
    with pytest.raises(SpecError):
        patch.apply(sales(), [])
    with pytest.raises(SpecError, match="at most"):
        patch.apply(sales(), [{"op": "set", "id": "c_rev", "field": "title", "value": "x"}] * (patch.MAX_OPS + 1))


def test_failed_move_leaves_spec_untouched():
    doc = sales()
    with pytest.raises(SpecError):
        patch.apply(doc, [{"op": "move", "id": "c_rev", "page": "pg_customers", "index": 5}])
    assert spec.page_of(doc, "c_rev") == "pg_overview"


# ---------------------------------------------------------------- project


def test_create_change_close_reopen(tmp_path):
    folder = project.create(tmp_path, "Sales overview", sales())
    assert folder.name == "Sales overview.KFApp"
    project.change(folder, [{"op": "set", "id": "c_region", "field": "type", "value": "column"}],
                   note="Change the region chart to a column chart", source="ai")
    again = project.open_project(folder)  # a new session: nothing but the folder
    assert again["valid"] and again["can_undo"] and not again["can_redo"]
    assert spec.find(again["spec"], "c_region")[1]["type"] == "column"
    assert again["spec"]["pages"][1] == sales()["pages"][1]
    lines = (folder / "history.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["source"] == "ai" and json.loads(lines[0])["inverse"]
    assert "transcript" not in (folder / "kfapp.json").read_text()


def test_undo_redo_round_trip(tmp_path):
    folder = project.create(tmp_path, "S", sales())
    original = project.open_project(folder)["spec"]
    project.change(folder, [{"op": "set", "id": "c_rev", "field": "title", "value": "Total revenue"}])
    project.change(folder, [{"op": "remove", "id": "c_slicer"}])
    project.undo(folder)
    project.undo(folder)
    assert project.open_project(folder)["spec"] == original
    with pytest.raises(ProjectError, match="nothing to undo"):
        project.undo(folder)
    project.redo(folder)
    got = project.redo(folder)
    assert spec.find(got["spec"], "c_rev")[1]["title"] == "Total revenue"
    assert not got["can_redo"]
    project.undo(folder)
    project.change(folder, [{"op": "set", "id": "c_rev", "field": "title", "value": "Other"}])
    assert not project.open_project(folder)["can_redo"]  # a new change clears redo


def test_failed_change_writes_nothing(tmp_path):
    folder = project.create(tmp_path, "S", sales())
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    with pytest.raises(SpecError):
        project.change(folder, [{"op": "set", "id": "c_rev", "field": "type", "value": "hologram"}])
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before


@pytest.mark.parametrize("value", ["sk-ant-api03-" + "a" * 40, "sk-proj-" + "b" * 40, "AIza" + "c" * 35,
                                   "password=hunter2", "ghp_" + "d" * 36])
def test_secrets_never_saved(tmp_path, value):
    folder = project.create(tmp_path, "S", sales())
    with pytest.raises(SpecError, match="secret|key, token"):
        project.change(folder, [{"op": "set", "id": "c_rev", "field": "props.note", "value": value}])
    assert value not in "".join(p.read_text() for p in folder.rglob("*") if p.is_file())
    doc = sales()
    doc["sources"][0]["api_key"] = "x"
    with pytest.raises(SpecError, match="secret"):
        project.create(tmp_path, "T", doc)


def test_versions_and_restore(tmp_path):
    folder = project.create(tmp_path, "S", sales())
    v1 = project.save_version(folder, "First draft")
    project.change(folder, [{"op": "remove", "id": "c_slicer"}])
    assert [v["label"] for v in project.versions(folder)] == ["First draft"]
    got = project.restore_version(folder, v1["version"])
    assert spec.find(got["spec"], "c_slicer")
    assert [v["version"] for v in project.versions(folder)] == [1, 2]  # what was current is kept as version 2
    restored_back = project.restore_version(folder, got["kept_as"])
    with pytest.raises(KeyError):
        spec.find(restored_back["spec"], "c_slicer")
    with pytest.raises(ProjectError):
        project.restore_version(folder, 99)


def test_save_as_and_clone(tmp_path):
    folder = project.create(tmp_path, "S", sales())
    project.change(folder, [{"op": "set", "id": "c_rev", "field": "title", "value": "R"}])
    project.save_version(folder)
    copy = project.save_as(folder, tmp_path, "S copy")
    clone = project.save_as(folder, tmp_path, "S clone", clone=True)
    a, b, c = (project.open_project(f) for f in (folder, copy, clone))
    assert len({a["meta"]["app_id"], b["meta"]["app_id"], c["meta"]["app_id"]}) == 3
    assert b["spec"]["name"] == "S copy" and b["can_undo"] and project.versions(copy)
    assert not c["can_undo"] and not project.versions(clone)
    project.undo(copy)  # the copied history still undoes
    with pytest.raises(ProjectError, match="already exists"):
        project.save_as(folder, tmp_path, "S copy")


def test_export_import_round_trip(tmp_path):
    folder = project.create(tmp_path / "a", "Sales", sales())
    project.save_version(folder, "v1")
    data = project.export(folder)
    dest = project.import_(data, tmp_path / "b")
    assert project.open_project(dest)["spec"] == project.open_project(folder)["spec"]
    assert project.versions(dest)[0]["label"] == "v1"


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


@pytest.mark.parametrize("files, expected", [
    ({"../evil.json": "{}"}, "aren't part"),
    ({"kfapp.json": "{}", "app.json": "{}", "run.ps1": "x"}, "aren't part"),
    ({"kfapp.json": "{}"}, "no app.json"),
    ({"kfapp.json": "{}", "app.json": "not json"}, "isn't valid JSON"),
])
def test_import_refuses_unsafe_zips(tmp_path, files, expected):
    with pytest.raises(ProjectError, match=expected):
        project.import_(_zip(files), tmp_path)
    with pytest.raises(ProjectError, match="isn't an exported"):
        project.import_(b"nope", tmp_path)


def test_import_refuses_invalid_or_secret_spec(tmp_path):
    doc = sales()
    doc["purpose"] = "token=abc123secret"
    with pytest.raises(SpecError):
        project.import_(_zip({"kfapp.json": "{}", "app.json": json.dumps(doc)}), tmp_path)
    assert not list(tmp_path.iterdir())


def test_folder_rules(tmp_path):
    assert project.folder_name("Sales: Q1/Q2 <draft>") == "Sales Q1 Q2 draft.KFApp"
    with pytest.raises(ProjectError):
        project.folder_name("///")
    with pytest.raises(ProjectError, match="ends in"):
        project.open_project(tmp_path)
    (tmp_path / "x.KFApp").mkdir()
    with pytest.raises(ProjectError, match="no kfapp.json"):
        project.open_project(tmp_path / "x.KFApp")


def test_hand_edited_invalid_spec_opens_with_problems(tmp_path):
    folder = project.create(tmp_path, "S", sales())
    doc = json.loads((folder / "app.json").read_text())
    doc["pages"][0]["components"][0]["type"] = "hologram"
    (folder / "app.json").write_text(json.dumps(doc))
    got = project.open_project(folder)
    assert not got["valid"] and any("hologram" in p for p in got["problems"])
    with pytest.raises(SpecError):
        project.change(folder, [{"op": "set", "id": "c_rev", "field": "title", "value": "x"}])


def test_torn_history_line_is_skipped(tmp_path):
    folder = project.create(tmp_path, "S", sales())
    project.change(folder, [{"op": "set", "id": "c_rev", "field": "title", "value": "x"}])
    with open(folder / "history.jsonl", "a") as f:
        f.write('{"seq": 2, "ki')
    project.undo(folder)
    assert spec.find(project.open_project(folder)["spec"], "c_rev")[1]["title"] == "Revenue"


SECRET = "sk-" + "ant-api03-" + "Q" * 40


def test_a_change_that_sets_and_removes_a_secret_is_refused(tmp_path):
    folder = project.create(tmp_path, "S", sales())
    with pytest.raises(SpecError, match="change"):
        project.change(folder, [{"op": "set", "id": "c_rev", "field": "props.note", "value": SECRET},
                                {"op": "set", "id": "c_rev", "field": "props.note"}])
    assert SECRET not in "".join(p.read_text() for p in folder.rglob("*") if p.is_file())


def test_export_refuses_a_history_with_a_secret(tmp_path):
    folder = project.create(tmp_path, "S", sales())
    project.change(folder, [{"op": "set", "id": "c_rev", "field": "title", "value": "x"}])
    with open(folder / "history.jsonl", "a") as f:  # a hand-edited history
        f.write(json.dumps({"seq": 2, "kind": "change", "note": SECRET}) + "\n")
    with pytest.raises(SpecError, match="history.jsonl"):
        project.export(folder)
    with pytest.raises(SpecError, match="history.jsonl"):
        project.save_as(folder, tmp_path, "S copy")


@pytest.mark.parametrize("history", [
    json.dumps({"seq": 1, "kind": "change", "ops": [{"op": "set", "id": "c_rev", "field": "props.k", "value": SECRET}]}) + "\n",
    "not json " + SECRET + "\n",
])
def test_import_refuses_a_history_with_a_secret(tmp_path, history):
    with pytest.raises(SpecError, match="history.jsonl"):
        project.import_(_zip({"kfapp.json": "{}", "app.json": json.dumps(sales()), "history.jsonl": history}), tmp_path / "in")
    assert not (tmp_path / "in").exists()


def test_import_drops_a_version_with_a_secret_label(tmp_path):
    version = {"version": 1, "label": SECRET, "at": "x", "spec": sales()}
    dest = project.import_(_zip({"kfapp.json": "{}", "app.json": json.dumps(sales()),
                                 "versions/0001.json": json.dumps(version)}), tmp_path)
    assert project.versions(dest) == []


def test_import_refuses_too_many_files_and_caps_versions(tmp_path, monkeypatch):
    base = {"kfapp.json": "{}", "app.json": json.dumps(sales())}
    version = json.dumps({"version": 1, "label": "v", "at": "x", "spec": sales()})
    too_many = base | {f"versions/{n:04d}.json": version for n in range(1, project.MAX_VERSIONS + 4)}
    with pytest.raises(ProjectError, match="more files"):
        project.import_(_zip(too_many), tmp_path / "a")
    assert not (tmp_path / "a").exists()
    monkeypatch.setattr(project, "MAX_VERSIONS", 3)
    dest = project.import_(_zip(base | {f"versions/{n:04d}.json": version for n in range(1, 6)}), tmp_path / "b")
    assert len(list((dest / "versions").glob("*.json"))) == 3
