"""Clone, fetch, pull and push in the background (git/ops.py), pinned so a changed number or flag
fails a test: how many finished operations are kept and in what order, the remote a first push
picks, a clone into a folder that's already there, how a failed run ends, and how git's stderr and
its time limit are read. A small Python program, or a fake process, stands in for git."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from fabric_desktop.git import cli, ops


class Repo:
    def __init__(self, root, remotes=("origin",), **status):
        self.root = root
        self._remotes = [{"name": r} for r in remotes]
        self._status = {"merging": False, "branch": "main", "upstream": "origin/main", "commit": "abc",
                        "remotes": self._remotes, "conflicts": []} | status

    def remotes(self):
        return self._remotes

    def status(self):
        return self._status


def wait(o, op_id, timeout=20):
    end = time.monotonic() + timeout
    while o.status(op_id)["status"] == "running":
        assert time.monotonic() < end, "the operation never finished"
        time.sleep(0.02)
    return o.status(op_id)


def git_says(monkeypatch, script, seen=None):
    def command(cwd, args):
        if seen is not None:
            seen.append(args)
        return [sys.executable, "-c", script]
    monkeypatch.setattr(cli, "command", command)


def test_fifty_finished_operations_are_kept_and_listed_newest_first():
    o = ops.Operations()
    for i in range(52):
        o._ops[f"{i:02}"] = {"id": f"{i:02}", "status": "ok", "started_at": f"2026-11-07T00:{i:02}"}
    o._trim()
    assert sorted(o._ops) == [f"{i:02}" for i in range(2, 52)]
    assert [x["id"] for x in o.list()][:3] == ["51", "50", "49"]


def test_a_message_git_gave_none_for_is_empty_text():
    assert ops._done_message("Counting objects: done") == ""


def test_an_operation_is_marked_as_one_that_talks_to_a_remote(tmp_path, monkeypatch):
    git_says(monkeypatch, "pass")
    o = ops.Operations()
    op = o._start("fetch", None, ["fetch"], cwd=tmp_path)
    assert op["remote"] is True
    wait(o, op["id"])


def test_a_failure_that_isnt_a_conflict_ends_with_after_false_and_never_reads_the_result(tmp_path, monkeypatch):
    git_says(monkeypatch, "import sys; sys.stderr.write('fatal: could not read from remote\\n'); sys.exit(1)")
    o = ops.Operations()
    after, read = [], []
    op = wait(o, o._start("fetch", None, ["fetch"], cwd=tmp_path, after=after.append,
                          result=lambda: read.append(1) or {})["id"])
    assert op["status"] == "failed" and op["result"] is None
    assert after == [False] and read == []


def test_a_clone_into_a_parent_folder_that_already_exists_starts(tmp_path, monkeypatch):
    seen = []
    git_says(monkeypatch, "pass", seen)
    o = ops.Operations()
    op = wait(o, o.clone("https://example.com/team/demo.git", str(tmp_path), "demo")["id"])
    assert op["status"] == "ok" and seen[0][:2] == ["clone", "--progress"]


def test_a_first_push_goes_to_origin_when_there_is_one_else_to_the_first_remote(tmp_path, monkeypatch):
    seen = []
    git_says(monkeypatch, "pass", seen)
    o = ops.Operations()
    wait(o, o.push(Repo(tmp_path, remotes=("upstream", "origin"), upstream=None))["id"])
    wait(o, o.push(Repo(tmp_path, remotes=("upstream", "fork"), upstream=None))["id"])
    assert [args[3] for args in seen] == ["origin", "upstream"]


class Timer:
    made: list = []

    def __init__(self, seconds, func):
        self.daemon = None
        Timer.made.append(self)

    def start(self):
        self.started_as_daemon = self.daemon

    def cancel(self):
        pass


class Stderr:
    """Only read1, as a real pipe has: what git wrote comes back in pieces."""

    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read1(self, n):
        return self.chunks.pop(0) if self.chunks else b""


def test_git_runs_with_a_daemon_time_limit_and_its_stderr_is_read_as_it_comes(tmp_path, monkeypatch):
    proc = SimpleNamespace(stdout=SimpleNamespace(read=lambda: b""), stderr=Stderr([b"remote: hel", b"lo\nfatal: no\n"]),
                           wait=lambda: 1, poll=lambda: 1, pid=1)
    monkeypatch.setattr(ops, "subprocess", SimpleNamespace(Popen=lambda *a, **kw: proc, DEVNULL=subprocess.DEVNULL,
                                                           PIPE=subprocess.PIPE))
    monkeypatch.setattr(ops, "threading", SimpleNamespace(Timer=Timer, Thread=threading.Thread, Lock=threading.Lock))
    monkeypatch.setattr(cli, "command", lambda cwd, args: ["git", *args])
    Timer.made = []
    o = ops.Operations()
    op = wait(o, o._start("fetch", None, ["fetch"], cwd=tmp_path)["id"])
    assert [t.started_as_daemon for t in Timer.made] == [True]
    assert op["status"] == "failed" and op["details"].splitlines() == ["remote: hello", "fatal: no"]


def test_fewer_finished_operations_than_the_limit_are_all_kept():
    o = ops.Operations()
    for i in range(3):
        o._ops[str(i)] = {"id": str(i), "status": "ok", "started_at": f"2026-11-07T00:0{i}"}
    o._trim()
    assert sorted(o._ops) == ["0", "1", "2"]


class Thread(threading.Thread):
    made: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.joins = []
        Thread.made.append(self)

    def join(self, timeout=None):
        self.joins.append(timeout)
        super().join(timeout)


class ReadOnly:
    """Only read, as an older pipe object has."""

    def __init__(self, data):
        self.data, self.sizes = data, []

    def read(self, n):
        self.sizes.append(n)
        chunk, self.data = self.data[:n], self.data[n:]
        return chunk


def fake_git(monkeypatch, tmp_path, stderr, stdout=b"", code=0, clock=None):
    proc = SimpleNamespace(stdout=SimpleNamespace(read=lambda: stdout), stderr=stderr, wait=lambda: code,
                           poll=lambda: code, pid=1)
    monkeypatch.setattr(ops, "subprocess", SimpleNamespace(Popen=lambda *a, **kw: proc, DEVNULL=subprocess.DEVNULL,
                                                           PIPE=subprocess.PIPE))
    monkeypatch.setattr(ops, "threading", SimpleNamespace(Timer=threading.Timer, Thread=Thread, Lock=threading.Lock))
    monkeypatch.setattr(cli, "command", lambda cwd, args: ["git", *args])
    if clock is not None:
        times = iter(clock)
        monkeypatch.setattr(ops, "time", SimpleNamespace(monotonic=lambda: next(times)))
    Thread.made = []
    o = ops.Operations()
    op = o._start("fetch", None, ["fetch"], cwd=tmp_path)
    assert len(op["id"]) == 12
    return wait(o, op["id"])


def test_every_thread_is_a_daemon_and_git_is_read_4096_bytes_at_a_time(tmp_path, monkeypatch):
    lines = b"".join(b"line %d\n" % i for i in range(45))
    stderr = ReadOnly(lines)
    op = fake_git(monkeypatch, tmp_path, stderr, stdout=b"".join(b"out %d\n" % i for i in range(25)))
    assert all(t.daemon for t in Thread.made) and len(Thread.made) == 2
    assert Thread.made[1].joins == [5]  # the stdout reader
    assert set(stderr.sizes) == {4096}
    details = op["details"].splitlines()
    assert len(details) == 40 and details[-20:] == [f"out {i}" for i in range(5, 25)]
    assert details[:20] == [f"line {i}" for i in range(25, 45)]


def test_stderr_read1_asks_for_4096_bytes(tmp_path, monkeypatch):
    sizes = []

    class Pipe(Stderr):
        def read1(self, n):
            sizes.append(n)
            return super().read1(n)

    fake_git(monkeypatch, tmp_path, Pipe([b"remote: x\n"]))
    assert sizes and set(sizes) == {4096}


def test_a_finished_run_says_how_long_it_took_to_a_tenth_of_a_second(tmp_path, monkeypatch):
    op = fake_git(monkeypatch, tmp_path, Stderr([]), clock=[100.0, 102.26])
    assert op["status"] == "ok" and op["seconds"] == 2.3


def test_a_run_that_ended_one_second_before_the_limit_counts_as_timed_out(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "NETWORK_TIMEOUT", 120)
    op = fake_git(monkeypatch, tmp_path, Stderr([]), code=128, clock=[0.0, 119.0])
    assert op["error_kind"] == "timeout" and "longer than 2 minutes" in op["message"]


def test_on_windows_taskkill_runs_quietly_with_a_15_second_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(ops, "_WINDOWS", True)
    monkeypatch.setattr(ops, "system32", lambda name: name)
    monkeypatch.setattr(ops, "subprocess", SimpleNamespace(run=lambda cmd, **kw: calls.append((cmd, kw)),
                                                           TimeoutExpired=subprocess.TimeoutExpired))
    proc = SimpleNamespace(pid=7, poll=lambda: None, kill=lambda: None)
    ops._kill(proc)
    assert calls[0][0] == ["taskkill.exe", "/F", "/T", "/PID", "7"]
    assert calls[0][1]["capture_output"] is True and calls[0][1]["timeout"] == 15


def test_a_clone_makes_a_parent_folder_two_levels_deep(tmp_path, monkeypatch):
    git_says(monkeypatch, "pass")
    o = ops.Operations()
    parent = tmp_path / "source" / "repos"
    op = wait(o, o.clone("https://example.com/team/demo.git", str(parent), "demo")["id"])
    assert op["status"] == "ok" and parent.is_dir()
