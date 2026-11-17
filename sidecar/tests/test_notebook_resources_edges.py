"""A notebook's resources folder: the edges the main tests don't reach. Long names, text that isn't
a builtin path, another drive, no free name, a write or rename that fails part way, and a folder copy
that doesn't match."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop import notebook_resources as nr
from fabric_desktop.notebook_resources import Resources


def test_a_name_longer_than_the_limit_is_refused():
    with pytest.raises(ValueError, match=f"{nr.MAX_NAME} characters"):
        nr.check_name("a" * (nr.MAX_NAME + 1))
    assert nr.check_name("a" * nr.MAX_NAME) == "a" * nr.MAX_NAME


def test_text_that_isnt_a_builtin_path_has_no_real_path(tmp_path):
    assert nr.real_path("data/regions.csv", tmp_path) is None


def test_paths_on_another_drive_are_never_within(monkeypatch):
    def other_drive(paths):
        raise ValueError("Paths don't have the same drive")

    monkeypatch.setattr(nr.os.path, "commonpath", other_drive)
    assert nr._within("D:/x", "C:/notebooks") is False


def test_a_file_with_every_suggested_name_taken_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda self: True)
    with pytest.raises(ValueError, match="free name"):
        nr._free(tmp_path, "regions.csv")


def test_a_write_that_fails_leaves_no_partial_file(tmp_path, monkeypatch):
    res = Resources(tmp_path / "Sales.resources")
    real_open = open

    class Full:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

        def write(self, data):
            self.f.write(data[:3])
            raise OSError(28, "No space left on device")

    monkeypatch.setattr("builtins.open", lambda p, mode="r", *a, **k: Full(real_open(p, mode, *a, **k)))
    with pytest.raises(OSError, match="No space"):
        res.add("regions.csv", b"region\nEast\n")
    monkeypatch.undo()
    assert not (res.files / "regions.csv").exists()
    assert res.add("regions.csv", b"x")["added"] is True  # a retry isn't blocked


def test_renaming_a_file_to_its_own_name_changes_nothing(tmp_path):
    res = Resources(tmp_path / "Sales.resources")
    res.add("regions.csv", b"x")
    assert res.rename("regions.csv", "regions.csv") == {"name": "regions.csv", "path": "builtin/regions.csv"}
    assert [r["name"] for r in res.list()["files"]] == ["regions.csv"]


def test_a_rename_that_fails_frees_the_reserved_name(tmp_path, monkeypatch):
    res = Resources(tmp_path / "Sales.resources")
    res.add("regions.csv", b"x")

    def locked(src, dest):
        raise PermissionError("in use by another program")

    monkeypatch.setattr(nr.jsonfile, "replace", locked)
    with pytest.raises(PermissionError):
        res.rename("regions.csv", "areas.csv")
    assert sorted(p.name for p in res.files.iterdir()) == ["regions.csv"]


def test_a_case_only_rename_that_fails_removes_nothing(tmp_path, monkeypatch):
    res = Resources(tmp_path / "Sales.resources")
    res.add("regions.csv", b"x")
    monkeypatch.setattr(nr.os.path, "normcase", str.lower)  # as Windows compares names

    def locked(src, dest):
        raise PermissionError("in use by another program")

    monkeypatch.setattr(nr.jsonfile, "replace", locked)
    with pytest.raises(PermissionError):
        res.rename("regions.csv", "Regions.csv")
    assert (res.files / "regions.csv").read_bytes() == b"x"


def test_count_is_the_number_of_files(tmp_path):
    folder = tmp_path / "Sales.resources"
    assert nr.count(folder) == 0
    res = Resources(folder)
    res.add("a.csv", b"1")
    res.add("b.csv", b"2")
    (res.files / "sub").mkdir()  # a folder isn't a resource file
    assert nr.count(folder) == 2


def test_copying_onto_an_existing_folder_moves_nothing(tmp_path):
    src = tmp_path / "Sales.resources"
    Resources(src).add("a.csv", b"1")
    (tmp_path / "Copy.resources").mkdir()
    with pytest.raises(ValueError, match="already there"):
        nr.copy_folder(src, tmp_path / "Copy.resources")


def test_a_copy_that_doesnt_match_is_removed(tmp_path, monkeypatch):
    src = tmp_path / "Sales.resources"
    Resources(src).add("a.csv", b"12345")
    real = nr.shutil.copytree

    def short_copy(a, b, *args, **kwargs):
        real(a, b, *args, **kwargs)
        cut = Path(b) / nr.BUILTIN / "a.csv"
        if cut.exists():
            cut.write_bytes(b"12")  # a copy cut short

    monkeypatch.setattr(nr.shutil, "copytree", short_copy)
    dest = tmp_path / "Copy.resources"
    with pytest.raises(OSError, match="a.csv doesn't match"):
        nr.copy_folder(src, dest)
    assert not dest.exists()
    assert (src / nr.BUILTIN / "a.csv").read_bytes() == b"12345"
