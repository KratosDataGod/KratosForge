"""Deleting a folder waits out what Windows refuses for a moment, and clears read-only files.

On a person's PC shutil.rmtree stops at a read-only file (a copy of a read-only share, a Git object), at a
file a virus scanner, the indexer or OneDrive has open (a sharing violation), and at a folder whose deleted
files are still open somewhere ("The directory is not empty", WinError 145). trees.remove handles those and
never follows a link or junction out of the folder. The read-only and junction cases run for real on the
windows-latest CI runner.
"""

from __future__ import annotations

import ast
import errno
import os
import stat
import sys
from pathlib import Path

import pytest

from fabric_desktop import trees

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"

# Files that call shutil.rmtree without ignore_errors themselves, each for a reason of its own.
OWN = {
    "trees.py": "the shared helper",
    "git/ops.py": "remove_clone clears read-only Git objects and leaves what is still in use",
    "local_writes_service.py": "a clear-up that leaves each in-use folder and moves on, rather than waiting on each",
    # Runs only in an Academy learner's process (kf_learner, academy/learner_pkg.py), which can't import
    # trees; the folder is in the exercise's own workspace.
    "academy/notebookutils_lab.py": "notebookutils.fs.rm in a learner's exercise folder",
}


def test_every_folder_delete_goes_through_trees_remove():
    bare = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel in OWN:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "rmtree"):
                continue
            if any(k.arg in ("ignore_errors", "onexc", "onerror") for k in node.keywords):
                continue  # a best-effort clean-up, or one with its own handling
            bare.append(f"{rel}:{node.lineno}")
    assert not bare, f"Use trees.remove, or add the file to OWN with its reason: {bare}"


def _tree(root: Path) -> Path:
    top = root / "Tables" / "sales"
    (top / "_delta_log").mkdir(parents=True)
    (top / "_delta_log" / "00000000000000000000.json").write_text("{}")
    (top / "part-00000.parquet").write_bytes(b"PAR1")
    return top


def test_removes_a_folder_holding_read_only_files(tmp_path):
    top = _tree(tmp_path)
    for f in (top / "part-00000.parquet", top / "_delta_log" / "00000000000000000000.json"):
        os.chmod(f, stat.S_IREAD)   # Windows: the read-only attribute, which os.unlink refuses
    if sys.platform == "win32":   # a read-only folder; on Linux that would stop deleting what is in it
        os.chmod(top / "_delta_log", stat.S_IREAD | stat.S_IEXEC)
    trees.remove(top)
    assert not top.exists()
    assert (tmp_path / "Tables").is_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")
def test_a_junction_inside_is_removed_without_touching_its_target(tmp_path):
    import _winapi

    outside = tmp_path / "outside"
    outside.mkdir()
    keep = outside / "keep.txt"
    keep.write_text("not ours")
    os.chmod(keep, stat.S_IREAD)
    top = _tree(tmp_path)
    _winapi.CreateJunction(str(outside), str(top / "link"))
    trees.remove(top)
    assert not top.exists()
    assert keep.read_text() == "not ours"
    assert not keep.stat().st_mode & stat.S_IWRITE, "the target's read-only file was made writable through the junction"


def test_a_symbolic_link_inside_is_removed_without_touching_its_target(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    keep = outside / "keep.txt"
    keep.write_text("not ours")
    os.chmod(keep, stat.S_IREAD)
    top = _tree(tmp_path)
    try:
        os.symlink(outside, top / "link", target_is_directory=True)
    except OSError:
        pytest.skip("this account can't make symbolic links")
    trees.remove(top)
    assert not top.exists()
    assert keep.read_text() == "not ours"
    assert not keep.stat().st_mode & stat.S_IWRITE


def test_a_file_another_program_holds_for_a_moment_is_waited_out(tmp_path, monkeypatch):
    target = tmp_path / "f"
    target.write_text("x")
    calls, waits = [], []

    def unlink(p):   # os.unlink while a scanner has the file open for its first two tries
        calls.append(p)
        if len(calls) <= 2:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        os.unlink(p)

    monkeypatch.setattr(trees.time, "sleep", waits.append)
    trees._clear_and_retry(unlink, str(target), PermissionError(13, "busy"))
    assert not target.exists()
    assert waits == [0.05, 0.1]


def test_a_folder_not_empty_while_deleted_files_are_still_open_is_waited_out(tmp_path, monkeypatch):
    folder = tmp_path / "d"
    folder.mkdir()
    tries, waits = [], []
    real_rmdir = os.rmdir

    def rmdir(p):   # os.rmdir while a deleted file inside is still open somewhere, for its first try
        tries.append(p)
        if len(tries) == 1:
            exc = OSError(errno.ENOTEMPTY, "The directory is not empty")
            exc.winerror = 145
            raise exc
        real_rmdir(p)

    monkeypatch.setattr(trees.time, "sleep", waits.append)
    first = OSError(errno.ENOTEMPTY, "The directory is not empty")
    trees._clear_and_retry(rmdir, str(folder), first)
    assert not folder.exists()
    assert waits == [0.05]


def test_gives_up_with_the_error_after_about_one_and_a_half_seconds(tmp_path, monkeypatch):
    target = tmp_path / "f"
    target.write_text("x")
    waits = []

    def unlink(p):   # a file another program keeps open
        raise PermissionError(13, "busy")

    monkeypatch.setattr(trees.time, "sleep", waits.append)
    with pytest.raises(PermissionError):
        trees._clear_and_retry(unlink, str(target), PermissionError(13, "busy"))
    assert len(waits) == 5 and 1.5 <= sum(waits) <= 1.6


def test_other_failures_are_raised_at_once(tmp_path, monkeypatch):
    waits = []
    monkeypatch.setattr(trees.time, "sleep", waits.append)
    with pytest.raises(OSError, match="listing"):
        trees._clear_and_retry(os.scandir, str(tmp_path), OSError(errno.EIO, "listing"))
    with pytest.raises(OSError, match="disk"):
        trees._clear_and_retry(os.unlink, str(tmp_path / "x"), OSError(errno.EIO, "disk"))
    assert waits == []


def test_a_file_gone_in_the_meantime_counts_as_removed(tmp_path):
    trees._clear_and_retry(os.unlink, str(tmp_path / "gone"), PermissionError(13, "busy"))


def test_a_missing_folder_is_an_error_as_rmtree_makes_it(tmp_path):
    with pytest.raises(FileNotFoundError):
        trees.remove(tmp_path / "nope")
