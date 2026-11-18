"""trees.remove's retry handler on the paths a person's PC rarely takes: a chmod Windows refuses, a
file gone between tries, a retry failing some other way, and a non-OS error handed to it."""

from __future__ import annotations

import errno
import os

import pytest

from fabric_desktop import trees


def test_only_os_refusals_count_as_busy():
    assert trees._busy(PermissionError(13, "busy"))
    assert trees._busy(OSError(errno.ENOTEMPTY, "The directory is not empty"))
    assert not trees._busy(OSError(errno.EIO, "disk"))
    assert not trees._busy(ValueError("not an OS error"))


def test_a_non_os_error_is_raised_as_it_was(tmp_path):
    original = ValueError("not an OS error")
    with pytest.raises(ValueError) as caught:
        trees._clear_and_retry(os.unlink, str(tmp_path / "f"), original)
    assert caught.value is original


def test_a_chmod_windows_refuses_still_waits_and_retries(tmp_path, monkeypatch):
    target = tmp_path / "f"
    target.write_text("x")
    chmods = []

    def refuse_chmod(path, mode):
        chmods.append(path)
        raise PermissionError(13, "still open elsewhere")

    monkeypatch.setattr(trees.os, "chmod", refuse_chmod)
    monkeypatch.setattr(trees.time, "sleep", lambda s: None)
    trees._clear_and_retry(os.unlink, str(target), PermissionError(13, "busy"))
    assert chmods == [str(target)]
    assert not target.exists()


def test_a_file_gone_between_tries_counts_as_removed(tmp_path, monkeypatch):
    target = tmp_path / "f"
    target.write_text("x")
    waits, tries = [], []

    def unlink(p):  # busy on the first try; by the second something else has deleted it
        tries.append(p)
        if len(tries) == 1:
            raise PermissionError(13, "busy")
        os.unlink(p)
        raise FileNotFoundError(errno.ENOENT, "gone", p)

    monkeypatch.setattr(trees.time, "sleep", waits.append)
    trees._clear_and_retry(unlink, str(target), PermissionError(13, "busy"))
    assert len(tries) == 2 and waits == [0.05]
    assert not target.exists()


def test_a_retry_failing_some_other_way_is_raised_at_once(tmp_path, monkeypatch):
    target = tmp_path / "f"
    target.write_text("x")
    waits = []
    disk = OSError(errno.EIO, "disk")

    def unlink(p):
        raise disk

    monkeypatch.setattr(trees.time, "sleep", waits.append)
    with pytest.raises(OSError) as caught:
        trees._clear_and_retry(unlink, str(target), PermissionError(13, "busy"))
    assert caught.value is disk
    assert waits == []
    assert target.exists()
