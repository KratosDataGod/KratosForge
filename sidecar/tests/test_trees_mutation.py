"""Kill tests for trees.py (docs/quality/mutation-testing.md, batch 110).

WinError 145 alone is a busy folder, a link is never chmod-ed (so its target keeps its read-only
attribute), a file's read-only attribute is cleared with nothing else added, what isn't an OSError is
raised at once, and a different failure on a retry is raised at once.
"""

from __future__ import annotations

import errno
import os
import stat
import sys

import pytest

from fabric_desktop import trees


def test_winerror_145_alone_is_a_busy_folder():
    exc = OSError(errno.EIO, "The directory is not empty")
    exc.winerror = 145
    assert trees._busy(exc)
    other = OSError(errno.EIO, "something else")
    other.winerror = 146
    assert not trees._busy(other)


def test_what_is_not_an_os_error_is_raised_at_once(tmp_path, monkeypatch):
    target = tmp_path / "f"
    target.write_text("x")
    waits = []
    monkeypatch.setattr(trees.time, "sleep", waits.append)
    with pytest.raises(ValueError, match="odd"):
        trees._clear_and_retry(os.unlink, str(target), ValueError("odd"))
    assert target.exists() and waits == []


def test_a_different_failure_on_a_retry_is_raised_at_once(tmp_path, monkeypatch):
    target = tmp_path / "f"
    target.write_text("x")
    waits = []
    monkeypatch.setattr(trees.time, "sleep", waits.append)

    def unlink(p):
        raise OSError(errno.EIO, "disk")

    with pytest.raises(OSError, match="disk") as got:
        trees._clear_and_retry(unlink, str(target), PermissionError(13, "busy"))
    assert got.value.errno == errno.EIO and waits == []


def test_a_read_only_file_is_made_writable_with_nothing_else_added(tmp_path):
    target = tmp_path / "f"
    target.write_text("x")
    os.chmod(target, stat.S_IREAD)

    def unlink(p):   # succeeds without deleting, so the file is left to look at
        return None

    unlink.__name__ = "unlink"
    trees._clear_and_retry(unlink, str(target), PermissionError(13, "read-only"))
    mode = target.stat().st_mode
    assert mode & stat.S_IWRITE
    if sys.platform != "win32":   # Windows keeps only the read-only attribute
        assert stat.S_IMODE(mode) == stat.S_IWRITE | stat.S_IREAD


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no execute bits")
def test_a_folder_also_gets_its_execute_bit(tmp_path):
    folder = tmp_path / "d"
    folder.mkdir()
    os.chmod(folder, stat.S_IREAD)

    def rmdir(p):
        return None

    rmdir.__name__ = "rmdir"
    try:
        trees._clear_and_retry(rmdir, str(folder), PermissionError(13, "read-only"))
        assert stat.S_IMODE(folder.stat().st_mode) == stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC
    finally:
        os.chmod(folder, stat.S_IRWXU)


def test_a_link_is_retried_without_chmod_so_its_target_stays_read_only(tmp_path):
    keep = tmp_path / "keep.txt"
    keep.write_text("not ours")
    os.chmod(keep, stat.S_IREAD)
    link = tmp_path / "link"
    try:
        os.symlink(keep, link)
    except OSError:
        pytest.skip("this account can't make symbolic links")
    try:
        trees._clear_and_retry(os.unlink, str(link), PermissionError(13, "busy"))
        assert not os.path.lexists(link)
        assert not keep.stat().st_mode & stat.S_IWRITE, "chmod followed the link to its target"
    finally:
        os.chmod(keep, stat.S_IWRITE | stat.S_IREAD)
