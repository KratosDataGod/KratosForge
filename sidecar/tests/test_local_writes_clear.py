"""Settings > Advanced > OneLake writes > Delete left-overs (local_writes_service.py): the edges of what
it deletes and keeps. The main paths are in test_retention.py."""

import errno
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

from fabric_desktop import local_writes_service as lw


class Sessions:
    def __init__(self, kernels=()):
        self._kernels = list(kernels)

    def any_running(self):
        return False

    def kernels(self):
        return self._kernels


def _svc(root, kernels=()):
    class Svc(lw.LocalWritesMixin):
        pass
    svc = Svc()
    svc.root, svc.sessions = root, Sessions(kernels)
    return svc


@pytest.fixture
def temp(tmp_path, monkeypatch):
    folder = tmp_path / "temp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(folder))
    return folder


# --------------------------------------------------------------------------- a file that goes while it looks

def test_a_spill_that_finishes_uploading_mid_clear_is_not_an_error(tmp_path, temp, monkeypatch):
    """The upload deletes its spill between the listing and the delete: the rest is still cleared."""
    svc = _svc(tmp_path / "app")
    spills = svc.root / "staging" / "_writes"
    spills.mkdir(parents=True)
    gone = spills / "write-1.arrow"
    gone.write_bytes(b"y" * 10)
    (spills / "write-2.arrow").write_bytes(b"y" * 20)
    real_stat = pathlib.Path.stat

    def stat(self, *a, **kw):
        if self == gone and os.path.exists(self):
            self.unlink()  # its upload finished just now
        return real_stat(self, *a, **kw)
    monkeypatch.setattr(pathlib.Path, "stat", stat)
    assert svc.local_writes_clear() == {"removed": 2, "bytes": 20, "in_use": []}
    assert list(spills.iterdir()) == []


def test_a_file_that_goes_inside_a_folder_being_cleared_counts_as_nothing(tmp_path, temp, monkeypatch):
    job = tmp_path / "app" / "staging" / "old_job"
    job.mkdir(parents=True)
    (job / "a.parquet").write_bytes(b"z" * 7)
    gone = job / "b.parquet"
    gone.write_bytes(b"z" * 5)
    real_stat = pathlib.Path.stat

    def stat(self, *a, **kw):
        if self == gone and os.path.exists(self):
            self.unlink()
        return real_stat(self, *a, **kw)
    monkeypatch.setattr(pathlib.Path, "stat", stat)
    assert lw._remove(job) == 7 and not job.exists()


def test_what_is_already_gone_frees_nothing(tmp_path):
    assert lw._remove(tmp_path / "never-there.arrow") == 0
    assert lw._remove(tmp_path / "never-there") == 0


def test_a_file_or_folder_in_use_is_left_and_reported(tmp_path, monkeypatch):
    held = tmp_path / "write-1.arrow"
    held.write_bytes(b"y")
    folder = tmp_path / "tpch_x"
    folder.mkdir()

    def refuse(*a, **kw):
        raise PermissionError(32, "The process cannot access the file because it is being used by another process")
    monkeypatch.setattr(pathlib.Path, "unlink", refuse)
    monkeypatch.setattr(lw.shutil, "rmtree", refuse)
    assert lw._remove(held) is None and held.exists()
    assert lw._remove(folder) is None and folder.exists()


def test_what_is_there_skips_a_file_that_goes_while_counting(tmp_path, monkeypatch):
    folder = tmp_path / "staging"
    folder.mkdir()
    (folder / "a").write_bytes(b"123")
    gone = folder / "b"
    gone.write_bytes(b"12345")
    real_stat = pathlib.Path.stat

    def stat(self, *a, **kw):
        if self == gone:
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(self))
        return real_stat(self, *a, **kw)
    monkeypatch.setattr(pathlib.Path, "stat", stat)
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: True)  # it was there when listed
    out = lw._usage(folder, 0)
    assert (out["files"], out["bytes"]) == (1, 3) and out["newest"].endswith("Z")


# --------------------------------------------------------------------------- whose a left-over is

@pytest.mark.parametrize("name, owner", [
    ("write-1234-abc.arrow", 1234),
    ("write-1234.arrow", None),  # from a build before spills were named by process
    ("write-x-abc.arrow", None),
    ("schema.json", None),
])
def test_a_spill_names_its_process(tmp_path, name, owner):
    assert lw._owner(tmp_path / name) == owner


def test_a_tpch_folder_names_its_process_in_its_marker(tmp_path):
    folder = tmp_path / "tpch_a"
    folder.mkdir()
    assert lw._owner(folder) is None  # no marker
    (folder / lw.MARKER).write_text(" 4321\n")
    assert lw._owner(folder) == 4321
    (folder / lw.MARKER).write_text("not a pid")
    assert lw._owner(folder) is None


def test_no_temp_folder_lists_no_tpch_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "missing"))
    assert lw._tpch_folders() == []


def test_a_tpch_file_with_the_prefix_is_not_a_folder_to_delete(temp):
    (temp / "tpch_notes.txt").write_text("someone else's")
    marked = temp / "tpch_a"
    marked.mkdir()
    (marked / lw.MARKER).write_text("1")
    assert lw._tpch_folders() == [marked]


# --------------------------------------------------------------------------- is a process running

def test_this_process_and_a_live_child_run_and_an_ended_one_doesnt():
    assert lw.pid_running(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not lw.pid_running(child.pid)


@pytest.mark.skipif(sys.platform == "win32", reason="os.kill(pid, 0) is the POSIX check")
def test_a_process_of_another_user_counts_as_running(monkeypatch):
    def kill(pid, sig):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(lw.os, "kill", kill)
    assert lw.pid_running(1)


# --------------------------------------------------------------------------- this engine's idle notebooks

def test_an_idle_notebooks_left_overs_go_though_its_process_still_runs(tmp_path, temp):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        kernel = type("K", (), {"_proc": child})()
        no_proc = type("K", (), {})()  # a kernel not started yet
        svc = _svc(tmp_path / "app", kernels=[kernel, no_proc])
        assert svc._idle_kernel_pids() == {child.pid}
        spills = svc.root / "staging" / "_writes"
        spills.mkdir(parents=True)
        spill = spills / f"write-{child.pid}-abc.arrow"
        spill.write_bytes(b"y" * 4)
        tpch = temp / "tpch_k"
        tpch.mkdir()
        (tpch / lw.MARKER).write_text(str(child.pid))
        assert svc.local_writes_clear() == {"removed": 2, "bytes": 4 + len(str(child.pid)), "in_use": []}
        assert not spill.exists() and not tpch.exists()
    finally:
        child.kill()
        child.wait()


def test_nothing_to_clear_clears_nothing(tmp_path, temp):
    assert _svc(tmp_path / "app").local_writes_clear() == {"removed": 0, "bytes": 0, "in_use": []}
