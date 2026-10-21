"""Folders for notebooks (lifecycle parity row 14): a name like "Sales/Daily load" is a notebook in a
folder. New, rename and delete (empty only) folders, move a notebook into one, and everything kept by
name follows. Called the way the app calls them."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import audit, notebook_versions
from fabric_desktop.local_schedules import LocalSchedules
from fabric_desktop.notebook import MAX_FOLDER_DEPTH, check_notebook_name, leaf_name
from fabric_desktop.server import METHODS, handle
from fabric_desktop.service import Service

from test_git import git_home  # noqa: F401  (an autouse fixture: a Git configuration of its own)
from test_notebook_files import FakeFabric, FilesService


class FakeScheduler:
    available = True

    def __init__(self):
        self.tasks = {}

    def register(self, schedule, executable, workdir):
        self.tasks[schedule["id"]] = schedule["target"]

    def unregister(self, schedule_id):
        self.tasks.pop(schedule_id, None)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fake = FakeScheduler()
    monkeypatch.setattr(Service, "pc_schedules", property(lambda self: LocalSchedules(self.root, fake, "C:/engine/python.exe")))
    s = FilesService(root=tmp_path / "app")
    s.fake = FakeFabric()
    s.fake_scheduler = fake
    return s


def call(svc, method, **params):
    out = handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" not in out, out["error"]
    return out["result"]


def error(svc, method, **params):
    out = handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" in out, out
    return out["error"]


def save(svc, name, source="x = 1"):
    svc.save_notebook(name, [{"type": "code", "source": source}])


def test_a_name_with_folders_is_a_notebook_in_subfolders(svc):
    assert check_notebook_name(" Sales / Daily load ") == "Sales/Daily load"
    assert leaf_name("Sales/Archive/Q1") == "Q1"
    for bad in ("Sales//X", "Sales/.git/X", "Sales/X.resources/Y", "a/b/c/d/X", "Sales\\X", "../X", "Sales/../X"):
        with pytest.raises(ValueError):
            check_notebook_name(bad)
    assert MAX_FOLDER_DEPTH == 3
    save(svc, "Sales/Archive/Q1 backfill")
    save(svc, "Top")
    assert (svc.notebooks.folder / "Sales" / "Archive" / "Q1 backfill.ipynb").is_file()
    assert {n["name"] for n in call(svc, "list_notebooks")} == {"Sales/Archive/Q1 backfill", "Top"}
    assert call(svc, "open_notebook", name="Sales/Archive/Q1 backfill")["cells"][0]["source"] == "x = 1"


def test_listing_skips_resources_and_hidden_folders(svc):
    save(svc, "Sales/Daily load")
    res = svc.notebooks.resources("Sales/Daily load")
    (res / "builtin").mkdir(parents=True)
    (res / "builtin" / "helper.ipynb").write_text("{}", encoding="utf-8")  # a file kept as a resource
    (svc.notebooks.folder / ".ipynb_checkpoints").mkdir()
    (svc.notebooks.folder / ".ipynb_checkpoints" / "Top-checkpoint.ipynb").write_text("{}", encoding="utf-8")
    assert [n["name"] for n in svc.list_notebooks()] == ["Sales/Daily load"]
    folders = call(svc, "notebook_folders")
    assert [(f["path"], f["notebooks"]) for f in folders["folders"]] == [("Sales", 1)] and folders["max_depth"] == 3


def test_new_folder_and_new_notebook_in_it(svc):
    out = call(svc, "notebook_folder_create", path="Finance/Month end")
    assert out == {"path": "Finance/Month end", "place": None}
    assert "already in the app's notebooks folder" in error(svc, "notebook_folder_create", path="Finance")
    assert "levels deep" in error(svc, "notebook_folder_create", path="a/b/c/d")
    assert "resources" in error(svc, "notebook_folder_create", path="X.resources")
    rows = {f["path"]: f for f in call(svc, "notebook_folders")["folders"]}
    assert rows["Finance"]["notebooks"] == 0 and rows["Finance/Month end"]["parent"] == "Finance"
    nb = call(svc, "new_notebook", folder="Finance")
    assert nb["name"] == "Finance/Notebook 1"
    assert call(svc, "new_notebook", folder="Finance")["name"] == "Finance/Notebook 2"
    assert call(svc, "new_notebook")["name"] == "Notebook 1"  # a name is the whole path, so "Notebook 1" is free


def test_move_to_folder_takes_resources_versions_schedules_and_link(svc):
    save(svc, "Daily load", "a = 1")
    save(svc, "Daily load", "a = 2")
    (svc.notebooks.resources("Daily load") / "builtin").mkdir(parents=True)
    (svc.notebooks.resources("Daily load") / "builtin" / "map.csv").write_text("k,v", encoding="utf-8")
    sched = svc.add_pc_schedule({"kind": "notebook", "name": "Daily load"}, {"kind": "daily", "times": ["06:00"]})["schedule"]
    svc.workload_store.record_link("Daily load", "ws1", "item-1", "Sales WS", how="recorded", by="")
    versions_before = len(call(svc, "notebook_versions", name="Daily load")["versions"])
    call(svc, "notebook_folder_create", path="Sales")
    out = call(svc, "notebook_move_to_folder", name="Daily load", folder="Sales")
    assert out == {"name": "Sales/Daily load", "was": "Daily load", "moved": True, "schedules_moved": [sched["id"]]}
    folder = svc.notebooks.folder
    assert not (folder / "Daily load.ipynb").exists()
    assert (folder / "Sales" / "Daily load.resources" / "builtin" / "map.csv").read_text(encoding="utf-8") == "k,v"
    assert len(call(svc, "notebook_versions", name="Sales/Daily load")["versions"]) == versions_before
    assert svc.pc_schedules_for("notebook", "Sales/Daily load")[0]["id"] == sched["id"]
    assert "Sales/Daily load" in svc.workload_store.links()
    # And back out of the folder.
    assert call(svc, "notebook_move_to_folder", name="Sales/Daily load", folder="")["name"] == "Daily load"
    assert (folder / "Daily load.resources" / "builtin" / "map.csv").is_file()


def test_move_never_replaces_a_notebook_and_needs_the_folder(svc):
    save(svc, "Report", "mine = 1")
    save(svc, "Sales/Report", "theirs = 1")
    assert "Sales already has a notebook named 'Report'. Nothing was moved." in error(
        svc, "notebook_move_to_folder", name="Report", folder="Sales")
    assert "theirs" in (svc.notebooks.folder / "Sales" / "Report.ipynb").read_text(encoding="utf-8")
    assert "mine" in (svc.notebooks.folder / "Report.ipynb").read_text(encoding="utf-8")
    assert "Make it with New folder first" in error(svc, "notebook_move_to_folder", name="Report", folder="Nope")
    assert call(svc, "notebook_move_to_folder", name="Report", folder="")["moved"] is False


def test_rename_folder_moves_its_notebooks_and_what_follows_them(svc):
    save(svc, "Sales/Daily load")
    save(svc, "Sales/Archive/Q1")
    (svc.notebooks.resources("Sales/Daily load")).mkdir()
    sched = svc.add_pc_schedule({"kind": "notebook", "name": "Sales/Archive/Q1"}, {"kind": "daily", "times": ["06:00"]})["schedule"]
    out = call(svc, "notebook_folder_rename", path="Sales", new_name="Sales EMEA")
    assert out["new_path"] == "Sales EMEA" and out["schedules_moved"] == [sched["id"]]
    assert sorted(m["to"] for m in out["moved"]) == ["Sales EMEA/Archive/Q1", "Sales EMEA/Daily load"]
    assert {n["name"] for n in svc.list_notebooks()} == {"Sales EMEA/Archive/Q1", "Sales EMEA/Daily load"}
    assert (svc.notebooks.folder / "Sales EMEA" / "Daily load.resources").is_dir()
    assert call(svc, "notebook_versions", name="Sales EMEA/Archive/Q1")["versions"]
    assert svc.pc_schedules_for("notebook", "Sales EMEA/Archive/Q1")[0]["id"] == sched["id"]
    # A nested folder renames in place.
    assert call(svc, "notebook_folder_rename", path="Sales EMEA/Archive", new_name="Old")["new_path"] == "Sales EMEA/Old"


def test_rename_folder_never_overwrites(svc):
    save(svc, "Sales/A")
    call(svc, "notebook_folder_create", path="Ops")
    assert "A folder named 'Ops' is already in" in error(svc, "notebook_folder_rename", path="Sales", new_name="Ops")
    assert (svc.notebooks.folder / "Sales" / "A.ipynb").is_file()
    assert "There's no folder" in error(svc, "notebook_folder_rename", path="Nope", new_name="X")


def test_delete_folder_only_when_empty(svc):
    save(svc, "Finance/Close")
    call(svc, "notebook_folder_create", path="Scratch")
    msg = error(svc, "notebook_folder_delete", path="Finance")
    assert "Finance has 1 notebook in it, so it wasn't deleted" in msg and "only an empty folder" in msg
    assert (svc.notebooks.folder / "Finance" / "Close.ipynb").is_file()
    (svc.notebooks.folder / "Scratch" / "notes.txt").write_text("keep", encoding="utf-8")
    assert "1 other item" in error(svc, "notebook_folder_delete", path="Scratch")
    (svc.notebooks.folder / "Scratch" / "notes.txt").unlink()
    assert call(svc, "notebook_folder_delete", path="Scratch") == {"deleted": "Scratch", "place": None}
    assert not (svc.notebooks.folder / "Scratch").exists()


def test_recently_deleted_and_restore_keep_the_folder(svc):
    save(svc, "Sales/Daily load", "kept = 1")
    gone = call(svc, "notebook_delete", name="Sales/Daily load")
    assert call(svc, "notebook_deleted")[0]["name"] == "Sales/Daily load"
    call(svc, "notebook_folder_delete", path="Sales")  # empty now, so it can go
    back = call(svc, "notebook_restore", id=gone["id"])
    assert back["name"] == "Sales/Daily load"
    assert "kept" in (svc.notebooks.folder / "Sales" / "Daily load.ipynb").read_text(encoding="utf-8")


def test_a_notebook_and_a_folder_of_the_same_name_keep_their_versions_apart(svc):
    save(svc, "Sales", "top = 1")
    save(svc, "Sales/Daily load", "inner = 1")
    call(svc, "rename_notebook", name="Sales", new_name="Sales overview")
    assert call(svc, "notebook_versions", name="Sales overview")["versions"]
    assert call(svc, "notebook_versions", name="Sales/Daily load")["versions"]  # not carried off with "Sales"
    root = svc.root / "notebook-versions"
    assert notebook_versions._all(root, "Sales/Daily load")  # noqa: SLF001


def test_publish_uses_the_last_part_and_refuses_a_clash(svc):
    ws = {"workspace_id": "ws1", "workspace_name": "Sales WS"}
    cells = [{"type": "code", "source": "x = 1"}]
    out = call(svc, "publish_notebook", name="Sales/Daily load", cells=cells, workspace=ws)
    assert svc.fake.saved == [("ws1", "Daily load")] and out["name"] == "Sales/Daily load"
    assert svc.workload_store.links()["Sales/Daily load"]["item_id"] == out["id"]
    msg = error(svc, "publish_notebook", name="Ops/Daily load", cells=cells, workspace=ws)
    assert "Sales/Daily load already publishes to the Fabric notebook Daily load" in msg and "Nothing was published" in msg
    assert svc.fake.saved == [("ws1", "Daily load")]
    # Another workspace has no clash.
    call(svc, "publish_notebook", name="Ops/Daily load", cells=cells, workspace={"workspace_id": "ws2", "workspace_name": "Ops"})
    assert svc.fake.saved[-1] == ("ws2", "Daily load")


def test_folders_in_a_notebooks_repository(tmp_path):
    s = Service(root=tmp_path / "app")
    s.git_create(str(tmp_path / "repo"), "notebooks")
    root = str((tmp_path / "repo").resolve())
    call(s, "notebook_folder_create", path="Silver", place=root)
    assert (tmp_path / "repo" / "notebooks" / "Silver").is_dir()
    nb = call(s, "new_notebook", place=root, folder="Silver")
    assert nb["name"] == "Silver/Notebook 1" and nb["place"]["root"] == root
    rows = call(s, "notebook_folders")["folders"]
    assert [(f["path"], f["place"]["root"], f["notebooks"]) for f in rows] == [("Silver", root, 1)]
    # Moving to the app's own folder keeps the folders.
    s.move_notebook("Silver/Notebook 1", None)
    assert (s.notebooks.folder / "Silver" / "Notebook 1.ipynb").is_file()


def test_methods_are_registered_and_the_read_is_not_audited():
    names = {"notebook_folders", "notebook_folder_create", "notebook_folder_rename", "notebook_folder_delete",
             "notebook_move_to_folder"}
    assert names <= METHODS
    assert "notebook_folders" in audit.READ_ONLY
    for m in names - {"notebook_folders"}:
        assert audit.audited(m)
