"""Notebooks saved in notebooks repositories, and where their data is per environment (item 37, stage 2)."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.git import scopes
from fabric_desktop.git.cli import GitError
from fabric_desktop.notebook import NotebookShelf
from fabric_desktop.service import Service

from test_git import git_home  # noqa: F401  (an autouse fixture)

TEST = {"workspace_id": "ws-test", "workspace_name": "Sales Test", "lakehouse_id": "lh-test", "lakehouse_name": "LH_Silver"}


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    yield s
    s.kernel.shutdown() if hasattr(s.kernel, "shutdown") else None


def repo_of(svc, tmp_path, name="sales-notebooks"):
    svc.git_create(str(tmp_path / name), "notebooks")
    return str((tmp_path / name).resolve())


def test_a_notebooks_repository_is_a_place_to_save(svc, tmp_path):
    root = repo_of(svc, tmp_path)
    svc.git_create(str(tmp_path / "plat"), "platform")  # other kinds are not places for notebooks
    places = svc.notebook_places()["places"]
    assert [p["label"] for p in places] == ["This PC", "sales-notebooks"]
    nb = svc.new_notebook(place=root)
    assert nb["place"] == {"root": root, "label": "sales-notebooks"}
    assert (tmp_path / "sales-notebooks" / "notebooks" / f"{nb['name']}.ipynb").is_file()
    assert svc.git_status(root)["dirty"]  # a change to review, nothing committed
    home = svc.new_notebook()
    assert home["place"] is None and home["name"] != nb["name"]  # names stay unique across places
    listed = {n["name"]: n["place"] for n in svc.list_notebooks()}
    assert listed == {nb["name"]: {"root": root, "label": "sales-notebooks"}, home["name"]: None}


def test_versioning_the_apps_own_folder_is_not_a_second_place(svc):
    folder = svc.notebooks.folder
    folder.mkdir(parents=True)
    svc.git_init(str(folder), kind="notebooks")
    assert [p["label"] for p in svc.notebook_places()["places"]] == ["This PC"]


def test_moving_a_notebook_never_replaces_or_loses_one(svc, tmp_path):
    root = repo_of(svc, tmp_path)
    nb = svc.new_notebook()
    name = nb["name"]
    svc.save_notebook(name, [{"type": "code", "source": "x = 1"}])
    out = svc.move_notebook(name, root)
    assert out["moved"] and out["place"]["root"] == root
    assert not (svc.notebooks.folder / f"{name}.ipynb").exists()
    assert svc.open_notebook(name)["cells"][0]["source"] == "x = 1"
    # Another copy of that name appears back home (the home copy wins by name): moving it into the
    # repository refuses, and both files stay as they are.
    (svc.notebooks.folder / f"{name}.ipynb").write_text(json.dumps({"cells": [], "metadata": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="already in sales-notebooks. Nothing was moved"):
        svc.move_notebook(name, root)
    assert (svc.notebooks.folder / f"{name}.ipynb").exists()
    assert "x = 1" in (tmp_path / "sales-notebooks" / "notebooks" / f"{name}.ipynb").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        svc.move_notebook(name, str(tmp_path / "not-a-repo"))


def test_renaming_keeps_the_notebook_where_it_is_and_names_unique(svc, tmp_path):
    root = repo_of(svc, tmp_path)
    a = svc.new_notebook(place=root)["name"]
    b = svc.new_notebook()["name"]
    with pytest.raises(ValueError):
        svc.rename_notebook(a, b)
    svc.rename_notebook(a, "Orders silver")
    assert svc.open_notebook("Orders silver")["place"]["root"] == root


def test_locations_come_from_the_repository_and_only_hold_ids_and_names(svc, tmp_path):
    root = repo_of(svc, tmp_path)
    name = svc.new_notebook(place=root)["name"]
    envs = svc.open_notebook(name)["locations"]["envs"]
    assert envs["dev"] == {"this_pc": True, "lakehouse": None} and envs["test"] is None  # the template
    out = svc.git_set_locations(root, {"dev": {"this_pc": True}, "test": TEST | {"password": "nope"}})
    assert out["envs"]["test"]["lakehouse"] == {"workspace_id": "ws-test", "lakehouse_id": "lh-test",
                                                 "name": "LH_Silver", "workspace_name": "Sales Test"}
    assert out["envs"]["prod"] is None
    text = (tmp_path / "sales-notebooks" / scopes.LOCATIONS).read_text(encoding="utf-8")
    assert "password =" not in text and "nope" not in text
    assert svc.open_notebook(name)["locations"]["envs"]["test"]["lakehouse"]["lakehouse_id"] == "lh-test"
    assert scopes.LOCATIONS in json.dumps(out["status"]["untracked"] + out["status"]["unstaged"])


def test_locations_belong_to_notebooks_repositories_and_reject_bad_values(svc, tmp_path):
    svc.git_create(str(tmp_path / "plat"), "platform")
    with pytest.raises(GitError):
        svc.git_set_locations(str(tmp_path / "plat"), {"test": TEST})
    root = repo_of(svc, tmp_path)
    with pytest.raises(GitError):
        svc.git_set_locations(root, {"test": TEST | {"workspace_name": "a\nb"}})
    (tmp_path / "sales-notebooks" / scopes.LOCATIONS).write_text("[test\n", encoding="utf-8")
    bad = svc.git_locations(root)
    assert bad["problem"] and bad["envs"]["test"] is None


def test_a_quoted_name_round_trips(tmp_path):
    out = scopes.write_locations(tmp_path, {"prod": TEST | {"workspace_name": 'Sales "Prod" \\ EU'}})
    assert out["envs"]["prod"]["workspace_name"] == 'Sales "Prod" \\ EU'


def test_the_shelf_finds_the_apps_own_copy_first(tmp_path):
    repo_folder = tmp_path / "r" / "notebooks"
    repo_folder.mkdir(parents=True)
    shelf = NotebookShelf(tmp_path / "home", lambda: [(str(tmp_path / "r"), "r", repo_folder)])
    shelf.folder.mkdir()
    for folder in (shelf.folder, repo_folder):
        (folder / "Same.ipynb").write_text(json.dumps({"cells": [], "metadata": {}}), encoding="utf-8")
    assert shelf.path("Same").parent == shelf.folder
    assert [(n["name"], n["place"]) for n in shelf.list()] == [("Same", None)]  # only the one reachable by name


def test_publishing_with_a_location_does_not_save_it_into_the_notebook(svc, tmp_path, monkeypatch):
    root = repo_of(svc, tmp_path)
    name = svc.new_notebook(place=root)["name"]
    sent = {}

    class Fabric:
        def save_notebook(self, ws, nb_name, ipynb):
            sent["ws"], sent["ipynb"] = ws, json.loads(ipynb)
            return {"id": "item"}

    monkeypatch.setattr(Service, "fabric", Fabric(), raising=False)
    monkeypatch.setattr(svc, "_schemas_enabled", lambda *a: True)
    ref = {"workspace_id": "ws-test", "lakehouse_id": "lh-test", "name": "LH_Silver", "workspace_name": "Sales Test"}
    svc.publish_notebook(name, [{"type": "code", "source": "1"}], None, location_ref=ref)
    assert sent["ws"] == "ws-test"
    assert svc.open_notebook(name)["lakehouse"] is None  # it keeps following the environment


def test_saving_locations_or_a_kind_replaces_a_link_never_its_target(svc, tmp_path):
    root = repo_of(svc, tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    for name in (scopes.LOCATIONS, scopes.MARKER):
        link = tmp_path / "sales-notebooks" / name
        link.unlink()
        try:
            link.symlink_to(victim)
        except OSError:
            pytest.skip("this system can't make symbolic links")
    # The marker is now a link, so the repository has no readable kind: set it, confirming.
    scopes.set_kind(svc._repo(root), "notebooks", confirm=True)
    svc.git_set_locations(root, {"test": TEST})
    assert victim.read_text(encoding="utf-8") == "keep me\n"
    for name in (scopes.LOCATIONS, scopes.MARKER):
        assert not (tmp_path / "sales-notebooks" / name).is_symlink()
    assert svc.git_locations(root)["envs"]["test"]["lakehouse_id"] == "lh-test"


def test_a_delete_character_is_refused(svc, tmp_path):
    root = repo_of(svc, tmp_path)
    with pytest.raises(GitError):
        svc.git_set_locations(root, {"test": TEST | {"workspace_name": "Sales\x7fTest"}})


def test_a_failed_copy_leaves_nothing_behind(svc, tmp_path, monkeypatch):
    root = repo_of(svc, tmp_path)
    name = svc.new_notebook()["name"]
    real_open = open

    def short_write(path, mode="r", *a, **k):
        f = real_open(path, mode, *a, **k)
        if mode == "xb":
            f.write = lambda data: real_open.__call__  # writes nothing: the check sees a mismatch
        return f

    monkeypatch.setattr("builtins.open", short_write)
    with pytest.raises(OSError, match="still where it was"):
        svc.move_notebook(name, root)
    monkeypatch.undo()
    assert not (tmp_path / "sales-notebooks" / "notebooks" / f"{name}.ipynb").exists()
    assert svc.move_notebook(name, root)["moved"]  # a retry works
