"""The notebook shelf's move and rename refusals, and the variable panel's helpers for values whose
size, columns or text can't be read. Notebooks live in temporary folders; no kernel runs a cell."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fabric_desktop import notebook as nb
from fabric_desktop import notebook_resources
from fabric_desktop.notebook import NotebookShelf
from fabric_desktop.notebook_resources import Resources

CELLS = [{"type": "code", "source": "x = 1"}]


@pytest.fixture
def shelf(tmp_path):
    repo = tmp_path / "repo"
    s = NotebookShelf(tmp_path / "app", places=lambda: [(str(repo), "repo", repo / "notebooks")])
    s.save("A", CELLS)
    return s


def repo(shelf):
    return shelf.places()[0]["root"]


def test_rename_refuses_a_taken_name_and_a_missing_notebook(shelf):
    shelf.save("B", CELLS)
    with pytest.raises(ValueError, match="already exists"):
        shelf.rename("A", "B")
    with pytest.raises(ValueError, match="There's no notebook named 'Gone'"):
        shelf.rename("Gone", "C")


def test_moving_a_missing_notebook_is_refused(shelf):
    with pytest.raises(ValueError, match="There's no notebook named 'Gone'"):
        shelf.move("Gone", repo(shelf))


def test_moving_to_an_unknown_repository_is_refused(shelf, tmp_path):
    with pytest.raises(ValueError, match="isn't a notebooks repository"):
        shelf.move("A", str(tmp_path / "elsewhere"))


def test_moving_a_notebook_to_where_it_already_is_moves_nothing(shelf):
    assert shelf.move("A", None) == {"name": "A", "place": None, "moved": False}
    assert shelf.path("A").is_file()


def test_a_resources_folder_already_at_the_target_stops_the_move(shelf):
    Resources(shelf.resources("A")).add("x.csv", b"1")
    there = shelf.places()[0]["folder"] / "A.resources"
    there.mkdir(parents=True)
    with pytest.raises(ValueError, match="A folder named 'A.resources' is already in repo. Nothing was moved."):
        shelf.move("A", repo(shelf))
    assert (shelf.folder / "A.ipynb").is_file()


def test_a_notebook_already_at_the_target_is_never_replaced(shelf):
    folder = shelf.places()[0]["folder"]
    folder.mkdir(parents=True)
    (folder / "A.ipynb").write_bytes(b"theirs")
    # The app's own copy is found first, so the move reaches the open("xb") refusal.
    with pytest.raises(ValueError, match="A notebook named 'A' is already in repo. Nothing was moved."):
        shelf.move("A", repo(shelf))
    assert (folder / "A.ipynb").read_bytes() == b"theirs"
    assert (shelf.folder / "A.ipynb").is_file()


def test_a_copy_that_doesnt_match_is_removed_and_the_notebook_stays(shelf, monkeypatch):
    real = nb.Path.read_bytes
    calls = {"n": 0}

    def read_bytes(self):
        calls["n"] += 1
        data = real(self)
        return data + b"!" if calls["n"] == 2 else data  # the read back of the copy differs

    monkeypatch.setattr(nb.Path, "read_bytes", read_bytes)
    with pytest.raises(OSError, match="Couldn't copy A to repo .*the copy doesn't match.*still where it was"):
        shelf.move("A", repo(shelf))
    monkeypatch.undo()
    assert not (shelf.places()[0]["folder"] / "A.ipynb").exists()
    assert (shelf.folder / "A.ipynb").is_file()


def test_a_resources_copy_that_fails_removes_the_copied_notebook(shelf, monkeypatch):
    Resources(shelf.resources("A")).add("x.csv", b"1")

    def refuse(src, dest):
        raise OSError("disk full")

    monkeypatch.setattr(notebook_resources, "copy_folder", refuse)
    with pytest.raises(OSError, match="Couldn't copy A's resources to repo \\(disk full\\)"):
        shelf.move("A", repo(shelf))
    assert not (shelf.places()[0]["folder"] / "A.ipynb").exists()
    assert (shelf.folder / "A.ipynb").is_file()


def test_an_old_resources_folder_that_cant_be_removed_is_noted(shelf, monkeypatch):
    Resources(shelf.resources("A")).add("x.csv", b"1")

    def held(folder):
        raise OSError("file in use")

    monkeypatch.setattr(notebook_resources, "remove_folder", held)
    out = shelf.move("A", repo(shelf))
    assert out["moved"] is True
    assert "couldn't be removed (file in use). Delete it by hand." in out["note"]
    assert (shelf.places()[0]["folder"] / "A.ipynb").is_file()


def test_rename_never_replaces_a_notebook_made_since_the_check(tmp_path):
    src = tmp_path / "A.ipynb"
    src.write_text("{}")
    dest = tmp_path / "B.ipynb"
    dest.write_text("theirs")
    with pytest.raises(ValueError, match="A notebook named 'B' already exists. Nothing was renamed."):
        nb._rename_with_resources(src, dest, "B")
    assert dest.read_text() == "theirs"


def test_a_failed_rename_puts_the_resources_back_and_removes_the_new_folder(tmp_path, monkeypatch):
    src = tmp_path / "A.ipynb"
    src.write_text("{}")
    Resources(notebook_resources.folder_for(src)).add("x.csv", b"1")
    dest = tmp_path / "Sales" / "B.ipynb"

    def refuse(self, target):
        raise OSError("locked")

    real_rename = nb.Path.rename
    monkeypatch.setattr(nb.Path, "rename", lambda self, t: refuse(self, t) if self == src else real_rename(self, t))
    with pytest.raises(OSError, match="locked"):
        nb._rename_with_resources(src, dest, "Sales/B")
    monkeypatch.undo()
    assert (tmp_path / "A.resources" / "builtin" / "x.csv").is_file()
    assert not (tmp_path / "Sales").exists()


def test_resolved_falls_back_to_the_text_when_the_path_cant_be_resolved(monkeypatch, tmp_path):
    def broken(self, strict=False):
        raise OSError("bad path")

    monkeypatch.setattr(nb.Path, "resolve", broken)
    assert nb._resolved(tmp_path / "x") == nb.os.path.normcase(str(tmp_path / "x"))


# ---- the variable panel's helpers


def test_a_pandas_frames_columns_and_size_are_read():
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    assert nb._columns(df) == [{"name": "a", "type": "int64"}, {"name": "b", "type": "object"}]
    assert nb._memory(df) == int(df.memory_usage(deep=False).sum())


def test_a_numpy_arrays_size_is_its_buffer():
    assert nb._memory(np.zeros(10, dtype=np.int64)) == 80


class _Broken:
    __module__ = "polars.fake"

    @property
    def schema(self):
        raise RuntimeError("no schema")

    def estimated_size(self):
        raise RuntimeError("no size")

    def __repr__(self):
        raise RuntimeError("no repr")


def test_values_whose_columns_size_or_text_cant_be_read():
    assert nb._columns(_Broken()) is None
    assert nb._memory(_Broken()) is None
    assert nb._preview(object.__new__(type("X", (), {"__repr__": lambda self: 1 / 0}))) == "?"


def test_counts_that_arent_numbers_and_billions():
    assert nb._count("many") == "many"
    assert nb._count(2_500_000_000) == "2.5B"
    assert nb._count(3_000_000_000) == "3B"
