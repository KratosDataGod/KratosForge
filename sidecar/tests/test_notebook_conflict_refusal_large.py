"""Today's answer when a large notebook was changed in KratosForge and in VS Code before a sync:
the app's save is refused, the file stays as VS Code left it, and both sides are kept under
Versions before anyone chooses, whatever the change (save_notebook, _keep_both_sides). A save or a
Versions copy cut short by closing the app leaves the old file or the new one, never half of one."""
import json

import pytest

from fabric_desktop import jsonfile, server
from fabric_desktop import notebook_versions as versions
from fabric_desktop.service import Service

CELLS = 1000


@pytest.fixture
def svc(tmp_path):
    return Service(client_id="x", root=tmp_path)


def call(svc, method, **params):
    out = json.loads(json.dumps(server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))))
    assert "error" not in out, out["error"]
    return out["result"]


def big(svc):
    cells = [{"type": "code" if i % 4 else "markdown", "source": f"# cell {i}\n" + "x = 1\n" * 80} for i in range(CELLS)]
    call(svc, "save_notebook", name="Big", cells=cells)
    return call(svc, "open_notebook", name="Big")


def vs_code_saves(svc, change):
    path = svc.notebooks.path("Big")
    nb = json.loads(path.read_text(encoding="utf-8"))
    change(nb)
    path.write_text(json.dumps(nb, indent=1), encoding="utf-8")


def kept(svc):
    """The two sides kept at the refusal, by kind: the cells of each as the Versions screen shows them."""
    out = {}
    for v in call(svc, "notebook_versions", name="Big")["versions"]:
        if v["kind"] in ("changed outside", "not saved") and v["kind"] not in out:
            out[v["kind"]] = [c["source"] for c in call(svc, "notebook_version", name="Big", version_id=v["id"])["cells"]]
    return out


def refused(svc, opened, mine):
    path = svc.notebooks.path("Big")
    before = path.read_bytes()
    out = call(svc, "save_notebook", name="Big", cells=mine, disk_stamp=opened["disk_stamp"])
    assert out["changed_on_disk"] and not out["unreadable"]
    assert path.read_bytes() == before  # nothing written over VS Code's save
    assert out["disk_stamp"] == call(svc, "notebook_disk_stamp", name="Big")["disk_stamp"]
    return kept(svc)


def test_the_same_cell_changed_in_both_keeps_both_versions(svc):
    opened = big(svc)
    vs_code_saves(svc, lambda nb: nb["cells"][500].__setitem__("source", "theirs = 1"))
    mine = [dict(c) for c in opened["cells"]]
    mine[500]["source"] = "mine = 1"
    sides = refused(svc, opened, mine)
    assert sides["changed outside"][500] == "theirs = 1" and sides["not saved"][500] == "mine = 1"
    assert len(sides["changed outside"]) == len(sides["not saved"]) == CELLS


def test_a_cell_deleted_in_the_app_and_changed_in_vs_code_keeps_both(svc):
    opened = big(svc)
    vs_code_saves(svc, lambda nb: nb["cells"][700].__setitem__("source", "changed in VS Code"))
    mine = opened["cells"][:700] + opened["cells"][701:]
    sides = refused(svc, opened, mine)
    assert sides["changed outside"][700] == "changed in VS Code" and len(sides["changed outside"]) == CELLS
    assert len(sides["not saved"]) == CELLS - 1 and "changed in VS Code" not in sides["not saved"]


def test_a_cell_deleted_in_vs_code_and_changed_in_the_app_keeps_both(svc):
    opened = big(svc)
    vs_code_saves(svc, lambda nb: nb["cells"].pop(700))
    mine = [dict(c) for c in opened["cells"]]
    mine[700]["source"] = "changed in the app"
    sides = refused(svc, opened, mine)
    assert len(sides["changed outside"]) == CELLS - 1
    assert sides["not saved"][700] == "changed in the app" and len(sides["not saved"]) == CELLS


def test_cells_moved_in_both_keep_both_orders(svc):
    opened = big(svc)
    vs_code_saves(svc, lambda nb: nb["cells"].insert(0, nb["cells"].pop()))
    mine = opened["cells"][1:] + opened["cells"][:1]
    sides = refused(svc, opened, mine)
    first, last = opened["cells"][0]["source"], opened["cells"][-1]["source"]
    assert sides["changed outside"][0] == last and sides["not saved"][-1] == first
    assert sorted(sides["changed outside"]) == sorted(sides["not saved"])  # the same cells, two orders


def test_keep_mine_after_a_refusal_saves_the_apps_cells_and_vs_codes_stay_in_versions(svc):
    opened = big(svc)
    vs_code_saves(svc, lambda nb: nb["cells"][3].__setitem__("source", "theirs = 1"))
    mine = [dict(c) for c in opened["cells"]]
    mine[3]["source"] = "mine = 1"
    out = call(svc, "save_notebook", name="Big", cells=mine, disk_stamp=opened["disk_stamp"])
    saved = call(svc, "save_notebook", name="Big", cells=mine, disk_stamp=out["disk_stamp"])  # Keep mine
    assert "changed_on_disk" not in saved
    assert svc.notebooks.load("Big")["cells"][3]["source"] == "mine = 1"
    assert kept(svc)["changed outside"][3] == "theirs = 1"


def test_load_its_version_after_a_refusal_shows_vs_codes_and_the_apps_stays_in_versions(svc):
    opened = big(svc)
    vs_code_saves(svc, lambda nb: nb["cells"][3].__setitem__("source", "theirs = 1"))
    mine = [dict(c) for c in opened["cells"]]
    mine[3]["source"] = "mine = 1"
    call(svc, "save_notebook", name="Big", cells=mine, disk_stamp=opened["disk_stamp"])
    assert call(svc, "open_notebook", name="Big")["cells"][3]["source"] == "theirs = 1"  # Load its version
    assert kept(svc)["not saved"][3] == "mine = 1"


def test_outputs_vs_code_saved_for_a_cell_the_app_is_editing_go_with_the_old_code(svc):
    opened = big(svc)
    output = {"output_type": "stream", "name": "stdout", "text": ["ran in VS Code\n"]}

    def ran(nb):
        nb["cells"][1]["outputs"] = [output]
        nb["cells"][2]["outputs"] = [output]
    vs_code_saves(svc, ran)
    mine = [dict(c) for c in opened["cells"]]
    mine[2]["source"] = "edited in the app while VS Code ran it"
    out = call(svc, "save_notebook", name="Big", cells=mine, disk_stamp=opened["disk_stamp"])
    assert "changed_on_disk" not in out  # outputs aren't an edit
    cells = json.loads(svc.notebooks.path("Big").read_text(encoding="utf-8"))["cells"]
    assert cells[1]["outputs"] == [output]  # its code is what ran
    assert cells[2]["outputs"] == []  # from code that's gone


def test_a_save_cut_short_by_closing_the_app_leaves_the_file_as_it_was(svc, monkeypatch):
    opened = big(svc)
    path = svc.notebooks.path("Big")
    before = path.read_bytes()
    count = len(call(svc, "notebook_versions", name="Big")["versions"])

    def closed(src, dst):
        raise KeyboardInterrupt("the app closed")
    monkeypatch.setattr(jsonfile, "replace", closed)
    mine = [dict(c) for c in opened["cells"]]
    mine[0]["source"] = "never saved"
    with pytest.raises(KeyboardInterrupt):
        svc.save_notebook("Big", mine, disk_stamp=opened["disk_stamp"])
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == []
    monkeypatch.undo()
    assert len(call(svc, "notebook_versions", name="Big")["versions"]) == count  # no version of a save that didn't happen
    again = Service(client_id="x", root=svc.root)  # the app starts again
    assert again.notebooks.load("Big")["disk_stamp"] == opened["disk_stamp"]
    assert "changed_on_disk" not in again.save_notebook("Big", mine, disk_stamp=opened["disk_stamp"])


def test_a_versions_copy_cut_short_at_a_refusal_still_refuses_and_keeps_the_file(svc, monkeypatch):
    opened = big(svc)
    vs_code_saves(svc, lambda nb: nb["cells"][3].__setitem__("source", "theirs = 1"))
    before = svc.notebooks.path("Big").read_bytes()

    def cut(path, record):
        path.write_text(json.dumps(record)[:500], encoding="utf-8")  # half a version, as a crash would leave it
        raise OSError("the app closed")
    monkeypatch.setattr(versions, "_write", cut)
    mine = [dict(c) for c in opened["cells"]]
    mine[3]["source"] = "mine = 1"
    out = svc.save_notebook("Big", mine, disk_stamp=opened["disk_stamp"])
    assert out["changed_on_disk"] and svc.notebooks.path("Big").read_bytes() == before
    monkeypatch.undo()
    listed = call(svc, "notebook_versions", name="Big")["versions"]  # the half-written copies don't break the list
    assert all(v["kind"] not in ("changed outside", "not saved") for v in listed)


def test_conflict_markers_left_in_a_large_notebook_are_kept_as_a_byte_copy(svc):
    opened = big(svc)
    path = svc.notebooks.path("Big")
    text = path.read_text(encoding="utf-8")
    broken = "<<<<<<< HEAD\n" + text + "=======\n" + text + ">>>>>>> theirs\n"
    path.write_text(broken, encoding="utf-8")
    out = call(svc, "save_notebook", name="Big", cells=opened["cells"], disk_stamp=opened["disk_stamp"])
    assert out["changed_on_disk"] and out["unreadable"]
    assert path.read_text(encoding="utf-8") == broken
    with open(out["outside_copy"], encoding="utf-8") as f:
        assert f.read() == broken
    entries = versions._entries(svc._versions_root, "Big")
    assert entries[0]["kind"] == "not saved" and entries[0]["cells"] == CELLS
