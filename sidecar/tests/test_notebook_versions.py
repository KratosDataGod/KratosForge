"""Version history: a version per save (coalesced), checkpoints, compare, restore, rename."""
import json

import pytest

from fabric_desktop import notebook_versions as versions
from fabric_desktop import server
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    return Service(client_id="x", root=tmp_path)


def call(svc, method, **params):
    out = json.loads(json.dumps(server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))))
    assert "error" not in out, out["error"]
    return out["result"]


def error(svc, method, **params):
    out = json.loads(json.dumps(server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))))
    assert "error" in out, out
    return out["error"]


def save(svc, name, *sources):
    return call(svc, "save_notebook", name=name, cells=[{"type": "code", "source": s} for s in sources])


@pytest.fixture
def clock(monkeypatch):
    t = {"now": 1_800_000_000.0}
    monkeypatch.setattr(versions, "_now", lambda: t["now"])
    return t


def test_saves_make_versions_coalesced_to_one_per_five_minutes(svc, clock):
    save(svc, "NB", "a = 1")
    clock["now"] += 60
    save(svc, "NB", "a = 2")  # within 5 minutes: replaces the last automatic version
    clock["now"] += 60
    save(svc, "NB", "a = 2")  # unchanged: nothing kept
    listed = call(svc, "notebook_versions", name="NB")["versions"]
    assert [v["kind"] for v in listed] == ["auto"] and listed[0]["current"] is True
    clock["now"] += 6 * 60
    save(svc, "NB", "a = 3")
    listed = call(svc, "notebook_versions", name="NB")["versions"]
    assert len(listed) == 2 and [v["current"] for v in listed] == [True, False]
    assert call(svc, "notebook_version", name="NB", version_id=listed[1]["id"])["cells"][0]["source"] == "a = 2"


def test_a_checkpoint_is_kept_and_the_next_save_starts_a_new_version(svc, clock):
    save(svc, "NB", "a = 1")
    cp = call(svc, "notebook_checkpoint", name="NB", label="  Before   the refactor ")
    assert cp["kind"] == "checkpoint" and cp["label"] == "Before the refactor"
    clock["now"] += 30
    save(svc, "NB", "a = 2")  # the newest is a checkpoint, so this isn't coalesced into anything
    kinds = [v["kind"] for v in call(svc, "notebook_versions", name="NB")["versions"]]
    assert kinds == ["auto", "checkpoint", "auto"]
    assert "Name the checkpoint" in error(svc, "notebook_checkpoint", name="NB", label="  ")


def test_restore_keeps_the_current_notebook_first_and_keeps_its_lakehouse(svc, clock):
    lh = {"workspace_id": "ws", "lakehouse_id": "lh", "name": "Sales"}
    call(svc, "save_notebook", name="NB", cells=[{"type": "code", "source": "old = 1"}], lakehouse_ref=lh)
    first = call(svc, "notebook_versions", name="NB")["versions"][0]["id"]
    clock["now"] += 10 * 60
    call(svc, "save_notebook", name="NB", cells=[{"type": "code", "source": "new = 2"}, {"type": "markdown", "source": "# x"}],
         lakehouse_ref=lh)
    out = call(svc, "notebook_version_restore", name="NB", version_id=first)
    assert [c["source"] for c in out["cells"]] == ["old = 1"] and out["lakehouse"] == lh
    listed = call(svc, "notebook_versions", name="NB")["versions"]
    before = next(v for v in listed if v["kind"] == "before restore")
    assert call(svc, "notebook_version", name="NB", version_id=before["id"])["cells"][0]["source"] == "new = 2"
    # ...so the restore can be undone.
    call(svc, "notebook_version_restore", name="NB", version_id=before["id"])
    assert [c["source"] for c in svc.notebooks.load("NB")["cells"]] == ["new = 2", "# x"]


def test_compare_with_a_version_and_with_unsaved_cells(svc, clock):
    save(svc, "NB", "a = 1", "b = 2")
    vid = call(svc, "notebook_versions", name="NB")["versions"][0]["id"]
    cells = svc.notebooks.load("NB")["cells"]
    on_screen = [dict(cells[0], source="a = 10"), cells[1], {"type": "code", "source": "c = 3"}]
    out = call(svc, "notebook_version_compare", name="NB", version_id=vid, cells=on_screen)
    assert sorted(c["kind"] for c in out["changes"]) == ["added", "changed"]
    assert call(svc, "notebook_version_compare", name="NB", version_id=vid)["changes"] == []
    head = call(svc, "notebook_version_compare", name="NB", against="head")
    assert head["problem"].startswith("This notebook isn't in a Git commit")


def test_versions_follow_a_rename_and_bad_ids_are_refused(svc, clock):
    save(svc, "NB", "a = 1")
    call(svc, "rename_notebook", name="NB", new_name="Sales")
    assert len(call(svc, "notebook_versions", name="Sales")["versions"]) == 1
    for bad in ("../../x", "nope", ""):
        assert "isn't a version" in error(svc, "notebook_version", name="Sales", version_id=bad)


def test_versions_live_outside_the_notebooks_folder_and_old_ones_are_pruned(svc, clock):
    save(svc, "NB", "a = 0")
    for i in range(1, versions.MAX_AUTO + 5):
        clock["now"] += 6 * 60
        save(svc, "NB", f"a = {i}")
    call(svc, "notebook_checkpoint", name="NB", label="keep")
    folder = svc.root / "notebook-versions" / "NB"
    assert not list((svc.root / "notebooks").glob("**/*.json"))
    assert len([p for p in folder.glob("*.json") if p.name != versions.INDEX and json.loads(p.read_text())["kind"] == "auto"]) == versions.MAX_AUTO
    clock["now"] += (versions.KEEP_DAYS + 1) * 86400
    save(svc, "NB", "fresh = 1")
    kinds = sorted(v["kind"] for v in call(svc, "notebook_versions", name="NB")["versions"])
    assert kinds == ["auto", "checkpoint"]  # the checkpoint is never pruned


def test_delete_a_version_and_a_torn_file_is_skipped(svc, clock):
    save(svc, "NB", "a = 1")
    call(svc, "notebook_checkpoint", name="NB", label="one")
    folder = svc.root / "notebook-versions" / "NB"
    (folder / "20260101000000-deadbeef.json").write_text("{not json", encoding="utf-8")
    listed = call(svc, "notebook_versions", name="NB")["versions"]
    assert len(listed) == 2
    call(svc, "notebook_version_delete", name="NB", version_id=listed[0]["id"])
    assert len(call(svc, "notebook_versions", name="NB")["versions"]) == 1


def test_a_failed_version_never_fails_the_save(svc, clock, monkeypatch):
    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(versions, "record", broken)
    assert save(svc, "NB", "a = 1")["name"] == "NB"


def test_reads_are_read_only_in_the_audit_trail():
    from fabric_desktop import audit
    for m in ("notebook_versions", "notebook_version", "notebook_version_compare"):
        assert m in audit.READ_ONLY
    for m in ("notebook_checkpoint", "notebook_version_restore", "notebook_version_delete"):
        assert m not in audit.READ_ONLY


def test_a_record_missing_fields_is_skipped_and_never_breaks_a_save(svc, clock):
    save(svc, "NB", "a = 1")
    folder = svc.root / "notebook-versions" / "NB"
    (folder / "20000101000000-deadbeef.json").write_text(json.dumps({"id": "20000101000000-deadbeef", "nb": {"cells": []}}))
    clock["now"] += 10_000
    save(svc, "NB", "a = 2")
    assert len(call(svc, "notebook_versions", name="NB")["versions"]) == 2
    assert "isn't a version" in error(svc, "notebook_version", name="NB", version_id="20000101000000-deadbeef\n")


def test_a_save_reads_the_index_not_every_version(svc, clock, monkeypatch):
    big = "x = '" + "y" * 200_000 + "'"  # about 200 KB a version, so 60 of them make a 12 MB history
    for i in range(60):
        clock["now"] += 6 * 60
        save(svc, "NB", f"a = {i}", big)
    reads = []
    real = versions._read
    monkeypatch.setattr(versions, "_read", lambda p: reads.append(p) or real(p))
    clock["now"] += 6 * 60
    save(svc, "NB", "a = new", big)
    listed = call(svc, "notebook_versions", name="NB")["versions"]
    assert reads == []  # neither the save nor the list opened a version file
    assert len(listed) == 61 and listed[0]["current"] and listed[0]["cells"] == 2
    assert call(svc, "notebook_version", name="NB", version_id=listed[1]["id"])["cells"][0]["source"] == "a = 59"


def test_the_index_is_rebuilt_when_it_is_torn_or_out_of_step(svc, clock):
    save(svc, "NB", "a = 1")
    clock["now"] += 6 * 60
    save(svc, "NB", "a = 2")
    folder = svc.root / "notebook-versions" / "NB"
    (folder / versions.INDEX).write_text('{"versions": [', encoding="utf-8")
    assert len(call(svc, "notebook_versions", name="NB")["versions"]) == 2
    oldest = sorted(p for p in folder.glob("*.json") if p.name != versions.INDEX)[0]
    oldest.unlink()  # removed by hand: the index names a file that isn't there
    assert len(call(svc, "notebook_versions", name="NB")["versions"]) == 1
    clock["now"] += 6 * 60
    save(svc, "NB", "a = 3")
    assert [v["current"] for v in call(svc, "notebook_versions", name="NB")["versions"]] == [True, False]


def test_a_save_never_writes_over_a_notebook_changed_on_disk(svc):
    first = save(svc, "NB", "a = 1")
    opened = call(svc, "open_notebook", name="NB")
    assert opened["disk_stamp"] == first["disk_stamp"]
    path = svc.notebooks.path("NB")
    nb = json.loads(path.read_text())
    nb["cells"][0]["source"] = ["a = 2  # from VS Code"]
    path.write_text(json.dumps(nb))  # another program saves the file
    mine = [{"type": "code", "source": "a = 3"}]
    out = call(svc, "save_notebook", name="NB", cells=mine, disk_stamp=opened["disk_stamp"])
    assert out["changed_on_disk"] and out["disk_stamp"] != opened["disk_stamp"]
    assert svc.notebooks.load("NB")["cells"][0]["source"] == "a = 2  # from VS Code"  # nothing written
    kept = call(svc, "notebook_versions", name="NB")["versions"]
    assert kept[0]["kind"] == "changed outside"
    # Keep mine: saved with the stamp it was told; the outside version stays under Versions
    again = call(svc, "save_notebook", name="NB", cells=mine, disk_stamp=out["disk_stamp"])
    assert "changed_on_disk" not in again
    assert svc.notebooks.load("NB")["cells"][0]["source"] == "a = 3"
    kinds = [v["kind"] for v in call(svc, "notebook_versions", name="NB")["versions"]]
    assert "changed outside" in kinds and kinds[0] == "auto"


def test_metadata_changes_and_unchecked_saves_are_not_outside_changes(svc):
    first = save(svc, "NB", "a = 1")
    svc.notebooks.set_extra("NB", "replay", {"x": 1})  # the app's own metadata write
    out = call(svc, "save_notebook", name="NB", cells=[{"type": "code", "source": "a = 2"}], disk_stamp=first["disk_stamp"])
    assert "changed_on_disk" not in out
    path = svc.notebooks.path("NB")
    nb = json.loads(path.read_text())
    nb["cells"][0]["source"] = "b = 1"
    path.write_text(json.dumps(nb))
    assert "changed_on_disk" not in save(svc, "NB", "c = 1")  # callers that don't send a stamp save as before
