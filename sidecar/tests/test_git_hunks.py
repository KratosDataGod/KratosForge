"""Staging part of a file: one block of changed lines at a time (Source control > Changes)."""

from __future__ import annotations

import subprocess

import pytest

from fabric_desktop.git.cli import GitError
from fabric_desktop.git import repo as repo_mod
from fabric_desktop.git.repo import init

from test_git import git_home, raw, write  # noqa: F401  (git_home is an autouse fixture)

BASE = "".join(f"line {i}\n" for i in range(1, 31))


@pytest.fixture
def repo(tmp_path):
    r = init(tmp_path / "project")
    write(r.root, "load.py", BASE)
    r.stage_all()
    r.commit("First commit")
    # Two changes far apart: two blocks.
    write(r.root, "load.py", BASE.replace("line 3\n", "line 3 fixed\n").replace("line 27\n", "line 27 experiment\n"))
    return r


def blocks(r, staged=False):
    return r.diff("load.py", staged=staged)["hunks"]


def test_stage_one_block_leaves_the_other_unstaged(repo):
    first, second = blocks(repo)
    repo.apply_hunk("load.py", first["id"], "stage")
    assert "line 3 fixed" in raw(repo.root, "diff", "--cached") and "experiment" not in raw(repo.root, "diff", "--cached")
    assert [h["id"] for h in blocks(repo)] == [second["id"]]
    st = repo.status()
    assert [e["path"] for e in st["staged"]] == ["load.py"] and [e["path"] for e in st["unstaged"]] == ["load.py"]
    # The file on disk is untouched.
    assert "line 27 experiment" in (repo.root / "load.py").read_text(encoding="utf-8")


def test_unstage_one_block(repo):
    repo.stage(["load.py"])
    first, _ = blocks(repo, staged=True)
    repo.apply_hunk("load.py", first["id"], "unstage")
    cached = raw(repo.root, "diff", "--cached")
    assert "line 3 fixed" not in cached and "line 27 experiment" in cached


def test_discard_one_block_asks_first_and_keeps_the_rest(repo):
    first, _ = blocks(repo)
    with pytest.raises(GitError) as exc:
        repo.apply_hunk("load.py", first["id"], "discard")
    assert exc.value.kind == "confirm" and "2 changed lines" in str(exc.value)
    assert "line 3 fixed" in (repo.root / "load.py").read_text(encoding="utf-8")  # nothing changed yet
    repo.apply_hunk("load.py", first["id"], "discard", confirm=True)
    text = (repo.root / "load.py").read_text(encoding="utf-8")
    assert "line 3 fixed" not in text and "line 3\n" in text and "line 27 experiment" in text


def test_a_block_that_changed_since_it_was_shown_is_refused(repo):
    first, _ = blocks(repo)
    write(repo.root, "load.py", (repo.root / "load.py").read_text(encoding="utf-8").replace("line 3 fixed", "line 3 fixed again"))
    with pytest.raises(GitError) as exc:
        repo.apply_hunk("load.py", first["id"], "stage")
    assert exc.value.kind == "stale"
    assert raw(repo.root, "diff", "--cached") == ""


def test_files_without_blocks_are_refused(repo):
    write(repo.root, "new.py", "x = 1\n")
    with pytest.raises(GitError, match="doesn't track this file yet"):
        repo.apply_hunk("new.py", "0" * 16, "stage")
    with pytest.raises(GitError, match="Choose to stage"):
        repo.apply_hunk("load.py", blocks(repo)[0]["id"], "commit")
    with pytest.raises(GitError):
        repo.apply_hunk("../outside.py", "0" * 16, "stage")


def test_crlf_and_no_newline_at_end_survive_a_block(tmp_path):
    r = init(tmp_path / "crlf")
    (r.root / "w.txt").write_bytes(b"a\r\nb\r\nc\r\n" + b"".join(b"x%d\r\n" % i for i in range(20)) + b"end")
    r.stage_all()
    r.commit("First")
    (r.root / "w.txt").write_bytes(b"a\r\nB\r\nc\r\n" + b"".join(b"x%d\r\n" % i for i in range(20)) + b"END")
    first, second = r.diff("w.txt")["hunks"]
    r.apply_hunk("w.txt", second["id"], "stage")

    def index():  # bytes as Git keeps them (text mode would turn CRLF into LF)
        return subprocess.run(["git", "-C", str(r.root), "show", ":w.txt"], capture_output=True, check=True).stdout

    staged = index()
    assert staged.endswith(b"END") and b"\r\nb\r\n" in staged  # the first block stays unstaged
    r.apply_hunk("w.txt", first["id"], "stage")
    assert index() == (r.root / "w.txt").read_bytes()


def test_a_block_cut_short_by_the_line_limit_has_no_buttons(repo, monkeypatch):
    monkeypatch.setattr(repo_mod, "MAX_DIFF_LINES", 10)
    d = repo.diff("load.py")
    assert d["truncated"] and d["hunks"][0]["id"] and d["hunks"][-1]["id"] is None
