"""A large notebook changed in KratosForge and in VS Code before a sync: neither edit is lost
silently. Changes on one side merge by cell; a cell changed on both, deleted on one side and changed
on the other, or cells moved on both, come back as named conflicts with both versions, and the
person's choice is what gets saved (notebook_sync, save_notebook, notebook_sync_merge)."""
import json
import shutil

import pytest

from fabric_desktop import jsonfile, server
from fabric_desktop import notebook as notebook_mod
from fabric_desktop.notebook_sync import (BOTH_EDITED, DELETED_HERE, DELETED_THERE, ORDER_CONFLICT_ID, REORDERED,
                                          SyncBases, merge_cells)
from fabric_desktop.service import Service

CELLS = 1000


@pytest.fixture
def svc(tmp_path):
    return Service(client_id="x", root=tmp_path)


def call(svc, method, **params):
    out = json.loads(json.dumps(server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))))
    assert "error" not in out, out["error"]
    return out["result"]


def refused(svc, method, **params):
    out = json.loads(json.dumps(server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))))
    assert "error" in out
    return out["error"]


def big(svc):
    cells = [{"type": "code" if i % 4 else "markdown", "source": f"# cell {i}\n" + "x = 1\n" * 80} for i in range(CELLS)]
    call(svc, "save_notebook", name="Big", cells=cells)
    return call(svc, "open_notebook", name="Big")


def vs_code_saves(svc, change):
    path = svc.notebooks.path("Big")
    nb = json.loads(path.read_text(encoding="utf-8"))
    change(nb)
    path.write_text(json.dumps(nb, indent=1), encoding="utf-8")


def on_disk(svc):
    return json.loads(svc.notebooks.path("Big").read_text(encoding="utf-8"))


def sources(svc):
    return ["".join(c["source"]) for c in on_disk(svc)["cells"]]


def save(svc, nb, cells):
    return call(svc, "save_notebook", name="Big", cells=cells, disk_stamp=nb["disk_stamp"])


def test_the_notebook_is_large(svc):
    big(svc)
    assert svc.notebooks.path("Big").stat().st_size > 1_000_000


def test_the_same_cell_changed_in_both_is_a_named_conflict_with_both_versions(svc):
    nb = big(svc)
    cid = nb["cells"][500]["cell_id"]

    def there(f):
        f["cells"][500]["source"] = "theirs = 1"
        f["cells"][10]["source"] = "only VS Code changed this"
    vs_code_saves(svc, there)
    nb["cells"][500]["source"] = "mine = 1"
    nb["cells"][900]["source"] = "only KratosForge changed this"
    out = save(svc, nb, nb["cells"])

    assert out["changed_on_disk"] and "theirs = 1" in sources(svc)[500]  # nothing written over VS Code's save
    merge = out["merge"]
    assert not merge["resolved"]
    [c] = merge["conflicts"]
    assert c["kind"] == BOTH_EDITED and c["cell_id"] == cid and c["name"] == "Changed in both places"
    assert c["mine"]["source"] == "mine = 1" and c["theirs"]["source"] == "theirs = 1"
    assert c["base"]["source"].startswith("# cell 500")
    shown = [x["source"] for x in merge["cells"]]
    assert shown[10] == "only VS Code changed this" and shown[901] == "only KratosForge changed this"
    assert shown[500:502] == ["theirs = 1", "mine = 1"]  # both shown until the person chooses

    # Nothing is saved until every conflict is chosen.
    left = call(svc, "notebook_sync_merge", name="Big", cells=nb["cells"], disk_stamp=nb["disk_stamp"])
    assert not left["resolved"] and len(left["conflicts"]) == 1 and "theirs = 1" in sources(svc)[500]

    done = call(svc, "notebook_sync_merge", name="Big", cells=nb["cells"], disk_stamp=nb["disk_stamp"],
                choices={cid: "mine"})
    assert done["resolved"] and len(sources(svc)) == CELLS
    now = sources(svc)
    assert now[500] == "mine = 1" and now[10] == "only VS Code changed this" and now[900] == "only KratosForge changed this"
    assert done["disk_stamp"] == call(svc, "open_notebook", name="Big")["disk_stamp"]  # the screen can keep saving


@pytest.mark.parametrize("pick, expect", [("theirs", ["theirs = 1"]), ("both", ["theirs = 1", "mine = 1"])])
def test_choosing_theirs_or_both_for_a_cell_changed_in_both(svc, pick, expect):
    nb = big(svc)
    cid = nb["cells"][3]["cell_id"]
    vs_code_saves(svc, lambda f: f["cells"][3].__setitem__("source", "theirs = 1"))
    nb["cells"][3]["source"] = "mine = 1"
    call(svc, "notebook_sync_merge", name="Big", cells=nb["cells"], disk_stamp=nb["disk_stamp"], choices={cid: pick})
    assert sources(svc)[3:3 + len(expect)] == expect
    assert len(sources(svc)) == CELLS + len(expect) - 1
    ids = [c["id"] for c in on_disk(svc)["cells"]]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("where", ["here", "there"])
def test_a_cell_deleted_on_one_side_and_changed_on_the_other_is_a_conflict(svc, where):
    nb = big(svc)
    cid = nb["cells"][700]["cell_id"]
    if where == "here":  # deleted in KratosForge, changed in VS Code
        vs_code_saves(svc, lambda f: f["cells"][700].__setitem__("source", "changed in VS Code"))
        mine = nb["cells"][:700] + nb["cells"][701:]
        kind, kept = DELETED_HERE, "changed in VS Code"
    else:
        vs_code_saves(svc, lambda f: f["cells"].pop(700))
        nb["cells"][700]["source"] = "changed in KratosForge"
        mine = nb["cells"]
        kind, kept = DELETED_THERE, "changed in KratosForge"
    out = save(svc, nb, mine)
    [c] = out["merge"]["conflicts"]
    assert c["kind"] == kind and c["cell_id"] == cid
    assert {"mine", "theirs"} <= set(c)
    assert kept in [x["source"] for x in out["merge"]["cells"]]  # the changed version is shown, not dropped

    deleter = "mine" if where == "here" else "theirs"
    call(svc, "notebook_sync_merge", name="Big", cells=mine, disk_stamp=nb["disk_stamp"], choices={cid: deleter})
    assert len(sources(svc)) == CELLS - 1 and kept not in sources(svc)


def test_keeping_the_changed_version_of_a_cell_the_other_side_deleted(svc):
    nb = big(svc)
    cid = nb["cells"][700]["cell_id"]
    vs_code_saves(svc, lambda f: f["cells"].pop(700))
    nb["cells"][700]["source"] = "changed in KratosForge"
    call(svc, "notebook_sync_merge", name="Big", cells=nb["cells"], disk_stamp=nb["disk_stamp"], choices={cid: "mine"})
    assert sources(svc)[700] == "changed in KratosForge" and len(sources(svc)) == CELLS


def test_cells_moved_in_both_places_are_a_conflict_and_the_choice_sets_the_order(svc):
    nb = big(svc)
    first, last = nb["cells"][0]["source"], nb["cells"][-1]["source"]
    vs_code_saves(svc, lambda f: f["cells"].insert(0, f["cells"].pop()))  # VS Code moved the last cell to the top
    mine = nb["cells"][1:] + nb["cells"][:1]  # KratosForge moved the first cell to the bottom
    out = save(svc, nb, mine)
    [c] = out["merge"]["conflicts"]
    assert c["kind"] == REORDERED and c["id"] == ORDER_CONFLICT_ID and c["mine"] != c["theirs"]
    assert len(c["mine"]) == len(c["theirs"]) == CELLS

    call(svc, "notebook_sync_merge", name="Big", cells=mine, disk_stamp=nb["disk_stamp"], choices={"order": "mine"})
    assert sources(svc)[-1] == first and sources(svc)[0] != last
    nb = call(svc, "open_notebook", name="Big")
    vs_code_saves(svc, lambda f: f["cells"].insert(0, f["cells"].pop()))
    mine = nb["cells"][1:] + nb["cells"][:1]
    call(svc, "notebook_sync_merge", name="Big", cells=mine, disk_stamp=nb["disk_stamp"], choices={"order": "theirs"})
    assert sources(svc)[0] == first  # VS Code moved the cell KratosForge had put last back to the top


def test_a_move_on_one_side_and_edits_on_the_other_merge_without_asking(svc):
    nb = big(svc)
    moved = nb["cells"][999]["source"]
    vs_code_saves(svc, lambda f: f["cells"].insert(0, f["cells"].pop()))
    nb["cells"][400]["source"] = "edited in KratosForge"
    new = {"type": "code", "source": "added in KratosForge"}
    mine = nb["cells"][:401] + [new] + nb["cells"][401:]
    out = save(svc, nb, mine)
    assert out["merge"]["resolved"] and out["merge"]["conflicts"] == []
    done = call(svc, "notebook_sync_merge", name="Big", cells=mine, disk_stamp=nb["disk_stamp"])
    assert done["resolved"]
    now = sources(svc)
    assert now[0] == moved and now[401] == "edited in KratosForge" and now[402] == "added in KratosForge"
    assert len(now) == CELLS + 1


def test_outputs_that_arrive_while_an_edit_is_in_flight_are_kept(svc):
    nb = big(svc)
    out_cell = {"output_type": "stream", "name": "stdout", "text": ["ran in VS Code\n"]}

    def ran(f):  # VS Code ran two cells and saved their outputs; no source changed
        f["cells"][1]["outputs"] = [out_cell]
        f["cells"][2]["outputs"] = [out_cell]
    vs_code_saves(svc, ran)
    nb["cells"][2]["source"] = "edited in KratosForge while it ran"
    nb["cells"][3]["source"] = "edited too"
    out = save(svc, nb, nb["cells"])
    assert not out.get("changed_on_disk")  # outputs aren't an edit, so the save isn't refused
    after = on_disk(svc)["cells"]
    assert after[1]["outputs"] == [out_cell]  # kept: its code is what ran
    assert after[2]["outputs"] == []  # its code changed since it ran, so its output went with it
    assert "".join(after[3]["source"]) == "edited too"


def test_outputs_saved_with_an_edit_in_vs_code_are_kept_by_the_merge(svc):
    nb = big(svc)
    out_cell = {"output_type": "stream", "name": "stdout", "text": ["ran in VS Code\n"]}

    def there(f):
        f["cells"][5]["outputs"] = [out_cell]
        f["cells"][6]["source"] = "changed in VS Code"
    vs_code_saves(svc, there)
    nb["cells"][7]["source"] = "changed in KratosForge"
    call(svc, "notebook_sync_merge", name="Big", cells=nb["cells"], disk_stamp=nb["disk_stamp"])
    after = on_disk(svc)["cells"]
    assert after[5]["outputs"] == [out_cell]
    assert "".join(after[6]["source"]) == "changed in VS Code" and "".join(after[7]["source"]) == "changed in KratosForge"


def test_a_save_cut_short_by_closing_the_app_leaves_the_file_as_it_was(svc, monkeypatch):
    nb = big(svc)
    before = svc.notebooks.path("Big").read_bytes()

    def closed(src, dst):
        raise KeyboardInterrupt("the app closed")
    monkeypatch.setattr(jsonfile, "replace", closed)
    nb["cells"][0]["source"] = "never saved"
    with pytest.raises(KeyboardInterrupt):
        svc.save_notebook("Big", nb["cells"], disk_stamp=nb["disk_stamp"])
    assert svc.notebooks.path("Big").read_bytes() == before
    assert [p.name for p in svc.notebooks.folder.iterdir() if p.suffix == ".tmp"] == []


def test_a_sync_cut_short_by_closing_the_app_still_merges_after_it_starts_again(svc, tmp_path):
    nb = big(svc)
    vs_code_saves(svc, lambda f: f["cells"][20].__setitem__("source", "changed in VS Code"))
    nb["cells"][30]["source"] = "changed in KratosForge"
    del svc  # the app closed before the save; it starts again with the screen's unsaved cells
    again = Service(client_id="x", root=tmp_path)
    out = again.save_notebook("Big", nb["cells"], disk_stamp=nb["disk_stamp"])
    assert out["changed_on_disk"] and out["merge"]["resolved"]
    again.notebook_sync_merge("Big", nb["cells"], nb["disk_stamp"])
    now = ["".join(c["source"]) for c in json.loads(again.notebooks.path("Big").read_text(encoding="utf-8"))["cells"]]
    assert now[20] == "changed in VS Code" and now[30] == "changed in KratosForge"


def test_a_base_cut_short_mid_write_falls_back_to_choosing_and_loses_nothing(svc):
    nb = big(svc)
    folder = svc._sync_bases._folder("Big")
    for p in folder.glob("*.json"):
        p.write_text(p.read_text(encoding="utf-8")[:1000], encoding="utf-8")  # cut short
    vs_code_saves(svc, lambda f: f["cells"][20].__setitem__("source", "changed in VS Code"))
    nb["cells"][30]["source"] = "changed in KratosForge"
    out = save(svc, nb, nb["cells"])
    assert out["changed_on_disk"] and "merge" not in out
    kinds = [v["kind"] for v in call(svc, "notebook_versions", name="Big")["versions"]]
    assert "changed outside" in kinds and "not saved" in kinds  # both sides kept, as before
    err = refused(svc, "notebook_sync_merge", name="Big", cells=nb["cells"], disk_stamp=nb["disk_stamp"])
    assert "Keep mine or Load its version" in err


def test_a_merge_refuses_when_the_file_changes_again_before_it_saves(svc, monkeypatch):
    nb = big(svc)
    vs_code_saves(svc, lambda f: f["cells"][20].__setitem__("source", "changed in VS Code"))
    nb["cells"][30]["source"] = "changed in KratosForge"
    real_load = svc.notebooks.load

    def load_then_vs_code_saves_again(name):
        out = real_load(name)
        vs_code_saves(svc, lambda f: f["cells"][40].__setitem__("source", "changed again"))
        return out
    monkeypatch.setattr(svc.notebooks, "load", load_then_vs_code_saves_again)
    out = svc.notebook_sync_merge("Big", nb["cells"], nb["disk_stamp"])
    assert out["changed_on_disk"]
    now = sources(svc)
    assert now[40] == "changed again" and now[30] != "changed in KratosForge"


# The merge itself, small.

def cell(cid, source, kind="code"):
    return {"cell_id": cid, "type": kind, "source": source}


def test_merge_takes_one_sided_changes_and_places_added_cells_after_their_neighbour():
    base = [cell("a", "1"), cell("b", "2"), cell("c", "3")]
    mine = [cell("a", "1"), cell("n1", "new here"), cell("b", "2 mine"), cell("c", "3")]
    theirs = [cell("a", "1 theirs"), cell("b", "2"), cell("c", "3"), cell("n2", "new there")]
    out = merge_cells(base, mine, theirs)
    assert out["resolved"]
    assert [(c["cell_id"], c["source"]) for c in out["cells"]] == [
        ("a", "1 theirs"), ("n1", "new here"), ("b", "2 mine"), ("c", "3"), ("n2", "new there")]


def test_merge_same_change_both_sides_and_deleted_both_sides_are_not_conflicts():
    base = [cell("a", "1"), cell("b", "2")]
    out = merge_cells(base, [cell("a", "same")], [cell("a", "same")])
    assert out["resolved"] and out["cells"] == [cell("a", "same")]


def test_merge_a_cell_deleted_on_one_side_and_untouched_on_the_other_goes():
    base = [cell("a", "1"), cell("b", "2")]
    assert merge_cells(base, [cell("a", "1")], base)["cells"] == [cell("a", "1")]
    assert merge_cells(base, base, [cell("b", "2")])["cells"] == [cell("b", "2")]


def test_merge_a_cell_added_on_both_sides_with_one_id_differently_is_a_conflict():
    out = merge_cells([], [cell("x", "mine")], [cell("x", "theirs")])
    assert out["conflicts"][0]["kind"] == BOTH_EDITED


def test_merge_settings_and_type_count_as_a_change_outputs_do_not():
    base = [cell("a", "1")]
    tagged = dict(cell("a", "1"), meta={"tags": ["parameters"]})
    out = merge_cells(base, [cell("a", "1", "markdown")], [tagged])
    assert out["conflicts"][0]["kind"] == BOTH_EDITED
    assert merge_cells(base, [dict(cell("a", "1"), outputs=[1])], base)["resolved"]


def test_merge_refuses_a_choice_it_cant_apply():
    base = [cell("a", "1"), cell("b", "2")]
    with pytest.raises(ValueError, match="choose one of"):
        merge_cells(base, base, base, {"a": "neither"})
    with pytest.raises(ValueError, match="mine or theirs"):
        merge_cells(base, [cell("b", "2")], [cell("a", "x"), cell("b", "2")], {"a": "both"})
    with pytest.raises(ValueError, match="order: choose mine or theirs"):
        merge_cells([cell("a", "1"), cell("b", "2"), cell("c", "3")], [cell("b", "2"), cell("a", "1"), cell("c", "3")],
                    [cell("a", "1"), cell("c", "3"), cell("b", "2")], {"order": "both"})
    with pytest.raises(ValueError, match="cell id"):
        merge_cells([{"type": "code", "source": ""}], [], [])
    with pytest.raises(ValueError, match="share the id"):
        merge_cells([cell("a", "1"), cell("a", "2")], [], [])


def test_sync_bases_keep_a_few_per_notebook_and_skip_what_isnt_a_base(tmp_path):
    bases = SyncBases(tmp_path)
    for i in range(6):
        bases.remember("nb", f"stamp{i:04d}aaaa", [cell("a", str(i))])
    assert len(list(bases._folder("nb").glob("*.json"))) == 4
    assert bases.find("nb", "stamp0005aaaa") == [cell("a", "5")]
    assert bases.find("nb", "stamp0000aaaa") is None
    bases.remember("nb", "raw-not/a stamp", [])  # an unreadable file's stamp
    assert bases.find("nb", "raw-not/a stamp") is None and bases.find("nb", None) is None
    assert bases.find("other", "stamp0005aaaa") is None
    bases.forget("nb")
    assert bases.find("nb", "stamp0005aaaa") is None
    shutil.rmtree(tmp_path / "missing", ignore_errors=True)
    assert SyncBases(tmp_path / "missing").find("nb", "stamp0005aaaa") is None


def test_the_atomic_write_used_by_saves_is_the_shared_one():
    assert notebook_mod.jsonfile is jsonfile  # the interruption test above patches the one saves use
