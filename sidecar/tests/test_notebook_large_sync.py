"""A large notebook open in KratosForge and in VS Code at once: an edit saved in either is never
written over by the other (notebook.disk_stamp, NotebookStore.save, merge_saved)."""
import json

import pytest

from fabric_desktop import server
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


def test_the_large_notebook_is_large(svc):
    big(svc)
    assert svc.notebooks.path("Big").stat().st_size > 1_000_000


def test_an_edit_saved_in_vs_code_is_not_written_over_by_the_app(svc):
    opened = big(svc)
    vs_code_saves(svc, lambda nb: nb["cells"][834].__setitem__("source", ["y = 2  # from VS Code"]))
    mine = [dict(c) for c in opened["cells"]]
    mine[10]["source"] = "z = 3  # in the app"
    out = call(svc, "save_notebook", name="Big", cells=mine, disk_stamp=opened["disk_stamp"])
    assert out["changed_on_disk"]
    assert svc.notebooks.load("Big")["cells"][834]["source"] == "y = 2  # from VS Code"


def test_a_cell_setting_changed_in_vs_code_counts_as_a_change_and_isnt_lost(svc):
    opened = big(svc)
    vs_code_saves(svc, lambda nb: nb["cells"][701]["metadata"].__setitem__("tags", ["parameters"]))
    assert call(svc, "notebook_disk_stamp", name="Big")["disk_stamp"] != opened["disk_stamp"]  # the app reloads it
    out = call(svc, "save_notebook", name="Big", cells=opened["cells"], disk_stamp=opened["disk_stamp"])
    assert out["changed_on_disk"]  # the app's copy, without the tag, doesn't overwrite it
    assert svc.notebooks.load("Big")["cells"][701]["meta"]["tags"] == ["parameters"]
    fresh = call(svc, "open_notebook", name="Big")
    fresh["cells"][7]["source"] = "edited in the app"
    out = call(svc, "save_notebook", name="Big", cells=fresh["cells"], disk_stamp=fresh["disk_stamp"])
    assert "changed_on_disk" not in out
    saved = svc.notebooks.load("Big")
    assert saved["cells"][701]["meta"]["tags"] == ["parameters"] and saved["cells"][7]["source"] == "edited in the app"
    assert len(saved["cells"]) == CELLS


def test_outputs_saved_in_vs_code_are_no_change_to_reload_and_stay_through_the_apps_save(svc):
    opened = big(svc)
    output = {"output_type": "stream", "name": "stdout", "text": ["ran in VS Code\n"]}

    def ran(nb):
        for c in nb["cells"][1:300]:
            if c["cell_type"] == "code":
                c["outputs"], c["execution_count"] = [output], 1
    vs_code_saves(svc, ran)
    assert call(svc, "notebook_disk_stamp", name="Big")["disk_stamp"] == opened["disk_stamp"]  # no needless reload
    mine = [dict(c) for c in opened["cells"]]
    mine[999]["source"] = "last cell edited in the app"
    out = call(svc, "save_notebook", name="Big", cells=mine, disk_stamp=opened["disk_stamp"])
    assert "changed_on_disk" not in out
    nb = json.loads(svc.notebooks.path("Big").read_text(encoding="utf-8"))
    assert nb["cells"][1]["outputs"] == [output] and nb["cells"][1]["execution_count"] == 1
    assert "".join(nb["cells"][999]["source"]) == "last cell edited in the app"
