"""Mutation batch 272: what git/ops.py's background runs record, checked where the edge tests didn't
look. As in test_git_ops_edges.py, a small Python program stands in for git, so no network or
repository is needed."""

from __future__ import annotations

import sys
import threading
import time

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
            seen.append(list(args))
        return [sys.executable, "-c", script]
    monkeypatch.setattr(cli, "command", command)


def test_an_operation_has_a_twelve_character_id_and_talks_to_a_remote(tmp_path, monkeypatch):
    git_says(monkeypatch, "pass")
    o = ops.Operations()
    op = o._start("fetch", None, ["fetch"], cwd=tmp_path)
    assert len(op["id"]) == 12 and op["remote"] is True
    wait(o, op["id"])


def test_the_page_keeps_the_last_fifty_finished_operations_and_never_drops_a_few(tmp_path):
    o = ops.Operations()
    for i in range(3):
        o._ops[f"op{i}"] = {"id": f"op{i}", "status": "ok", "started_at": f"2026-01-01T00:00:0{i}"}
    o._trim()
    assert len(o._ops) == 3  # fewer than the limit: none dropped
    for i in range(3, 60):
        o._ops[f"op{i}"] = {"id": f"op{i}", "status": "ok", "started_at": f"2026-01-01T00:{i:02d}:00"}
    o._trim()
    assert len(o._ops) == ops.KEEP == 50
    assert "op59" in o._ops and "op0" not in o._ops


def test_operations_are_listed_newest_first(tmp_path):
    o = ops.Operations()
    o._ops = {"a": {"id": "a", "status": "ok", "started_at": "2026-01-01T00:00:01"},
              "b": {"id": "b", "status": "ok", "started_at": "2026-01-01T00:00:02"}}
    assert [x["id"] for x in o.list()] == ["b", "a"]


def test_no_known_ending_gives_no_message_of_its_own():
    assert ops._done_message("something else") == ""


def test_a_failed_run_tells_its_after_step_it_failed(tmp_path, monkeypatch):
    git_says(monkeypatch, "import sys; sys.exit(1)")
    told = []
    o = ops.Operations()
    op = wait(o, o._start("fetch", None, ["fetch"], cwd=tmp_path, after=told.append,
                          result=lambda: {"read": True})["id"])
    assert told == [False]
    assert op["status"] == "failed" and op["result"] is None  # only a conflict reads the result


def test_a_clone_makes_its_parent_folders_or_uses_one_that_exists(tmp_path, monkeypatch):
    git_says(monkeypatch, "pass")
    (tmp_path / "repos").mkdir()
    o = ops.Operations()
    for parent in (tmp_path / "repos", tmp_path / "new" / "deeper"):
        op = o.clone("https://example.com/team/demo.git", str(parent))
        assert op["status"] == "running" and parent.is_dir()
        wait(o, op["id"])


def test_a_first_push_goes_to_origin_or_else_the_first_remote(tmp_path, monkeypatch):
    seen = []
    git_says(monkeypatch, "pass", seen)
    o = ops.Operations()
    wait(o, o.push(Repo(tmp_path, remotes=("upstream", "origin"), upstream=None))["id"])
    wait(o, o.push(Repo(tmp_path, remotes=("first", "second"), upstream=None))["id"])
    assert [a[3] for a in seen] == ["origin", "first"]


def test_the_run_is_a_background_thread(tmp_path, monkeypatch):
    git_says(monkeypatch, "import time; time.sleep(30)")
    o = ops.Operations()
    op = o._start("fetch", None, ["fetch"], cwd=tmp_path)
    try:
        threads = [t for t in threading.enumerate() if t.name == "git-fetch"]
        assert threads and all(t.daemon for t in threads)
    finally:
        end = time.monotonic() + 20
        while op["id"] not in o._procs and time.monotonic() < end:
            time.sleep(0.01)
        o.cancel(op["id"])
        wait(o, op["id"])


def test_seconds_taken_are_rounded_to_a_tenth(tmp_path, monkeypatch):
    git_says(monkeypatch, "import time; time.sleep(0.4)")
    o = ops.Operations()
    op = wait(o, o._start("fetch", None, ["fetch"], cwd=tmp_path)["id"])
    assert 0.3 <= op["seconds"] < 30 and op["seconds"] == round(op["seconds"], 1)


def test_details_are_the_last_forty_lines_and_at_most_twenty_from_stdout(tmp_path, monkeypatch):
    git_says(monkeypatch, "print('\\n'.join(f'out{i}' for i in range(25)))")
    o = ops.Operations()
    op = wait(o, o._start("fetch", None, ["fetch"], cwd=tmp_path)["id"])
    assert op["details"].splitlines() == [f"out{i}" for i in range(5, 25)]
    git_says(monkeypatch, "import sys; sys.stderr.write('\\n'.join(f'err{i}' for i in range(39)) + '\\n'); "
                          "print('\\n'.join(f'out{i}' for i in range(20)))")
    op = wait(o, o._start("fetch", None, ["fetch"], cwd=tmp_path)["id"])
    lines = op["details"].splitlines()
    assert len(lines) == 40 and lines[0] == "err19" and lines[-1] == "out19"


class _TimeUpAtOnce:
    """Stands in for ops.AwakeTimer: the limit is reached as soon as git starts."""

    def __init__(self, seconds, fn):
        self.seconds, self.fn = seconds, fn

    def start(self):
        self.fn()

    def cancel(self):
        pass


def test_the_timeout_message_names_the_limit_in_whole_minutes(tmp_path, monkeypatch):
    git_says(monkeypatch, "import time; time.sleep(30)")
    monkeypatch.setattr(cli, "NETWORK_TIMEOUT", 150)  # 2.5 minutes is said as 2
    seen = []
    monkeypatch.setattr(ops, "AwakeTimer", lambda s, fn: seen.append(s) or _TimeUpAtOnce(s, fn))
    o = ops.Operations()
    op = wait(o, o._start("fetch", None, ["fetch"], cwd=tmp_path)["id"])
    assert seen == [150] and op["error_kind"] == "timeout"
    assert "longer than 2 minutes and was stopped" in op["message"]
