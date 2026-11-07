"""Clone, fetch, pull and push in the background (git/ops.py): the refusals before Git starts, and how a
run ends. The run itself is a small Python program standing in for git, so these tests need no network
and no repository. What it prints to stderr is what git would."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time

import pytest

from fabric_desktop.git import cli, ops
from fabric_desktop.git.cli import GitError


class Repo:
    """A Repository's status and remotes, as the operations read them."""

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


def started(o, op_id, timeout=20):
    """Wait until the stand-in for git is running, so a cancel has a process to stop."""
    end = time.monotonic() + timeout
    while op_id not in o._procs:
        assert time.monotonic() < end, "git never started"
        time.sleep(0.01)


def git_says(monkeypatch, script):
    """Every operation runs ``script`` (Python) in place of git."""
    monkeypatch.setattr(cli, "command", lambda cwd, args: [sys.executable, "-c", script])


# --------------------------------------------------------------------------- refused before Git starts

def test_a_clone_into_a_file_or_with_a_bad_name_is_refused(tmp_path):
    o = ops.Operations()
    (tmp_path / "afile").write_text("x")
    with pytest.raises(GitError, match="is a file, not a folder"):
        o.clone("https://example.com/r.git", str(tmp_path / "afile"))
    for bad in ("..", "a/b", "x" * 101):
        with pytest.raises(GitError, match="A folder name is letters"):
            o.clone("https://example.com/r.git", str(tmp_path), name=bad)


@pytest.mark.parametrize("status, mode, message", [
    ({"merging": True}, "ff-only", "A merge is in progress"),
    ({"branch": None}, "ff-only", "not on a branch"),
    ({"upstream": None}, "ff-only", "isn't linked to a remote branch"),
    ({}, "rebase", "fast-forwards or merges"),
])
def test_a_pull_that_cant_go_ahead_says_why(tmp_path, status, mode, message):
    with pytest.raises(GitError, match=message):
        ops.Operations().pull(Repo(tmp_path, **status), mode)


@pytest.mark.parametrize("repo_kw, remote, message", [
    ({"branch": None}, None, "Create a branch to push"),
    ({"commit": None}, None, "Nothing is committed yet"),
    ({"remotes": ()}, None, "no remote to push to"),
    ({}, "upstream", "There's no remote called upstream"),
])
def test_a_push_that_cant_go_ahead_says_why(tmp_path, repo_kw, remote, message):
    remotes = repo_kw.pop("remotes", ("origin",))
    repo = Repo(tmp_path, remotes=remotes, **repo_kw)
    repo._status["remotes"] = repo._remotes
    with pytest.raises(GitError, match=message):
        ops.Operations().push(repo, remote)


def test_a_fetch_from_a_remote_that_isnt_there_is_refused(tmp_path):
    with pytest.raises(GitError, match="There's no remote called fork"):
        ops.Operations().fetch(Repo(tmp_path), "fork")


def test_a_second_operation_in_the_same_repository_waits_for_the_first(tmp_path, monkeypatch):
    git_says(monkeypatch, "import time; time.sleep(30)")
    o = ops.Operations()
    repo = Repo(tmp_path)
    first = o.fetch(repo)
    started(o, first["id"])
    try:
        with pytest.raises(GitError, match="Another Git operation is still running"):
            o.fetch(repo)
        assert o.running(str(tmp_path))["id"] == first["id"]
    finally:
        assert o.cancel(first["id"])["cancel_requested"]
    assert wait(o, first["id"])["status"] == "cancelled"


# --------------------------------------------------------------------------- how a run ends

def test_progress_is_read_and_the_last_lines_are_kept(tmp_path, monkeypatch):
    git_says(monkeypatch, "import sys; sys.stderr.write('Receiving objects:  50% (1/2)\\r\\n\\nremote: done\\nlast words'); "
                          "print('Already up to date')")
    o = ops.Operations()
    op = wait(o, o.fetch(Repo(tmp_path))["id"])
    assert op["status"] == "ok" and op["percent"] == 100 and op["message"] == "Already up to date."
    assert op["details"].splitlines() == ["remote: done", "last words", "Already up to date"]
    assert [x["id"] for x in o.list(str(tmp_path))] == [op["id"]] and o.list("elsewhere") == []


def test_a_result_that_cant_be_read_is_a_note_not_a_failure(tmp_path, monkeypatch):
    git_says(monkeypatch, "pass")
    o = ops.Operations()
    repo = Repo(tmp_path)
    repo.status = lambda: (_ for _ in ()).throw(GitError("status went away"))  # after the push
    op = o._start("push", None, ["push"], cwd=tmp_path, target=str(tmp_path),
                  result=repo.status)
    assert wait(o, op["id"])["result"] == {"note": "status went away"}


def test_a_conflict_brings_back_the_result_when_it_can(tmp_path, monkeypatch):
    git_says(monkeypatch, "import sys; sys.stderr.write('CONFLICT (content): Merge conflict in a.py\\n'); sys.exit(1)")
    o = ops.Operations()
    good = wait(o, o._start("pull", None, ["pull"], cwd=tmp_path, result=lambda: {"conflicts": ["a.py"]})["id"])
    assert good["error_kind"] == "conflict" and good["result"] == {"conflicts": ["a.py"]}

    def broken():
        raise GitError("gone")
    bad = wait(o, o._start("pull", None, ["pull"], cwd=tmp_path, result=broken)["id"])
    assert bad["error_kind"] == "conflict" and bad["result"] is None


def test_a_run_past_the_network_limit_is_reported_as_timed_out(tmp_path, monkeypatch):
    git_says(monkeypatch, "import sys; sys.exit(128)")
    monkeypatch.setattr(cli, "NETWORK_TIMEOUT", 1)  # every run counts as having taken the whole limit
    o = ops.Operations()
    op = wait(o, o._start("fetch", None, ["fetch"], cwd=tmp_path)["id"])
    assert op["status"] == "failed" and op["error_kind"] == "timeout" and "was stopped" in op["message"]


def test_git_that_wont_start_is_reported(tmp_path, monkeypatch):
    def popen(*a, **kw):
        raise FileNotFoundError(2, "No such file", "git")
    monkeypatch.setattr(ops.subprocess, "Popen", popen)
    o = ops.Operations()
    op = wait(o, o._start("fetch", None, ["fetch"], cwd=tmp_path)["id"])
    assert op["error_kind"] == "no_git" and op["message"].startswith("Git couldn't start")


def test_a_cancelled_clone_removes_only_the_folder_it_made(tmp_path, monkeypatch):
    git_says(monkeypatch, "import time; time.sleep(30)")
    o = ops.Operations()
    op = o.clone("https://example.com/team/sales.git", str(tmp_path))
    dest = tmp_path / "sales"
    dest.mkdir(exist_ok=True)
    (dest / "objects").write_text("half a clone")
    started(o, op["id"])
    o.cancel(op["id"])
    assert wait(o, op["id"])["status"] == "cancelled"
    assert not dest.exists() and tmp_path.is_dir()


def test_a_cancel_pressed_before_git_started_still_stops_it(tmp_path, monkeypatch):
    git_says(monkeypatch, "import time; time.sleep(30)")
    o = ops.Operations()
    real, pressed = subprocess.Popen, []

    def popen(*a, **kw):
        # subprocess.Popen is patched for every caller, taskkill on Windows included: press Cancel once,
        # for git, while the thread is still starting it and there is no process to stop yet.
        if not pressed:
            pressed.append(True)
            (op_id,) = o._ops
            o.cancel(op_id)
        return real(*a, **kw)
    monkeypatch.setattr(ops.subprocess, "Popen", popen)
    began = time.monotonic()
    op = wait(o, o.push(Repo(tmp_path))["id"])
    assert op["status"] == "cancelled" and time.monotonic() - began < 15  # stopped, not run to the end


def test_an_operation_that_isnt_known_or_has_ended(tmp_path, monkeypatch):
    o = ops.Operations()
    with pytest.raises(GitError, match="isn't known any more"):
        o.status("nope")
    with pytest.raises(GitError, match="isn't known any more"):
        o.cancel("nope")
    git_says(monkeypatch, "pass")
    op = wait(o, o.fetch(Repo(tmp_path))["id"])
    assert o.cancel(op["id"])["status"] == "ok"  # nothing to cancel


def test_only_the_newest_finished_operations_are_remembered(monkeypatch):
    monkeypatch.setattr(ops, "KEEP", 2)
    o = ops.Operations()
    for i in range(4):
        o._ops[str(i)] = {"id": str(i), "status": "ok", "started_at": f"2026-11-07T00:00:0{i}"}
    o._ops["run"] = {"id": "run", "status": "running", "started_at": "2026-11-06"}
    o._trim()
    assert sorted(o._ops) == ["2", "3", "run"]


# --------------------------------------------------------------------------- stopping git

class Proc:
    def __init__(self, done=None, kill_error=None):
        self.pid, self.done, self.kill_error, self.killed = 4242, done, kill_error, False

    def poll(self):
        return self.done

    def kill(self):
        self.killed = True
        if self.kill_error:
            raise self.kill_error


def test_git_that_has_ended_isnt_stopped_again(monkeypatch):
    proc = Proc(done=0)
    ops._kill(proc)
    assert not proc.killed


def test_on_windows_git_and_its_helpers_are_ended_together(monkeypatch):
    ran = []
    monkeypatch.setattr(ops, "_WINDOWS", True)
    monkeypatch.setattr(ops.subprocess, "run", lambda args, **kw: ran.append(args))
    proc = Proc()
    ops._kill(proc)
    assert ran == [["taskkill", "/F", "/T", "/PID", "4242"]] and proc.killed


def test_a_process_group_already_gone_and_a_kill_that_fails_are_both_fine(monkeypatch):
    monkeypatch.setattr(ops, "_WINDOWS", False)

    def killpg(pid, sig):
        raise ProcessLookupError(3, "No such process")
    monkeypatch.setattr(os, "killpg", killpg, raising=False)
    proc = Proc(kill_error=OSError(3, "gone"))
    ops._kill(proc)
    assert proc.killed


# --------------------------------------------------------------------------- what a failed clone leaves

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_a_file_that_still_cant_be_removed_is_left_without_an_error(tmp_path, monkeypatch):
    target = tmp_path / "pack"
    target.write_text("x")

    def chmod(path, mode):
        raise PermissionError(1, "not permitted")
    monkeypatch.setattr(ops.os, "chmod", chmod)
    ops._writable_then_retry(os.remove, str(target), PermissionError())
    assert target.exists()


def test_a_read_only_git_object_is_removed_with_the_clone(tmp_path):
    clone = tmp_path / "c" / ".git" / "objects"
    clone.mkdir(parents=True)
    obj = clone / "pack"
    obj.write_text("x")
    obj.chmod(stat.S_IREAD)
    ops.remove_clone(tmp_path / "c")
    assert not (tmp_path / "c").exists()
