"""App Studio's engine methods (AS-06/07/08): projects under the data folder named by folder name only,
Design changes with undo and redo, versions, Save As, export and import, templates and the local preview.
Nothing here reaches a model or Fabric."""

from __future__ import annotations

import base64
import os

import pytest

from fabric_desktop import audit, server
from fabric_desktop.appstudio import sample, templates
from fabric_desktop.appstudio import spec as specs
from fabric_desktop.appstudio_service import METHODS, AppStudioMixin


class _Svc(AppStudioMixin):
    def __init__(self, root):
        self.root = root


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    return _Svc(tmp_path / "data")


def test_methods_are_served_and_only_the_reads_skip_the_audit():
    assert METHODS <= server.METHODS
    reads = {"appstudio_home", "appstudio_open", "appstudio_history", "appstudio_versions", "appstudio_preview",
             "appstudio_connections"}
    for m in METHODS:
        assert audit.audited(m) is (m not in reads), m


@pytest.mark.parametrize("template", sorted(templates.TEMPLATES))
@pytest.mark.parametrize("target", sorted(specs.TARGETS))
def test_every_template_is_valid_for_every_target(template, target):
    doc = templates.build(template, "My app", target)
    assert specs.validate(doc) == []
    assert doc["target"] == target
    if target == "kratosforge-app":
        assert all(i["kind"] == "cross-filter" for i in doc["interactions"])


def test_create_lists_opens_and_refuses_a_second_project_with_the_same_name(svc):
    got = svc.appstudio_create("Sales overview", "sales")
    assert got["project"] == "Sales overview.KFApp" and got["valid"] and "path" not in got
    assert got["spec"]["pages"][0]["title"] == "Overview"
    home = svc.appstudio_home()
    assert [p["project"] for p in home["projects"]] == ["Sales overview.KFApp"]
    assert {t["id"] for t in home["templates"]} == {"blank", "sales", "operations"}
    assert {t["id"] for t in home["targets"]} == set(specs.TARGETS)
    with pytest.raises(ValueError, match="already exists"):
        svc.appstudio_create("Sales overview", "blank")
    with pytest.raises(ValueError, match="name"):
        svc.appstudio_create("   ")
    with pytest.raises(ValueError, match="template"):
        svc.appstudio_create("X", "nope")


@pytest.mark.parametrize("bad", ["../x.KFApp", "..", "Sales overview", "a/b.KFApp", "C:\\x.KFApp", "Missing.KFApp",
                                 "/etc.KFApp", "Sales  overview.KFApp"])
def test_a_project_is_named_by_its_folder_only(svc, bad):
    svc.appstudio_create("Sales overview", "sales")
    with pytest.raises(ValueError):
        svc.appstudio_open(bad)


def test_a_symlinked_project_folder_is_refused(svc, tmp_path):
    svc.appstudio_create("Real", "blank")
    root = svc._appstudio_root()
    try:
        os.symlink(root / "Real.KFApp", root / "Link.KFApp", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks aren't available here")
    with pytest.raises(ValueError):
        svc.appstudio_open("Link.KFApp")


def test_design_changes_undo_redo_and_history(svc):
    p = svc.appstudio_create("Ops", "operations")["project"]
    got = svc.appstudio_change(p, [{"op": "set", "id": "c_late", "field": "title", "value": "Late orders"}], note="Rename")
    assert svc.appstudio_open(p)["spec"]["pages"][0]["components"][1]["title"] == "Late orders"
    assert got["can_undo"] and not got["can_redo"]
    back = svc.appstudio_undo(p)
    assert back["spec"]["pages"][0]["components"][1]["title"] == "Late"
    assert back["can_redo"]
    assert svc.appstudio_redo(p)["spec"]["pages"][0]["components"][1]["title"] == "Late orders"
    hist = svc.appstudio_history(p)
    assert [h["kind"] for h in hist] == ["redo", "undo", "change"]
    assert hist[-1]["source"] == "visual" and hist[-1]["note"] == "Rename"
    assert all("ops" not in h and "inverse" not in h for h in hist)


def test_a_bad_change_saves_nothing_and_ui_cannot_claim_to_be_the_model(svc):
    p = svc.appstudio_create("Ops", "operations")["project"]
    before = svc.appstudio_open(p)["spec"]
    with pytest.raises(ValueError):
        svc.appstudio_change(p, [{"op": "set", "id": "c_late", "field": "type", "value": "hologram"}])
    with pytest.raises(ValueError, match="source"):
        svc.appstudio_change(p, [{"op": "set", "id": "c_late", "field": "title", "value": "x"}], source="ai")
    with pytest.raises(ValueError):
        svc.appstudio_change(p, [{"op": "set", "id": "c_late", "field": "title",
                                  "value": "sk-ant-" + "a" * 40}])
    assert svc.appstudio_open(p)["spec"] == before
    assert svc.appstudio_history(p) == []


def test_versions_restore_save_as_and_clone(svc):
    p = svc.appstudio_create("Sales", "sales")["project"]
    v = svc.appstudio_save_version(p, "First")
    assert v["version"] == 1
    svc.appstudio_change(p, [{"op": "remove", "id": "c_table"}])
    got = svc.appstudio_restore_version(p, 1)
    assert got["kept_as"] == 2
    assert any(c["id"] == "c_table" for c in got["spec"]["pages"][1]["components"])
    assert [x["version"] for x in svc.appstudio_versions(p)] == [2, 1]
    copy = svc.appstudio_save_as(p, "Sales copy")
    assert copy["project"] == "Sales copy.KFApp" and len(svc.appstudio_versions(copy["project"])) == 2
    clone = svc.appstudio_save_as(p, "Sales clone", clone=True)
    assert svc.appstudio_versions(clone["project"]) == [] and svc.appstudio_history(clone["project"]) == []
    assert clone["meta"]["app_id"] != copy["meta"]["app_id"]


def test_export_writes_a_new_zip_in_downloads_and_import_makes_a_new_project(svc, tmp_path):
    p = svc.appstudio_create("Sales", "sales")["project"]
    out = svc.appstudio_export(p)
    again = svc.appstudio_export(p)
    assert out["path"] != again["path"], "an export never replaces a file"
    assert out["path"].startswith(str(tmp_path / "home" / "Downloads"))
    data = open(out["path"], "rb").read()
    got = svc.appstudio_import(base64.b64encode(data).decode(), "Imported sales")
    assert got["project"] == "Imported sales.KFApp" and got["valid"]
    with pytest.raises(ValueError, match="isn't an App Studio export"):
        svc.appstudio_import("not base64!!")
    with pytest.raises(ValueError):
        svc.appstudio_import(base64.b64encode(b"not a zip").decode())


def test_preview_is_sample_data_and_a_slicer_narrows_the_page(svc):
    p = svc.appstudio_create("Sales", "sales")["project"]
    first = svc.appstudio_preview(p)
    assert first["simulated"] is True and "Sample numbers" in first["note"]
    by_id = {c["id"]: c for c in first["components"]}
    assert by_id["c_slicer"]["values"] == sample.categories("Region[Name]")
    assert set(by_id["c_region"]["labels"]) == set(sample.categories("Region[Name]"))
    assert by_id["c_month"]["labels"][0] == "Jan"
    vals = by_id["c_region"]["series"][0]["values"]
    assert vals == sorted(vals, reverse=True), "sort desc is shown"
    assert svc.appstudio_preview(p) == first, "the same numbers each time"
    narrowed = svc.appstudio_preview(p, "pg_overview", {"Region[Name]": "East"})
    nb = {c["id"]: c for c in narrowed["components"]}
    assert nb["c_region"]["labels"] == ["East"] and nb["c_slicer"]["selected"] == "East"
    assert nb["c_rev"]["value"] < by_id["c_rev"]["value"]
    assert sum(nb["c_month"]["series"][0]["values"]) < sum(by_id["c_month"]["series"][0]["values"])
    other = svc.appstudio_preview(p, "pg_products")
    assert other["page"]["title"] == "Products"
    # A choice that isn't a category, or isn't a field, is ignored.
    assert svc.appstudio_preview(p, "", {"Region[Name]": "Mars", "DROP": "x"})["selection"] == {}


def test_text_components_show_their_text():
    doc = templates.build("blank", "T")
    doc["pages"][0]["components"].append({"id": "c_t", "type": "text", "title": "Hello", "measures": [], "fields": [],
                                          "layout": {"x": 0, "y": 0, "w": 12, "h": 1}, "props": {"text": "Welcome"}})
    assert sample.preview(doc)["components"][0]["text"] == "Welcome"


@pytest.mark.parametrize("name", ["CON", "nul", "Com1", "LPT9"])
def test_windows_device_names_are_refused(svc, name):
    with pytest.raises(ValueError, match="device"):
        svc.appstudio_create(name)
    assert svc.appstudio_create(f"{name} report")["project"] == f"{name} report.KFApp"


def test_a_folder_that_resolves_outside_the_projects_folder_is_refused(svc, monkeypatch, tmp_path):
    svc.appstudio_create("Real", "blank")
    from pathlib import Path
    real = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: tmp_path / "elsewhere" / self.name
                        if self.name == "Real.KFApp" else real(self, *a, **k))
    with pytest.raises(ValueError, match="isn't in your App Studio projects"):
        svc.appstudio_open("Real.KFApp")
