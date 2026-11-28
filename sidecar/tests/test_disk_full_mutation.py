"""Mutation-testing pins for disk_full (docs/quality/mutation-testing.md, batch 5)."""

from __future__ import annotations

import errno
import os

import pytest

from fabric_desktop import disk_full as D


def _win(code):
    e = OSError("refused")
    e.winerror = code
    return e


@pytest.mark.parametrize("code", [39, 112])
def test_both_windows_disk_full_codes_count(code):
    assert D.is_disk_full(_win(code))


def test_another_windows_code_is_not_a_full_disk():
    assert D.is_disk_full(_win(40)) is False  # a bool, as annotated, not just something falsy
    assert not D.is_disk_full(OSError(errno.EIO, "io"))


def test_enospc_and_each_text_count():
    assert D.is_disk_full(OSError(errno.ENOSPC, "x"))
    for text in D._TEXTS:
        assert D.is_disk_full(RuntimeError(f"Write failed: {text.upper()}"))
    assert not D.is_disk_full(RuntimeError("something else"))


def _chained(n, last):
    """An error raised from n - 1 others, the deepest being ``last``."""
    exc = last
    for i in range(n - 1):
        outer = RuntimeError(f"wrapper {i}")
        outer.__cause__ = exc
        exc = outer
    return exc


def test_the_chain_is_followed_through_causes_and_contexts_up_to_twenty():
    assert D.is_disk_full(_chained(20, OSError(errno.ENOSPC, "x")))
    assert not D.is_disk_full(_chained(21, OSError(errno.ENOSPC, "x")))
    outer = RuntimeError("outer")
    outer.__context__ = OSError(errno.ENOSPC, "x")
    assert D.is_disk_full(outer)


def test_a_cycle_in_the_chain_ends():
    a, b = RuntimeError("a"), RuntimeError("b")
    a.__cause__, b.__cause__ = b, a
    assert D._chain(a) == [a, b]


def test_spill_is_found_in_any_case():
    assert D.is_spill_full("Out of Memory: FAILED TO OFFLOAD DATA BLOCK of size 256KiB")
    assert not D.is_spill_full("out of memory")


@pytest.mark.parametrize("n, text", [(0, "0 bytes"), (1023, "1023 bytes"), (1024, "1.0 KB"), (1536, "1.5 KB"),
                                     (1024 ** 2, "1.0 MB"), (5 * 1024 ** 3, "5.0 GB"), (1024 ** 4, "1024.0 GB")])
def test_sizes(n, text):
    assert D._size(n) == text


def test_free_space_of_nothing_is_empty():
    assert D.free_space(None) == "" and D.free_space("") == ""


def test_free_space_walks_up_to_a_folder_it_can_read(tmp_path, monkeypatch):
    asked = []

    def usage(p):
        asked.append(p)
        if p != tmp_path:
            raise OSError("no such folder")
        return type("U", (), {"free": 2048})()
    monkeypatch.setattr(D.shutil, "disk_usage", usage)
    out = D.free_space(tmp_path / "new" / "table")
    assert asked[:3] == [tmp_path / "new" / "table", tmp_path / "new", tmp_path]
    assert out == ("2.0 KB free" if not tmp_path.drive else f"{tmp_path.drive}\\ has 2.0 KB free")


def test_free_space_that_cant_be_read_anywhere_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(D.shutil, "disk_usage", lambda p: (_ for _ in ()).throw(OSError("no")))
    assert D.free_space(tmp_path) == ""


@pytest.mark.skipif(os.name != "nt", reason="drive letters are Windows-only")
def test_a_windows_drive_is_named_by_its_letter():
    assert D.free_space("C:\\").startswith("C:\\ has ")


def test_messages_with_and_without_free_space(monkeypatch):
    monkeypatch.setattr(D, "free_space", lambda p: "3.0 GB free" if p else "")
    assert D.message("x", "The table was not changed.") == \
        "The disk is full (3.0 GB free). The table was not changed. Free some space and run it again."
    assert D.message(None, "") == "The disk is full. Free some space and run it again."
    assert D.spill_message("t") == (
        "Stopped: the query ran out of room for its temp files, as the temp folder's drive is full (3.0 GB free). "
        "Nothing was saved. Free some space, or narrow the query, and run it again.")
    assert D.spill_message(None).startswith("Stopped: the query ran out of room for its temp files, as the temp "
                                            "folder's drive is full. Nothing")


@pytest.mark.parametrize("exc", [OSError(errno.EACCES, "x"), OSError(errno.EPERM, "x"), OSError(errno.EROFS, "x"),
                                 _win(5), _win(19)])
def test_each_refusal_is_unwritable(exc):
    assert D.is_unwritable(exc)
    assert D.is_unwritable(_chained(3, exc))


def test_other_failures_are_not_unwritable():
    assert D.is_unwritable(_win(6)) is False
    assert not D.is_unwritable(OSError(errno.EIO, "x"))
    assert not D.is_unwritable(RuntimeError("access denied"))


def test_inside(tmp_path):
    assert D._inside(str(tmp_path / "a" / "b.json"), tmp_path)
    assert D._inside(tmp_path / "a", tmp_path)
    assert D._inside(str(tmp_path.parent / "elsewhere"), tmp_path) is False
    assert D._inside("", tmp_path) is False
    assert not D._inside(None, tmp_path)
    assert not D._inside(5, tmp_path)


def test_inside_a_path_that_cant_be_resolved_is_not_inside(tmp_path, monkeypatch):
    def boom(self, *a, **k):
        raise RuntimeError("symlink loop")
    monkeypatch.setattr(D.Path, "resolve", boom)
    assert D._inside(str(tmp_path / "x"), tmp_path) is False


def test_a_full_disk_in_the_data_folder_names_the_files_drive(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(D, "free_space", lambda p: asked.append(p) or "1.0 MB free")
    exc = OSError(errno.ENOSPC, "No space left on device", str(tmp_path / "settings.json"))
    assert D.data_folder_message(tmp_path, exc) == (
        "The disk is full (1.0 MB free), so this wasn't saved. Free some space, then try again. "
        "On a shared or virtual desktop, ask IT to make your profile disk bigger.")
    assert asked == [str(tmp_path / "settings.json")]


def test_a_full_disk_with_no_file_name_names_the_folders_drive(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(D, "free_space", lambda p: asked.append(p) or "")
    out = D.data_folder_message(tmp_path, OSError(errno.ENOSPC, "x"), outcome="so the key wasn't kept",
                                then="add it again")
    assert out == ("The disk is full, so the key wasn't kept. Free some space, then add it again. "
                   "On a shared or virtual desktop, ask IT to make your profile disk bigger.")
    assert asked == [tmp_path]


def test_a_full_disk_already_reported_is_not_said_twice(tmp_path):
    inner = OSError(errno.ENOSPC, "x")
    outer = RuntimeError("The disk is full (2 GB free). The table was not changed.")
    outer.__cause__ = inner
    assert D.data_folder_message(tmp_path, outer) is None


def test_a_refusal_inside_the_data_folder_is_explained(tmp_path):
    exc = OSError(errno.EACCES, "denied", str(tmp_path / "keys.json"))
    out = D.data_folder_message(tmp_path, _chained(2, exc))
    assert out == (f"Windows won't let KratosForge write to its data folder ({tmp_path}), so this wasn't saved. "
                   "On a shared or virtual desktop the profile disk may be attached read-only; sign out and in "
                   "again, or ask IT to check it.")


def test_a_refusal_elsewhere_or_another_failure_is_not_the_data_folders(tmp_path):
    elsewhere = OSError(errno.EACCES, "denied", str(tmp_path.parent / "export.csv"))
    assert D.data_folder_message(tmp_path / "data", elsewhere) is None
    assert D.data_folder_message(tmp_path, OSError(errno.EACCES, "denied")) is None
    assert D.data_folder_message(tmp_path, RuntimeError("boom")) is None

