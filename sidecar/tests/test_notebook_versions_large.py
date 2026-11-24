"""Versions of a large notebook: a restore never loses what another program saved, automatic
versions stay bounded, and a crash part way through writing a version or the list of versions
costs at most that one version (notebook_versions, notebook_version_restore)."""
import json

import pytest

from fabric_desktop import jsonfile
from fabric_desktop import notebook_versions as versions
from fabric_desktop.service import Service

CELLS = 1000


@pytest.fixture
def svc(tmp_path):
    return Service(client_id="x", root=tmp_path)


def cells_of(n_lines=80, tag="cell"):
    return [{"type": "code" if i % 4 else "markdown", "source": f"# {tag} {i}\n" + "x = 1\n" * n_lines} for i in range(CELLS)]


def big(svc):
    svc.save_notebook("Big", cells_of())
    return svc.open_notebook("Big")


def vs_code_saves(svc, change):
    path = svc.notebooks.path("Big")
    nb = json.loads(path.read_text(encoding="utf-8"))
    change(nb)
    path.write_text(json.dumps(nb, indent=1), encoding="utf-8")


def sources(svc, vid):
    return [c["source"] for c in svc.notebook_version("Big", vid)["cells"]]


def test_restoring_over_a_change_vs_code_saved_keeps_that_change_as_before_restore(svc):
    opened = big(svc)
    first = svc.notebook_versions("Big")["versions"][0]["id"]
    vs_code_saves(svc, lambda nb: nb["cells"][640].__setitem__("source", "saved in VS Code"))
    out = svc.notebook_version_restore("Big", first)
    assert out["cells"][640]["source"] == opened["cells"][640]["source"] and len(out["cells"]) == CELLS
    before = next(v for v in svc.notebook_versions("Big")["versions"] if v["kind"] == "before restore")
    assert sources(svc, before["id"])[640] == "saved in VS Code" and before["cells"] == CELLS


def test_a_restore_cut_short_after_keeping_the_current_file_leaves_the_file_as_it_was(svc):
    big(svc)
    first = svc.notebook_versions("Big")["versions"][0]["id"]
    vs_code_saves(svc, lambda nb: nb["cells"][5].__setitem__("source", "saved in VS Code"))
    before = svc.notebooks.path("Big").read_bytes()

    def closed(*a, **k):
        raise KeyboardInterrupt("the app closed")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(svc.notebooks, "save", closed)
        with pytest.raises(KeyboardInterrupt):
            svc.notebook_version_restore("Big", first)
    assert svc.notebooks.path("Big").read_bytes() == before
    kept = next(v for v in svc.notebook_versions("Big")["versions"] if v["kind"] == "before restore")
    assert sources(svc, kept["id"])[5] == "saved in VS Code"


def test_saves_within_a_few_minutes_share_one_automatic_version(svc):
    root = svc._versions_root
    t = 1_800_000_000.0
    for i in range(10):
        nb = {"cells": [{"cell_type": "code", "source": f"{i}\n" + "y\n" * 5, "metadata": {}} for _ in range(CELLS)]}
        versions.record(root, "Big", nb, now=t + i * 20)  # every 20 seconds
    entries = versions._entries(root, "Big")
    assert [e["kind"] for e in entries] == ["auto"] and entries[0]["cells"] == CELLS
    assert versions.get(root, "Big", entries[0]["id"])["nb"]["cells"][0]["source"].startswith("9")  # the newest save


def test_automatic_versions_of_a_large_notebook_stay_bounded_on_disk(svc):
    root = svc._versions_root
    t = 1_800_000_000.0
    for i in range(versions.MAX_AUTO + 15):
        nb = {"cells": [{"cell_type": "code", "source": f"{i} {k}", "metadata": {}} for k in range(CELLS)]}
        versions.record(root, "Big", nb, now=t + i * (versions.COALESCE_MINUTES + 1) * 60)
    entries = versions._entries(root, "Big")
    assert len(entries) == versions.MAX_AUTO
    folder = versions._folder(root, "Big")
    assert versions._ids_on_disk(folder) == {e["id"] for e in entries}  # pruned files are gone, not just unlisted
    assert versions.get(root, "Big", entries[0]["id"])["nb"]["cells"][0]["source"] == f"{versions.MAX_AUTO + 14} 0"


def test_a_list_of_versions_cut_short_is_rebuilt_from_the_versions(svc):
    big(svc)
    for i in range(3):
        svc.notebook_checkpoint("Big", f"step {i}")
    listed = svc.notebook_versions("Big")["versions"]
    index = versions._folder(svc._versions_root, "Big") / versions.INDEX
    index.write_text(index.read_text(encoding="utf-8")[:40], encoding="utf-8")  # cut short mid-write
    assert svc.notebook_versions("Big")["versions"] == listed
    assert json.loads(index.read_text(encoding="utf-8"))["versions"]  # and written whole again


def test_a_version_cut_short_by_closing_the_app_leaves_no_half_file_and_the_list_as_it_was(svc):
    big(svc)
    listed = svc.notebook_versions("Big")["versions"]
    folder = versions._folder(svc._versions_root, "Big")
    on_disk = sorted(p.name for p in folder.iterdir())

    def closed(src, dst):
        raise KeyboardInterrupt("the app closed")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(jsonfile, "replace", closed)
        with pytest.raises(KeyboardInterrupt):
            svc.notebook_checkpoint("Big", "never kept")
    assert sorted(p.name for p in folder.iterdir()) == on_disk  # no half version, no temp file
    assert svc.notebook_versions("Big")["versions"] == listed


def test_a_damaged_version_file_says_it_isnt_kept_and_the_others_still_open(svc):
    big(svc)
    t = 1_800_000_000.0
    nb = json.loads(svc.notebooks.path("Big").read_text(encoding="utf-8"))
    good = versions.record(svc._versions_root, "Big", nb, kind="checkpoint", label="good", now=t)
    nb["cells"][0]["source"] = ["damaged"]
    bad = versions.record(svc._versions_root, "Big", nb, kind="checkpoint", label="damaged", now=t + 1)
    path = versions._folder(svc._versions_root, "Big") / f"{bad['id']}.json"
    path.write_text(path.read_text(encoding="utf-8")[:2000], encoding="utf-8")  # damaged on disk
    with pytest.raises(ValueError, match="isn't kept"):
        svc.notebook_version("Big", bad["id"])
    assert len(sources(svc, good["id"])) == CELLS
