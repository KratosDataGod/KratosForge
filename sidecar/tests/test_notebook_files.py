"""Notebook files: search inside notebooks, open a file from anywhere, save a copy, delete to
Recently deleted and restore, copy a Fabric notebook here, and the check before a Publish that
would replace changes made in Fabric. Called the way the app calls them."""

from __future__ import annotations

import base64
import datetime as dt
import json

import pytest

from fabric_desktop import audit, notebook_files
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.notebook import NotebookStore
from fabric_desktop.server import handle
from fabric_desktop.service import Service

LAKEHOUSE = {"workspace_id": "ws1", "workspace_name": "Sales WS", "lakehouse_id": "lh1", "name": "Sales_Lakehouse"}


def ipynb(cells, lakehouse=None, kernel="python3"):
    meta = {"kernelspec": {"name": kernel}}
    if lakehouse:
        meta["dependencies"] = {"lakehouse": {"default_lakehouse": lakehouse["lakehouse_id"],
                                              "default_lakehouse_name": lakehouse["name"],
                                              "default_lakehouse_workspace_id": lakehouse["workspace_id"]}}
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": meta,
            "cells": [{"cell_type": t, "id": f"c{i}", "metadata": {}, "source": s,
                       **({"outputs": [{"output_type": "stream", "text": "x"}], "execution_count": 1} if t == "code" else {})}
                      for i, (t, s) in enumerate(cells)]}


class FakeFabric:
    def __init__(self):
        self.items: dict[str, dict] = {}   # item id -> {"ws", "name", "ipynb"}
        self.saved = []
        self.definition_reads = 0

    def add(self, item_id, ws, name, nb):
        self.items[item_id] = {"ws": ws, "name": name, "ipynb": nb}

    def get_item_definition(self, ws, item_id, fmt=None):
        self.definition_reads += 1
        it = self.items.get(item_id)
        if not it or it["ws"] != ws:
            raise FabricError("not found", status=404)
        return {"definition": {"parts": [{"path": "notebook-content.ipynb",
                                          "payload": base64.b64encode(json.dumps(it["ipynb"]).encode()).decode()}]}}

    def save_notebook(self, ws, name, data):
        self.saved.append((ws, name))
        item_id = next((i for i, it in self.items.items() if it["ws"] == ws and it["name"] == name), f"nb-{len(self.items) + 1}")
        self.items[item_id] = {"ws": ws, "name": name, "ipynb": json.loads(data)}
        return {"id": item_id, "displayName": name}

    def list_notebooks(self, ws):
        return [{"id": i, "displayName": it["name"]} for i, it in self.items.items() if it["ws"] == ws]

    def get_notebook(self, ws, item_id):
        it = self.items.get(item_id)
        return {"id": item_id, "displayName": it["name"], "workspaceId": ws} if it and it["ws"] == ws else None

    def update_notebook(self, ws, item, name, data):
        self.saved.append((ws, name))
        self.items[item["id"]] = {"ws": ws, "name": name, "ipynb": json.loads(data)}
        return {"id": item["id"], "displayName": name}

    def get_lakehouse(self, ws, lh):
        return {"properties": {"defaultSchema": "dbo"}}

    def run_item_job(self, ws, item, job, execution_data=None):
        return "job1"


class FilesService(Service):
    fake = None

    @property
    def fabric(self):
        return self.fake

    @property
    def fabric_jobs(self):
        return self.fake


@pytest.fixture
def svc(tmp_path):
    s = FilesService(root=tmp_path)
    s.fake = FakeFabric()
    return s


def call(svc, method, **params):
    out = handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" not in out, out["error"]
    return out["result"]


def error(svc, method, **params):
    out = handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" in out, out
    return out["error"]


def save(svc, name, *sources, lakehouse=None):
    svc.notebooks.save(name, [{"type": "code", "source": s} for s in sources], lakehouse)


def test_search_finds_names_and_code_and_says_where(svc):
    save(svc, "Daily sales", "orders = 1", "by_region = orders.join(region_map)", "region_map = 2")
    save(svc, "Region map load", "lakehouse.write_table(df, 'dbo.region_map')")
    save(svc, "Other", "print('nothing here')")
    out = call(svc, "notebook_search", query="Region_Map")
    found = {n["name"]: n for n in out["notebooks"]}
    assert set(found) == {"Daily sales", "Region map load"} and out["searched"] == 3
    hits = found["Daily sales"]["hits"]
    assert [(h["cell"], h["line"]) for h in hits] == [(2, 1), (3, 1)]
    h = hits[0]
    assert h["text"][h["start"]:h["end"]] == "region_map"
    assert found["Region map load"]["name_match"] is False and len(found["Region map load"]["hits"]) == 1
    # Names only skips cell code.
    assert [n["name"] for n in call(svc, "notebook_search", query="map", names_only=True)["notebooks"]] == ["Region map load"]


def test_search_uses_the_open_notebooks_unsaved_cells_and_cuts_long_lines(svc):
    save(svc, "NB", "old = 1")
    long = "x = " + "a" * 300 + " needle " + "b" * 300
    out = call(svc, "notebook_search", query="needle", open_notebook={"name": "NB", "cells": [{"type": "code", "source": long}]})
    [h] = out["notebooks"][0]["hits"]
    assert len(h["text"]) <= notebook_files.LINE_CHARS + 2 and h["text"][h["start"]:h["end"]] == "needle"
    assert call(svc, "notebook_search", query="")["notebooks"] == []


def test_save_a_copy_keeps_cells_and_never_replaces_a_notebook(svc):
    svc.notebooks.save("Daily sales", [{"type": "code", "source": "x = 1"}], LAKEHOUSE, "local",
                       {"workspace_id": "ws1", "workspace_name": "Sales WS"})
    out = call(svc, "notebook_save_copy", name="Daily sales", new_name="Daily sales 2")
    copy = svc.notebooks.load("Daily sales 2")
    assert out["name"] == "Daily sales 2" and copy["cells"][0]["source"] == "x = 1"
    assert copy["lakehouse"] == LAKEHOUSE and copy["compute"] == "local" and copy["workspace"]["workspace_id"] == "ws1"
    save(svc, "Taken", "keep = 1")
    assert "already exists" in error(svc, "notebook_save_copy", name="Daily sales", new_name="Taken")
    assert svc.notebooks.load("Taken")["cells"][0]["source"] == "keep = 1"


def test_opening_a_file_adds_a_copy_with_fabrics_lakehouse_and_drops_outputs(svc):
    content = json.dumps(ipynb([("code", "df = spark.read.table('dbo.orders')"), ("markdown", "# Notes"),
                                ("code", "# Fabric Desktop setup\nimport x"), ("code", "print(1)")], LAKEHOUSE))
    save(svc, "finance_close", "mine = 1")
    check = call(svc, "notebook_import_check", file_name=r"C:\Users\c\Downloads\finance_close.ipynb", content=content)
    assert check["name"] == "finance_close 2" and check["taken"] and check["cells"] == 3
    assert check["lakehouse"] == {"workspace_id": "ws1", "lakehouse_id": "lh1", "name": "Sales_Lakehouse"}
    assert check["outputs_dropped"] and any("Spark" in n for n in check["notes"]) and any("setup cell" in n for n in check["notes"])
    assert not (svc.root / "notebooks" / "finance_close 2.ipynb").exists()  # the check wrote nothing
    out = call(svc, "notebook_import", content=content, name=check["name"])
    assert [c["source"] for c in out["cells"]] == ["df = spark.read.table('dbo.orders')", "# Notes", "print(1)"]
    saved = json.loads((svc.root / "notebooks" / "finance_close 2.ipynb").read_text())
    assert all(not c.get("outputs") for c in saved["cells"])
    assert svc.notebooks.load("finance_close")["cells"][0]["source"] == "mine = 1"


@pytest.mark.parametrize("content, text", [
    ("not json", "isn't JSON"), ("[]", "no cells"), ('{"cells": [], "nbformat": 3}', "format 4"),
    ('{"cells": ["x"], "nbformat": 4}', "isn't readable"),
])
def test_a_file_that_isnt_a_notebook_is_refused(svc, content, text):
    assert text in error(svc, "notebook_import_check", file_name="a.ipynb", content=content)
    assert text in error(svc, "notebook_import", content=content, name="A")
    assert not (svc.root / "notebooks").exists() or not list((svc.root / "notebooks").glob("*.ipynb"))


def test_a_name_taken_between_check_and_save_isnt_overwritten(svc, monkeypatch):
    content = json.dumps(ipynb([("code", "new = 1")]))
    taken = {"n": 0}
    real = svc.notebooks._taken

    def racing(name):  # someone saves "Raced" right after the name check
        taken["n"] += 1
        if taken["n"] == 1:
            (svc.root / "notebooks").mkdir(exist_ok=True)
            NotebookStore(svc.root / "notebooks").save("Raced", [{"type": "code", "source": "theirs = 1"}])
            return False
        return real(name)
    monkeypatch.setattr(svc.notebooks, "_taken", racing)
    assert "already exists" in error(svc, "notebook_import", content=content, name="Raced")
    assert svc.notebooks.load("Raced")["cells"][0]["source"] == "theirs = 1"


def test_delete_goes_to_recently_deleted_turns_its_schedule_off_and_restore_brings_both_back(svc):
    save(svc, "Daily sales", "x = 1")
    sched = call(svc, "add_pc_schedule", target={"kind": "notebook", "name": "Daily sales"},
                 schedule={"kind": "daily", "times": ["06:00"]})["schedule"]
    check = call(svc, "notebook_delete_check", name="Daily sales")
    assert [s["id"] for s in check["schedules"]] == [sched["id"]] and check["fabric_item"] is None and check["keep_days"] == 30
    out = call(svc, "notebook_delete", name="Daily sales")
    assert out["schedules_off"] == [sched["id"]]
    assert "Daily sales" not in [n["name"] for n in call(svc, "list_notebooks")]
    assert not call(svc, "pc_schedules_for", kind="notebook", name="Daily sales")[0]["enabled"]
    [gone] = call(svc, "notebook_deleted")
    assert gone["name"] == "Daily sales" and gone["schedules"] == 1
    back = call(svc, "notebook_restore", id=gone["id"])
    assert back["name"] == "Daily sales" and back["schedules_on"] == [sched["id"]]
    assert svc.notebooks.load("Daily sales")["cells"][0]["source"] == "x = 1"
    assert call(svc, "pc_schedules_for", kind="notebook", name="Daily sales")[0]["enabled"]
    assert call(svc, "notebook_deleted") == []


def test_restore_never_replaces_a_notebook_and_a_new_name_leaves_the_schedule_off(svc):
    save(svc, "NB", "old = 1")
    sched = call(svc, "add_pc_schedule", target={"kind": "notebook", "name": "NB"}, schedule={"kind": "daily", "times": ["06:00"]})["schedule"]
    tid = call(svc, "notebook_delete", name="NB")["id"]
    save(svc, "NB", "new = 1")  # a new notebook took the name
    assert "already exists" in error(svc, "notebook_restore", id=tid)
    assert svc.notebooks.load("NB")["cells"][0]["source"] == "new = 1"
    back = call(svc, "notebook_restore", id=tid, name="NB old")
    assert back["renamed"] and back["schedules_left_off"] == [sched["id"]] and back["schedules_on"] == []
    assert svc.notebooks.load("NB old")["cells"][0]["source"] == "old = 1"
    assert "isn't a deleted notebook" in error(svc, "notebook_restore", id="../../x")


def test_deleted_notebooks_are_kept_thirty_days(svc, monkeypatch):
    save(svc, "Old", "x = 1")
    call(svc, "notebook_delete", name="Old")
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=31)
    monkeypatch.setattr(notebook_files, "_now", lambda: later)
    assert call(svc, "notebook_deleted") == []
    save(svc, "Next", "y = 1")
    call(svc, "notebook_delete", name="Next")  # a delete clears what's past its time
    trash = svc.root / "notebooks-deleted"
    assert len(list(trash.glob("*.ipynb"))) == 1


def test_delete_api_name_also_goes_to_recently_deleted(svc):
    save(svc, "NB", "x = 1")
    assert svc.delete_notebook("NB")["deleted"] == "NB"  # in-process: the RPC takes notebook_delete
    assert [d["name"] for d in call(svc, "notebook_deleted")] == ["NB"]


def test_edit_a_fabric_notebook_here_links_it_and_publish_updates_the_same_item(svc):
    svc.fake.add("item-1", "ws1", "Monthly margin", ipynb([("code", "df = spark.read.table('dbo.orders')"),
                                                         ("code", "m = df.groupBy('month')")], LAKEHOUSE, "synapse_pyspark"))
    check = call(svc, "notebook_fabric_check", workspace_id="ws1", item_id="item-1", name="Monthly margin")
    assert check["here"] is None and check["taken"] is False and check["cells"] == 2
    assert svc.fake.saved == []  # nothing written to Fabric
    out = call(svc, "notebook_from_fabric", workspace_id="ws1", item_id="item-1", name="Monthly margin", workspace_name="Sales WS")
    nb = svc.notebooks.load("Monthly margin")
    assert out["name"] == "Monthly margin" and nb["workspace"] == {"workspace_id": "ws1", "workspace_name": "Sales WS"}
    assert nb["lakehouse"]["lakehouse_id"] == "lh1" and svc.fake.saved == []
    # Asking again opens the one already here instead of copying twice.
    assert call(svc, "notebook_from_fabric", workspace_id="ws1", item_id="item-1", name="Monthly margin") == \
        {"exists": True, "name": "Monthly margin"}
    # Nothing changed in Fabric: Publish goes ahead and updates the same item.
    assert call(svc, "notebook_publish_check", name="Monthly margin", cells=nb["cells"], lakehouse_ref=nb["lakehouse"],
                workspace=nb["workspace"])["changed"] is False
    pub = call(svc, "publish_notebook", name="Monthly margin", cells=nb["cells"], lakehouse_ref=nb["lakehouse"],
               workspace=nb["workspace"])
    assert pub["id"] == "item-1" and len(svc.fake.items) == 1
    [row] = call(svc, "notebook_listing")
    assert row["name"] == "Monthly margin" and row["published_to"] == {"workspace_id": "ws1", "workspace_name": "Sales WS"}


def test_publish_stops_when_the_item_changed_in_fabric_and_says_what_changed(svc):
    svc.fake.add("item-1", "ws1", "Monthly margin", ipynb([("code", "a = 1"), ("code", "b = 2")]))
    call(svc, "notebook_from_fabric", workspace_id="ws1", item_id="item-1", name="Monthly margin", workspace_name="Sales WS")
    nb = svc.notebooks.load("Monthly margin")
    # Dana edits cell 2 and adds a cell in Fabric; here cell 1 is edited.
    svc.fake.items["item-1"]["ipynb"]["cells"][1]["source"] = "b = 3"
    svc.fake.items["item-1"]["ipynb"]["cells"].append({"cell_type": "code", "id": "c9", "metadata": {}, "source": "c = 4"})
    mine = [dict(nb["cells"][0], source="a = 10"), nb["cells"][1]]
    args = {"name": "Monthly margin", "cells": mine, "lakehouse_ref": None, "workspace": nb["workspace"]}
    check = call(svc, "notebook_publish_check", **args)
    assert check["changed"] and {c["kind"] for c in check["fabric"]} == {"changed", "added"}
    assert [c["kind"] for c in check["mine"]] == ["changed"]
    for method in ("publish_notebook", "run_notebook_in_fabric"):
        assert "changed in Fabric" in error(svc, method, **args)
    assert svc.fake.saved == [] and svc.fake.items["item-1"]["ipynb"]["cells"][1]["source"] == "b = 3"
    # Keep Fabric's version first, as a notebook of its own that isn't linked to the item.
    kept = call(svc, "notebook_from_fabric", workspace_id="ws1", item_id="item-1", name="Monthly margin",
                workspace_name="Sales WS", copy_name="Monthly margin (Fabric)")
    assert [c["source"] for c in kept["cells"]] == ["a = 1", "b = 3", "c = 4"]
    assert "Monthly margin (Fabric)" not in svc.workload_store.links()
    # The person chose Replace with mine.
    call(svc, "publish_notebook", **args, replace_fabric_changes=True)
    assert ["".join(c["source"]) for c in svc.fake.items["item-1"]["ipynb"]["cells"]] == ["a = 10", "b = 2"]
    # ...and the next publish compares with what was just sent.
    assert call(svc, "notebook_publish_check", **args)["changed"] is False


def test_a_notebook_published_from_here_is_checked_too_and_a_deleted_item_is_made_again(svc):
    save(svc, "NB", "x = 1")
    args = {"name": "NB", "cells": [{"type": "code", "source": "x = 1"}], "lakehouse_ref": None,
            "workspace": {"workspace_id": "ws1", "workspace_name": "Sales WS"}}
    first = call(svc, "publish_notebook", **args)
    svc.fake.items[first["id"]]["ipynb"]["cells"][0]["source"] = "x = 2"
    assert "changed in Fabric" in error(svc, "publish_notebook", **args)
    del svc.fake.items[first["id"]]  # deleted in Fabric
    assert call(svc, "notebook_publish_check", **args)["changed"] is False
    call(svc, "publish_notebook", **args)


def test_publish_updates_the_linked_item_when_it_was_renamed_in_fabric(svc):
    svc.fake.add("item-1", "ws1", "Monthly margin", ipynb([("code", "a = 1")]))
    call(svc, "notebook_from_fabric", workspace_id="ws1", item_id="item-1", name="Monthly margin", workspace_name="WS")
    nb = svc.notebooks.load("Monthly margin")
    # In Fabric: the linked item is renamed and Dana makes a new one under the old name.
    svc.fake.items["item-1"]["name"] = "Monthly margin (old)"
    svc.fake.add("item-2", "ws1", "Monthly margin", ipynb([("code", "dana = 'her work'")]))
    args = {"name": "Monthly margin", "cells": [dict(nb["cells"][0], source="a = 10")], "lakehouse_ref": None,
            "workspace": nb["workspace"]}
    # Publish writes the item it is linked to, by id (PB-018), so Dana's notebook isn't in the way.
    assert call(svc, "notebook_publish_check", **args)["changed"] is False
    out = call(svc, "publish_notebook", **args)
    assert out["id"] == "item-1" and out["item_name"] == "Monthly margin (old)"  # Fabric's rename stays
    assert ["".join(c["source"]) for c in svc.fake.items["item-1"]["ipynb"]["cells"]] == ["a = 10"]
    assert ["".join(c["source"]) for c in svc.fake.items["item-2"]["ipynb"]["cells"]] == ["dana = 'her work'"]


def test_when_the_linked_item_is_gone_publish_checks_the_one_with_its_name(svc):
    svc.fake.add("item-1", "ws1", "Monthly margin", ipynb([("code", "a = 1")]))
    call(svc, "notebook_from_fabric", workspace_id="ws1", item_id="item-1", name="Monthly margin", workspace_name="WS")
    nb = svc.notebooks.load("Monthly margin")
    del svc.fake.items["item-1"]  # deleted in Fabric, and Dana makes one under the name
    svc.fake.add("item-2", "ws1", "Monthly margin", ipynb([("code", "dana = 'her work'")]))
    args = {"name": "Monthly margin", "cells": [dict(nb["cells"][0], source="a = 10")], "lakehouse_ref": None,
            "workspace": nb["workspace"]}
    check = call(svc, "notebook_publish_check", **args)
    assert check["changed"] and check["item_id"] == "item-2" and check["fabric"][0]["kind"] == "other item"
    assert "changed in Fabric" in error(svc, "publish_notebook", **args)
    assert ["".join(c["source"]) for c in svc.fake.items["item-2"]["ipynb"]["cells"]] == ["dana = 'her work'"]
    # Copy Fabric's version keeps Dana's notebook here, from the item Publish would replace.
    kept = call(svc, "notebook_from_fabric", workspace_id="ws1", item_id=check["item_id"], name="Monthly margin",
                workspace_name="WS", copy_name="Monthly margin (Fabric)")
    assert [c["source"] for c in kept["cells"]] == ["dana = 'her work'"]
    # Gone with no new item under the name: Publish makes a new item and says why.
    del svc.fake.items["item-2"]
    assert call(svc, "notebook_publish_check", **args)["changed"] is False
    out = call(svc, "publish_notebook", **args)
    assert out["id"] not in ("item-1", "item-2") and "was deleted, so a new one was made" in out["note"]


def test_publish_renames_the_fabric_item_after_a_rename_here_and_makes_no_second_one(svc):
    save(svc, "NB", "x = 1")
    ws = {"workspace_id": "ws1", "workspace_name": "Sales WS"}
    first = call(svc, "publish_notebook", name="NB", cells=[{"type": "code", "source": "x = 1"}], lakehouse_ref=None, workspace=ws)
    call(svc, "rename_notebook", name="NB", new_name="Daily load")
    out = call(svc, "publish_notebook", name="Daily load", cells=[{"type": "code", "source": "x = 2"}],
               lakehouse_ref=None, workspace=ws)
    assert out["id"] == first["id"] and out["item_name"] == "Daily load" and out["note"] == ""
    assert [it["name"] for it in svc.fake.items.values()] == ["Daily load"]


def test_a_failed_change_check_stops_the_publish(svc, monkeypatch):
    save(svc, "NB", "x = 1")
    args = {"name": "NB", "cells": [{"type": "code", "source": "x = 1"}], "lakehouse_ref": None,
            "workspace": {"workspace_id": "ws1", "workspace_name": "Sales WS"}}
    call(svc, "publish_notebook", **args)

    def down(*a, **k):
        raise FabricError("service unavailable", status=503)
    monkeypatch.setattr(svc.fake, "get_item_definition", down)
    assert "Couldn't check whether NB changed in Fabric" in error(svc, "publish_notebook", **args)
    assert len(svc.fake.saved) == 1


def test_the_reads_are_read_only_in_the_audit_trail():
    assert {"notebook_listing", "notebook_search", "notebook_import_check", "notebook_delete_check", "notebook_deleted",
            "notebook_fabric_check", "notebook_publish_check"} <= audit.READ_ONLY
    for m in ("notebook_save_copy", "notebook_import", "notebook_delete", "notebook_restore", "notebook_from_fabric"):
        assert audit.audited(m)


def test_where_a_notebook_is_for_vs_code_and_its_disk_stamp(svc):
    save(svc, "Sales", "x = 1")
    where = call(svc, "notebook_file_path", name="Sales")
    assert where["path"].endswith("Sales.ipynb") and where["repo_root"] is None
    assert where["folder"] == where["notebooks_folder"]
    stamp = call(svc, "notebook_disk_stamp", name="Sales")["disk_stamp"]
    assert stamp == call(svc, "open_notebook", name="Sales")["disk_stamp"]
    path = svc.notebooks.path("Sales")
    nb = json.loads(path.read_text())
    nb["cells"][0]["source"] = "x = 2"
    path.write_text(json.dumps(nb))  # saved in VS Code
    assert call(svc, "notebook_disk_stamp", name="Sales")["disk_stamp"] not in (None, stamp)
    path.write_text("<<<<<<< HEAD")
    assert call(svc, "notebook_disk_stamp", name="Sales")["disk_stamp"] is None
    assert "no notebook" in error(svc, "notebook_file_path", name="Nope")
    assert not audit.audited("notebook_file_path") and not audit.audited("notebook_disk_stamp")
