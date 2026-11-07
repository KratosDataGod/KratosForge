"""Folders for notebooks (notebook_folders.py): the edges test_notebook_folders.py leaves out. A rename that
fails part way puts every notebook back where it was, a folder made meanwhile stops a rename, a change of
case, and the plain refusals. Called on the shelf directly; nothing here reaches Fabric."""

from __future__ import annotations

import pathlib

import pytest

from fabric_desktop import notebook_folders as nf
from fabric_desktop.service import Service


@pytest.fixture
def shelf(tmp_path):
    return Service(root=tmp_path / "app").notebooks


def save(shelf, name):
    shelf.save(name, [{"type": "code", "source": "x = 1"}])


def test_a_folder_needs_a_name():
    for blank in ("", " / ", None):
        with pytest.raises(ValueError, match="Give the folder a name"):
            nf.check_folder(blank)


def test_a_rename_that_fails_part_way_puts_every_notebook_back(shelf, monkeypatch):
    save(shelf, "Sales/a")
    save(shelf, "Sales/b")
    src = shelf.folder / "Sales"
    before = sorted(p.name for p in src.iterdir())
    plan = nf.rename_plan(shelf, "Sales", "EMEA", None)
    real = pathlib.Path.rename

    def rename(self, target):
        if self.name == "b.ipynb" and self.parent == src:
            raise PermissionError(32, "in use by another process")
        return real(self, target)
    monkeypatch.setattr(pathlib.Path, "rename", rename)
    with pytest.raises(PermissionError):
        nf.rename(shelf, plan)
    assert sorted(p.name for p in src.iterdir()) == before
    assert not (shelf.folder / "EMEA").exists()


def test_a_notebook_that_cant_be_moved_back_stays_in_the_new_folder_and_the_first_error_is_reported(shelf, monkeypatch):
    """The rename stops on c; putting b back fails too. Nothing is deleted: a goes back, b stays in the
    new folder, and the error is the one that stopped the rename. On Linux and macOS a folder made between
    the exists check and the move is another such race; mkdir and the per-file check catch the rest."""
    for name in ("a", "b", "c"):
        save(shelf, f"Sales/{name}")
    src, dest = shelf.folder / "Sales", shelf.folder / "EMEA"
    plan = nf.rename_plan(shelf, "Sales", "EMEA", None)
    real = pathlib.Path.rename

    def rename(self, target):
        if self == src / "c.ipynb":
            raise PermissionError(32, "c is in use")
        if self == dest / "b.ipynb":
            raise PermissionError(32, "b is in use now")
        return real(self, target)
    monkeypatch.setattr(pathlib.Path, "rename", rename)
    with pytest.raises(OSError, match="c is in use") as raised:
        nf.rename(shelf, plan)
    assert "Some files are still in EMEA and couldn't be moved back to Sales: b.ipynb" in str(raised.value)
    assert isinstance(raised.value.__cause__, PermissionError)
    assert (src / "a.ipynb").is_file() and (src / "c.ipynb").is_file()
    assert (dest / "b.ipynb").is_file()  # split, but every notebook is still on disk


def test_a_file_that_appears_in_the_new_folder_meanwhile_stops_the_rename_and_stays(shelf, monkeypatch):
    save(shelf, "Sales/a")
    plan = nf.rename_plan(shelf, "Sales", "EMEA", None)
    dest = shelf.folder / "EMEA"
    real_mkdir = pathlib.Path.mkdir

    def mkdir(self, *a, **kw):
        real_mkdir(self, *a, **kw)
        if self == dest:
            (dest / "a.ipynb").write_text("{}", encoding="utf-8")  # someone else's, written just now
    monkeypatch.setattr(pathlib.Path, "mkdir", mkdir)
    with pytest.raises(OSError, match="appeared in EMEA meanwhile"):
        nf.rename(shelf, plan)
    assert (shelf.folder / "Sales" / "a.ipynb").is_file() and (dest / "a.ipynb").read_text() == "{}"


def test_a_folder_made_since_the_plan_stops_the_rename(shelf):
    save(shelf, "Sales/a")
    plan = nf.rename_plan(shelf, "Sales", "EMEA", None)
    (shelf.folder / "EMEA").mkdir()
    with pytest.raises(ValueError, match="already there. Nothing was renamed"):
        nf.rename(shelf, plan)
    assert (shelf.folder / "Sales" / "a.ipynb").is_file()


def test_renaming_to_the_same_name_does_nothing(shelf):
    save(shelf, "Sales/a")
    plan = nf.rename_plan(shelf, "Sales", "Sales", None)
    assert plan["moves"] == [] and plan["new_path"] == "Sales"
    nf.rename(shelf, plan)
    assert (shelf.folder / "Sales" / "a.ipynb").is_file()


def test_a_change_of_case_on_a_file_system_that_ignores_case_renames_in_one_step(shelf, monkeypatch):
    save(shelf, "Sales/a")
    src = shelf.folder / "Sales"
    monkeypatch.setattr(nf, "_same", lambda a, b: True)  # as Windows and macOS see Sales and SALES
    plan = {"path": "Sales", "new_path": "SALES", "src": src, "dest": src}
    nf.rename(shelf, plan)
    assert src.is_dir()  # renamed onto itself: the same folder


def test_two_paths_that_cant_be_compared_are_not_the_same(tmp_path):
    assert nf._same(tmp_path / "missing", tmp_path) is False
    assert nf._same(tmp_path, tmp_path) is True


def test_a_rename_that_would_take_a_notebooks_name_is_refused(shelf, monkeypatch):
    save(shelf, "Sales/a")
    monkeypatch.setattr(type(shelf), "_taken", lambda self, name: name == "EMEA/a")
    with pytest.raises(ValueError, match="'EMEA/a' already exists. Nothing was renamed"):
        nf.rename_plan(shelf, "Sales", "EMEA", None)


def test_a_folder_that_cant_be_read_lists_nothing(shelf, monkeypatch):
    save(shelf, "Sales/a")
    real = pathlib.Path.iterdir

    def iterdir(self):
        if self.name == "Sales":
            raise PermissionError(13, "denied")
        return real(self)
    monkeypatch.setattr(pathlib.Path, "iterdir", iterdir)
    assert nf._dirs(shelf.folder) == ["Sales"]
    assert nf._dirs(shelf.folder / "nowhere") == []


def test_a_folder_that_isnt_there_cant_be_deleted(shelf):
    with pytest.raises(ValueError, match="There's no folder 'Nope'"):
        nf.delete(shelf, "Nope", None)


def test_a_notebook_moves_only_when_it_is_there_and_to_its_own_place(shelf):
    save(shelf, "Top")
    with pytest.raises(ValueError, match="There's no notebook named 'Gone'"):
        nf.move_target(shelf, "Gone", "Sales")
    assert nf.move_target(shelf, "Top", "") == "Top"  # already at the top level
    with pytest.raises(ValueError, match="Make it with New folder first"):
        nf.move_target(shelf, "Top", "Sales")
    (shelf.folder / "Sales").mkdir()
    save(shelf, "Sales/Top")
    with pytest.raises(ValueError, match="Sales already has a notebook named 'Top'"):
        nf.move_target(shelf, "Top", "Sales")
    with pytest.raises(ValueError, match="the top level already has a notebook named 'Top'"):
        nf.move_target(shelf, "Sales/Top", "")
