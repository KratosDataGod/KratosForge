"""dbutils_local.py: dbutils.fs.rm when notebookutils refuses a folder (recursive, or in the lakehouse), and
quiet_part on an object that isn't this PC's dbutils."""

from __future__ import annotations

import types

import pytest

from fabric_desktop import dbutils_local


class FolderFs:
    """notebookutils.fs that has every path and refuses to remove any as a folder."""

    def __init__(self):
        self.removed = []

    def exists(self, path):
        return True

    def mkdirs(self, path):
        return True

    def rm(self, path, recurse):
        self.removed.append((path, recurse))
        raise IsADirectoryError(f"{path} is a folder")


@pytest.mark.parametrize("path, recurse", [
    ("Files/raw", False),            # the lakehouse: no local folder to remove only while empty
    ("file:/tmp/kf-cov4-x", True),   # recursive: notebookutils' answer stands
])
def test_a_folder_notebookutils_refuses_is_raised_as_it_is(path, recurse):
    nbu = FolderFs()
    fs = dbutils_local._make_fs(nbu)
    with pytest.raises(IsADirectoryError, match="is a folder$"):
        fs.rm(path, recurse)
    assert nbu.removed == [(path, recurse)]


def test_an_empty_local_folder_goes_without_recurse_and_a_full_one_is_refused(tmp_path):
    fs = dbutils_local._make_fs(FolderFs())
    empty = tmp_path / "empty"
    empty.mkdir()
    assert fs.rm(f"file:{empty}") is True and not empty.exists()
    full = tmp_path / "full"
    full.mkdir()
    (full / "a.txt").write_text("a")
    with pytest.raises(IsADirectoryError, match="isn't empty. Pass recurse=True"):
        fs.rm(f"file:{full}")
    assert (full / "a.txt").exists()


def test_quiet_part_reads_any_object_and_none_for_a_missing_part():
    other = types.SimpleNamespace(fs="the fs")
    assert dbutils_local.quiet_part(other, "fs") == "the fs"
    assert dbutils_local.quiet_part(other, "widgets") is None


def test_quiet_part_on_this_pcs_dbutils_prints_no_note(capsys):
    dbutils = dbutils_local._Dbutils(fs="the fs")
    dbutils._kf_arm()
    assert dbutils_local.quiet_part(dbutils, "fs") == "the fs"
    assert dbutils_local.quiet_part(dbutils, "secrets") is None
    assert capsys.readouterr().out == ""
    assert dbutils.fs == "the fs"
    assert dbutils_local.DATABRICKS_ANSI_NOTE in capsys.readouterr().out
    assert dbutils.fs == "the fs"
    assert capsys.readouterr().out == ""  # once per session
