"""Notebook files (notebook_files.py), the paths the main tests don't reach: searches limited to a
place or cut short, files that aren't Jupyter format 4, Fabric definitions without cells, and
Recently deleted entries that can't be read or whose repository is gone. All on local disk."""

from __future__ import annotations

import base64
import datetime as dt
import json

import pytest

from fabric_desktop import notebook_files as nf
from fabric_desktop.notebook import NotebookShelf


@pytest.fixture
def repo_root(tmp_path):
    root = tmp_path / "repo"
    (root / "notebooks").mkdir(parents=True)
    return root


@pytest.fixture
def shelf(tmp_path, repo_root):
    places = [(str(repo_root), "repo", repo_root / "notebooks")]
    return NotebookShelf(tmp_path / "app" / "notebooks", lambda: places)


def code(*sources):
    return [{"type": "code", "source": s} for s in sources]


def make(shelf, name, *sources, place=None):
    return nf.create(shelf, name, place, code(*sources), None, None, None)


# ---- search


def test_search_can_be_limited_to_this_pc_or_one_repository(shelf, repo_root):
    make(shelf, "Here", "needle = 1")
    make(shelf, "There", "needle = 2", place=str(repo_root))
    assert [n["name"] for n in nf.search(shelf, "needle", saved_in="pc")["notebooks"]] == ["Here"]
    assert [n["name"] for n in nf.search(shelf, "needle", saved_in=str(repo_root))["notebooks"]] == ["There"]


def test_a_notebook_that_cant_be_read_is_reported_not_raised(shelf):
    make(shelf, "Broken", "x = 1")
    shelf.path("Broken").write_text("{not json")
    out = nf.search(shelf, "x")
    assert out["notebooks"] == [] and out["problems"] == [{"name": "Broken", "text": "Couldn't read it (JSONDecodeError)."}]


def test_search_stops_at_its_limits(shelf, monkeypatch):
    make(shelf, "Many", "\n".join(["hit"] * 5), "hit")
    make(shelf, "More", "\n".join(["hit"] * 5), "hit")
    monkeypatch.setattr(nf, "MAX_HITS", 3)
    monkeypatch.setattr(nf, "MAX_NOTEBOOKS_FOUND", 1)
    out = nf.search(shelf, "hit")
    assert out["more"] is True and len(out["notebooks"]) == 1
    assert len(out["notebooks"][0]["hits"]) == 3 and out["notebooks"][0]["more"] is True


# ---- files from anywhere


@pytest.mark.parametrize("nb, says", [
    ({"nbformat": 3, "cells": []}, "Only Jupyter notebook format 4"),
    ({"nbformat": 4, "cells": ["text"]}, "a cell isn't readable"),
])
def test_a_file_that_isnt_a_format_4_notebook_is_refused(nb, says):
    with pytest.raises(ValueError, match=says):
        nf.checked(nb)


def test_a_file_over_the_size_limit_isnt_opened(monkeypatch):
    monkeypatch.setattr(nf, "MAX_FILE_BYTES", 10)
    with pytest.raises(ValueError, match="so it isn't opened"):
        nf._parse('{"cells": [], "nbformat": 4}')


def test_a_notebook_in_another_language_says_to_run_it_in_fabric():
    nb = {"nbformat": 4, "metadata": {"kernelspec": {"name": "sparkR"}},
          "cells": [{"cell_type": "code", "source": "x <- 1", "metadata": {}}]}
    assert nf.read(nb)["summary"]["notes"][0] == "It's a sparkr notebook. KratosForge runs Python cells only, so run it in Fabric."
    assert nf._language({"metadata": "junk"}) == ""


def test_a_fabric_definition_without_a_notebook_part_is_refused():
    with pytest.raises(ValueError, match="didn't return the notebook's cells"):
        nf.ipynb_of({"definition": {"parts": [{"path": ".platform", "payload": ""}]}})
    payload = base64.b64encode(json.dumps({"cells": []}).encode()).decode()
    assert nf.ipynb_of({"parts": [{"path": "notebook-content.ipynb", "payload": payload}]}) == {"cells": []}


def test_a_fabric_item_already_here_isnt_copied_and_a_taken_name_is_refused(shelf):
    assert nf.from_fabric(shelf, {}, {"name": "Sales"}, None, "Sales here") == {"exists": True, "name": "Sales here"}
    make(shelf, "Sales", "x = 1")
    with pytest.raises(ValueError, match="isn't this Fabric item"):
        nf.from_fabric(shelf, {}, {"name": "Sales", "workspace_id": "ws"}, None, None)


def test_a_save_that_fails_leaves_no_reserved_file(shelf, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(nf.NotebookStore, "save", broken)
    with pytest.raises(OSError, match="disk full"):
        make(shelf, "Half", "x = 1")
    assert not shelf.path("Half").exists()


# ---- what was published


def test_a_published_record_that_cant_be_read_is_none_and_item_ids_are_checked(tmp_path):
    (tmp_path / "abc-1.json").write_text("{bad")
    assert nf.published(tmp_path, "abc-1") is None and nf.published(tmp_path, "missing") is None
    with pytest.raises(ValueError, match="isn't a Fabric item id"):
        nf.published(tmp_path, "../escape")


# ---- Recently deleted


def test_unreadable_or_expired_entries_are_left_alone_or_hidden(shelf):
    make(shelf, "Gone", "x = 1")
    nf.delete(shelf, "Gone", [])
    trash = nf._trash(shelf)
    (trash / "junk.json").write_text("{bad")
    (trash / "orphan.json").write_text(json.dumps({"kept_until": "2999-01-01T00:00:00+00:00", "deleted_at": "x"}))
    assert [d["name"] for d in nf.deleted(shelf)] == ["Gone"]
    nf._purge(trash, dt.datetime.now(dt.timezone.utc))
    assert (trash / "junk.json").exists()  # never guessed at


def test_deleting_a_notebook_that_isnt_there_is_refused(shelf):
    with pytest.raises(ValueError, match="There's no notebook named 'Nope'"):
        nf.delete(shelf, "Nope", [])
    assert nf.deleted(shelf) == []


def test_a_copy_that_doesnt_match_keeps_the_notebook(shelf, monkeypatch):
    make(shelf, "Keep", "x = 1")
    real = nf.Path.read_bytes

    def flaky(self):
        data = real(self)
        return data + b"!" if self.parent.name == "notebooks-deleted" else data

    monkeypatch.setattr(nf.Path, "read_bytes", flaky)
    with pytest.raises(OSError, match="so it wasn't deleted"):
        nf.delete(shelf, "Keep", [])
    assert shelf.path("Keep").is_file()


@pytest.mark.parametrize("tid, says", [
    ("../x", "That isn't a deleted notebook"),
    ("20260101-000000-abcdef12", "isn't in Recently deleted any more"),
])
def test_restoring_something_that_isnt_there_is_refused(shelf, tid, says):
    with pytest.raises(ValueError, match=says):
        nf.restore(shelf, tid)


def test_a_notebook_from_a_repository_no_longer_open_comes_back_on_this_pc(tmp_path, repo_root):
    places = [(str(repo_root), "repo", repo_root / "notebooks")]
    shelf = NotebookShelf(tmp_path / "app" / "notebooks", lambda: places)
    make(shelf, "Moved", "x = 1", place=str(repo_root))
    tid = nf.delete(shelf, "Moved", [])["id"]
    make(shelf, "Moved", "y = 2")  # taken now
    with pytest.raises(ValueError, match="already exists. Restore it under another name"):
        nf.restore(shelf, tid)
    places.clear()  # the repository isn't open any more
    out = nf.restore(shelf, tid, "Moved back")
    assert out["moved_to_pc"] is True and shelf.path("Moved back").parent == tmp_path / "app" / "notebooks"
