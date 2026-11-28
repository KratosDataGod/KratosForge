"""notebook_resources.py: behavior that mutation testing (docs/quality/mutation-testing.md, batch 4)
showed no test pinned. The name and size limits at their edges, Windows device names, running out of
free names, what each folder helper returns, and a failed copy raising its own error."""

from __future__ import annotations

import shutil

import pytest

from fabric_desktop import notebook_resources as R


def test_a_name_of_the_most_characters_is_fine_and_one_more_is_refused():
    assert R.check_name("a" * 120) == "a" * 120
    with pytest.raises(ValueError, match="Keep file names to 120 characters"):
        R.check_name("a" * 121)


@pytest.mark.parametrize("name", ["COM1", "com9.txt", "LPT1", "lpt9.csv"])
def test_windows_device_names_are_refused(name):
    with pytest.raises(ValueError, match="Windows keeps for devices"):
        R.check_name(name)


@pytest.mark.parametrize("name", ["COM0.txt", "COM10.txt", "LPT0.csv", "LPT10.csv"])
def test_names_next_to_the_device_names_are_fine(name):
    assert R.check_name(name) == name


def test_a_file_of_the_largest_size_is_added_and_one_byte_more_is_refused(tmp_path):
    res = R.Resources(tmp_path / "nb.resources")
    limit = 50 * 1024 * 1024
    assert R.MAX_FILE_BYTES == limit
    with pytest.raises(ValueError) as e:
        res.add("big.bin", bytes(limit + 1))
    assert str(e.value) == "big.bin is 50.0 MB. Resource files can be up to 50 MB each."
    assert res.add("edge.bin", bytes(limit))["size"] == limit


def test_free_names_run_out_after_999(tmp_path):
    for n in range(2, 1000):
        (tmp_path / f"a ({n}).csv").touch()
    with pytest.raises(ValueError, match="Couldn't find a free name"):
        R._free(tmp_path, "a.csv")
    (tmp_path / "a (999).csv").unlink()
    assert R._free(tmp_path, "a.csv") == "a (999).csv"


def test_a_missing_file_is_not_found_for_reading_or_deleting(tmp_path):
    res = R.Resources(tmp_path / "nb.resources")
    with pytest.raises(FileNotFoundError, match="no resource file named 'x.csv'"):
        res.file("x.csv")
    with pytest.raises(FileNotFoundError):
        res.delete("x.csv")


def test_delete_says_what_it_deleted(tmp_path):
    res = R.Resources(tmp_path / "nb.resources")
    res.add("x.csv", b"1")
    assert res.delete("x.csv") == {"deleted": "x.csv"}
    assert not (res.files / "x.csv").exists()


def test_rename_folder_says_whether_it_moved_one(tmp_path):
    assert R.rename_folder(tmp_path / "none.resources", tmp_path / "b.resources") is False
    (tmp_path / "a.resources").mkdir()
    assert R.rename_folder(tmp_path / "a.resources", tmp_path / "b.resources") is True
    assert (tmp_path / "b.resources").is_dir()


def test_copy_and_remove_count_every_file_including_nested_ones(tmp_path):
    assert R.copy_folder(tmp_path / "none", tmp_path / "copy") == 0
    assert R.remove_folder(tmp_path / "none") == 0
    src = tmp_path / "a.resources" / "builtin"
    (src / "sub").mkdir(parents=True)
    (src / "x.csv").write_text("1")
    (src / "sub" / "y.csv").write_text("22")
    assert R.copy_folder(tmp_path / "a.resources", tmp_path / "b.resources") == 2
    assert R.remove_folder(tmp_path / "b.resources") == 2
    assert not (tmp_path / "b.resources").exists()


def test_a_copy_that_fails_before_making_its_folder_raises_its_own_error(tmp_path, monkeypatch):
    (tmp_path / "a.resources").mkdir()

    def refuse(src, dest, *a, **k):
        raise OSError("the disk is full")
    monkeypatch.setattr(shutil, "copytree", refuse)
    with pytest.raises(OSError, match="the disk is full"):
        R.copy_folder(tmp_path / "a.resources", tmp_path / "b.resources")
    assert not (tmp_path / "b.resources").exists()
