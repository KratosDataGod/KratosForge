"""durable.py: behavior that mutation testing (docs/quality/mutation-testing.md, batch 2) showed no test
pinned. How _sync opens and flushes a file or a folder on Windows and on POSIX (the flags, which entries
get an fsync, a folder skipped on Windows), and sync_commits answering True or False."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from fabric_desktop import durable

BINARY = 1 << 30  # stands in for Windows' O_BINARY; taken out again before the real open


class FakeOS:
    """The real os module under another name (``nt`` or ``posix``), with Windows' O_BINARY or
    POSIX's O_NONBLOCK, recording each open's flags and each fsync."""

    def __init__(self, name: str):
        self.name = name
        self.opened: list[int] = []
        self.synced: list[int] = []
        if name == "nt":
            self.O_BINARY = BINARY
        else:
            self.O_NONBLOCK = os.O_NONBLOCK

    def __getattr__(self, attr):
        if attr in ("O_BINARY", "O_NONBLOCK"):
            raise AttributeError(attr)
        return getattr(os, attr)

    def open(self, path, flags):
        self.opened.append(flags)
        return os.open(path, (flags & ~BINARY) | getattr(os, "O_NONBLOCK", 0))

    def fsync(self, fd):
        self.synced.append(os.fstat(fd).st_mode)


posix_only = pytest.mark.skipif(os.name == "nt", reason="opens a folder and uses O_NONBLOCK, which Windows can't")


@pytest.fixture
def fake(monkeypatch):
    def make(name: str) -> FakeOS:
        f = FakeOS(name)
        monkeypatch.setattr(durable, "os", f)
        return f
    return make


def test_a_file_on_windows_opens_for_writing_in_binary_mode_and_is_flushed(tmp_path, fake):
    f = fake("nt")
    (tmp_path / "a").write_bytes(b"x")
    durable._sync(tmp_path / "a")
    assert f.opened == [os.O_RDWR | BINARY]  # FlushFileBuffers needs a handle open for writing
    assert len(f.synced) == 1 and stat.S_ISREG(f.synced[0])


def test_a_folder_on_windows_is_left_to_ntfs(tmp_path, fake):
    f = fake("nt")
    durable._sync(tmp_path, folder=True)
    assert f.opened == [] and f.synced == []


@posix_only
def test_a_file_on_posix_opens_read_only_without_blocking_and_is_flushed(tmp_path, fake):
    f = fake("posix")
    (tmp_path / "a").write_bytes(b"x")
    durable._sync(tmp_path / "a")
    assert f.opened == [os.O_RDONLY | os.O_NONBLOCK]
    assert len(f.synced) == 1 and stat.S_ISREG(f.synced[0])


@posix_only
def test_a_folder_on_posix_is_opened_and_flushed(tmp_path, fake):
    f = fake("posix")
    durable._sync(tmp_path, folder=True)
    assert f.opened == [os.O_RDONLY | os.O_NONBLOCK]
    assert len(f.synced) == 1 and stat.S_ISDIR(f.synced[0])


@posix_only
def test_a_folder_named_as_a_file_isnt_flushed(tmp_path, fake):
    f = fake("posix")
    durable._sync(tmp_path)  # folder=False: only a regular file is flushed
    assert len(f.opened) == 1 and f.synced == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes are POSIX only")
def test_a_pipe_is_opened_but_never_flushed(tmp_path, fake):
    os.mkfifo(tmp_path / "p")
    f = fake("posix")
    durable._sync(tmp_path / "p")
    durable._sync(tmp_path / "p", folder=True)
    assert len(f.opened) == 2 and f.synced == []


def _table(root: Path) -> str:
    (root / "_delta_log").mkdir(parents=True)
    (root / "part-0.parquet").write_bytes(b"x")
    (root / "_delta_log" / f"{0:020d}.json").write_text(json.dumps({"add": {"path": "part-0.parquet"}}))
    return str(root)


def test_sync_commits_says_whether_every_commit_was_flushed(tmp_path, caplog):
    uri = _table(tmp_path / "t")
    assert durable.sync_commits(uri, 0, 0) is True
    assert durable.sync_commits(uri, 0, 1) is False  # commit 1 isn't there
    assert "Couldn't flush commits 0-1" in caplog.text
