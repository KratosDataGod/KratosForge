"""One Git repository on this PC: branch names, the status count and summary, which remote the
links use, commit rows and bodies, file size limits and conflict views. Real, temporary Git
repositories with a configuration of their own; nothing reaches a remote."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from fabric_desktop.git import repo as repo_mod
from fabric_desktop.git.cli import GitError
from fabric_desktop.git.repo import (Repository, _commit_row, _summary, check_branch_name, conflict_view, init,
                                     notebook_text, parse_unified)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def raw(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True, text=True).stdout


def write(root: Path, rel: str, data: str | bytes) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(data, encoding="utf-8", newline="\n")
    return p


def _config(folder: Path, email: bool = True) -> Path:
    cfg = folder / "gitconfig"
    cfg.write_text("[user]\n\tname = Test Person\n" + ("\temail = test@example.com\n" if email else "")
                   + "[init]\n\tdefaultBranch = main\n[core]\n\tautocrlf = false\n", encoding="utf-8")
    return cfg


@pytest.fixture(autouse=True)
def git_home(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return cfg


@pytest.fixture
def repo(tmp_path) -> Repository:
    r = init(tmp_path / "project")
    write(r.root, "a.txt", "one\n")
    r.stage_all()
    r.commit("First commit")
    return r


# ---- branch names

def test_a_branch_name_of_244_characters_is_allowed_and_245_is_not():
    assert check_branch_name("b" * 244) == "b" * 244
    with pytest.raises(GitError) as raised:
        check_branch_name("b" * 245)
    assert raised.value.kind == "invalid" and "Enter a branch name" in str(raised.value)


def test_a_branch_name_that_starts_with_a_dash_is_refused_before_git_sees_it():
    with pytest.raises(GitError) as raised:
        check_branch_name("-short")
    assert "Enter a branch name" in str(raised.value)


# ---- status

def test_the_change_count_adds_staged_unstaged_and_untracked(repo):
    write(repo.root, "b.txt", "new\n")
    repo.stage(["b.txt"])
    write(repo.root, "a.txt", "two\n")
    write(repo.root, "c.txt", "untracked\n")
    st = repo.status()
    assert (len(st["staged"]), len(st["unstaged"]), len(st["untracked"])) == (1, 1, 1)
    assert st["changed"] == 3 and st["dirty"]


def test_a_file_with_conflicts_counts_as_a_change(repo):
    raw(repo.root, "switch", "-q", "-c", "other")
    write(repo.root, "a.txt", "theirs\n")
    raw(repo.root, "commit", "-q", "-am", "Theirs")
    raw(repo.root, "switch", "-q", "main")
    write(repo.root, "a.txt", "mine\n")
    raw(repo.root, "commit", "-q", "-am", "Mine")
    subprocess.run(["git", "-C", str(repo.root), "merge", "-q", "other"], capture_output=True)
    write(repo.root, "c.txt", "untracked\n")
    st = repo.status()
    assert len(st["conflicts"]) == 1 and len(st["untracked"]) == 1
    assert st["changed"] == 2


def _info(**kw):
    base = {"commit": "0123456789abcdef", "branch": "main", "upstream": None, "ahead": 0, "behind": 0}
    return {**base, **kw}


def test_the_summary_of_a_detached_head_names_the_short_commit():
    assert _summary(_info(branch=None), 0) == "0123456 (no branch), no uncommitted changes, not linked to a remote branch."


def test_the_summary_of_a_linked_branch_in_step():
    assert _summary(_info(upstream="origin/main"), 1) == (
        "main, 1 uncommitted change, in step with origin/main as of the last fetch.")


def test_the_summary_counts_pushes_and_pulls_and_never_says_in_step_then():
    assert _summary(_info(upstream="origin/main", ahead=2), 0) == "main, no uncommitted changes, 2 to push."
    assert _summary(_info(upstream="origin/main", behind=3), 2) == "main, 2 uncommitted changes, 3 to pull."
    assert _summary(_info(upstream="origin/main", ahead=1, behind=1), 0) == (
        "main, no uncommitted changes, 1 to push, 1 to pull.")


# ---- which remote the links use

def test_links_use_the_branch_s_own_remote_and_its_default_branch(repo):
    raw(repo.root, "remote", "add", "origin", "https://github.com/first/one.git")
    raw(repo.root, "remote", "add", "upstream", "https://github.com/second/two.git")
    raw(repo.root, "config", "branch.main.remote", "upstream")
    head = repo.head()
    raw(repo.root, "update-ref", "refs/remotes/upstream/develop", head)
    raw(repo.root, "symbolic-ref", "refs/remotes/upstream/HEAD", "refs/remotes/upstream/develop")
    assert repo.default_branch("upstream") == "develop"
    links = repo.status()["links"]
    assert links["repository"] == "https://github.com/second/two"
    assert links["new_pull_request"] == "https://github.com/second/two/compare/develop...main?expand=1"
    assert repo._push_remote()["name"] == "upstream"


def test_without_a_branch_remote_the_links_use_origin(repo):
    raw(repo.root, "remote", "add", "fork", "https://github.com/third/three.git")
    raw(repo.root, "remote", "add", "origin", "https://github.com/first/one.git")
    assert repo._push_remote()["name"] == "origin"
    assert repo.status()["links"]["repository"] == "https://github.com/first/one"


# ---- commits

def test_a_commit_needs_an_email_as_well_as_a_name(repo, tmp_path, monkeypatch):
    folder = tmp_path / "noemail"
    folder.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(_config(folder, email=False)))
    write(repo.root, ".env", "A=1\n")
    repo.stage([".env"])
    with pytest.raises(GitError) as raised:
        repo.commit("Add env")
    assert raised.value.kind == "identity"  # asked before the staged files are scanned


def test_a_commit_s_body_is_kept_apart_from_its_subject(repo):
    write(repo.root, "b.txt", "new\n")
    repo.stage(["b.txt"])
    repo.commit("Add b\n\nBecause the report needs it.")
    detail = repo.commit_detail("HEAD")
    assert detail["subject"] == "Add b" and detail["body"] == "Because the report needs it."


def test_a_commit_row_keeps_each_field_in_its_place():
    row = _commit_row("full\0short\0Ann\0ann@example.com\x002026-11-18T10:00:00+00:00\0p1 p2\0HEAD -> main, tag: v1\0Subj")
    assert row == {"commit": "full", "short": "short", "author": "Ann", "email": "ann@example.com",
                   "date": "2026-11-18T10:00:00+00:00", "parents": ["p1", "p2"], "refs": ["HEAD -> main", "tag: v1"],
                   "subject": "Subj"}
    assert _commit_row("only") == {"commit": "only", "short": "", "author": "", "email": "", "date": "",
                                   "parents": [], "refs": [], "subject": ""}


def test_the_branch_you_are_on_cant_be_deleted_even_when_confirmed(repo):
    with pytest.raises(GitError) as raised:
        repo.delete_branch("main", confirm=True, force=True)
    assert raised.value.kind == "invalid" and "branch you're on" in str(raised.value)
    assert repo.branch() == "main"


# ---- files and conflicts

def test_a_file_at_a_commit_is_cut_only_beyond_five_million_bytes(repo, monkeypatch):
    assert repo_mod.MAX_FILE_BYTES == 5_000_000
    monkeypatch.setattr(repo_mod, "MAX_FILE_BYTES", 10)
    write(repo.root, "ten.txt", "0123456789")
    write(repo.root, "eleven.txt", "0123456789A")
    repo.stage_all()
    repo.commit("Sizes")
    assert repo.file_at("HEAD", "ten.txt")["truncated"] is False
    eleven = repo.file_at("HEAD", "eleven.txt")
    assert eleven["truncated"] is True and eleven["text"] == "0123456789" and eleven["size"] == 11


def test_a_missing_or_binary_side_has_no_conflict_view():
    empty = {"view": "conflict", "binary": False, "hunks": [], "added": 0, "removed": 0, "truncated": False,
             "conflicts": 0}
    assert conflict_view(None) == empty
    assert conflict_view(b"a\0b") == {**empty, "binary": True}


def test_a_conflict_view_starts_at_line_one_with_no_old_side():
    view = conflict_view(b"<<<<<<< HEAD\nmine\n=======\ntheirs\n>>>>>>> origin/main\n")
    hunk = view["hunks"][0]
    assert (hunk["old_start"], hunk["new_start"], hunk["context"]) == (0, 1, "")
    assert hunk["header"] == "1 conflict in this file" and view["conflicts"] == 1
    assert (view["added"], view["removed"]) == (0, 0)


# ---- more status, discards and secrets

def test_a_staged_rename_to_a_name_with_spaces_keeps_the_whole_name(repo):
    raw(repo.root, "mv", "a.txt", "new name here.txt")
    st = repo.status()
    assert st["staged"] == [{"path": "new name here.txt", "state": "renamed", "code": "R", "from": "a.txt"}]


def test_a_stash_is_counted_and_listed_with_its_date_and_message(repo):
    write(repo.root, "a.txt", "changed\n")
    raw(repo.root, "stash", "push", "-q", "-m", "keep this")
    assert repo.status()["stashes"] == 1
    (entry,) = repo.stashes()
    assert entry["ref"] == "stash@{0}" and entry["message"].endswith("keep this")
    assert entry["date"][:2] == "20" and "T" in entry["date"]


def test_the_discard_question_counts_the_files(repo):
    write(repo.root, "a.txt", "changed\n")
    write(repo.root, "b.txt", "new\n")
    with pytest.raises(GitError) as one:
        repo.discard(["a.txt"])
    assert "your changes to 1 file and" in str(one.value)
    with pytest.raises(GitError) as two:
        repo.discard(["a.txt", "b.txt"])
    assert "your changes to 2 files and" in str(two.value)


def test_one_staged_secret_is_named_in_the_singular(repo):
    write(repo.root, ".env", "A=1\n")
    repo.stage([".env"])
    with pytest.raises(GitError) as raised:
        repo.commit("Add env")
    assert raised.value.kind == "secrets" and str(raised.value).startswith("1 possible secret in the staged files.")


def test_history_says_there_is_more_only_beyond_the_limit(repo):
    assert repo.log(limit=1)["more"] is False
    write(repo.root, "b.txt", "new\n")
    repo.stage(["b.txt"])
    repo.commit("Second")
    assert repo.log(limit=2)["more"] is False
    assert repo.log(limit=1)["more"] is True


def test_a_commit_that_renames_one_file_and_adds_another_lists_both(repo):
    raw(repo.root, "mv", "a.txt", "renamed.txt")
    write(repo.root, "z_added.txt", "new\n")
    repo.stage(["z_added.txt"])
    repo.commit("Rename and add")
    files = repo.commit_detail("HEAD")["files"]
    assert [(f["path"], f["state"], f.get("from")) for f in files] == [
        ("renamed.txt", "renamed", "a.txt"), ("z_added.txt", "added", None)]


def test_an_untracked_text_file_shows_every_line_as_added(repo):
    write(repo.root, "new.txt", "one\ntwo\n")
    d = repo.diff("new.txt")
    assert d["untracked"] is True and d["view"] == "text" and d["binary"] is False
    assert (d["added"], d["removed"]) == (2, 0)


def test_a_commit_row_with_refs_but_no_parents_has_no_parents():
    assert _commit_row("c\0s\0A\0e\0d\0\0HEAD -> main\0First")["parents"] == []


def test_a_summary_before_the_first_commit_counts_files_to_commit():
    assert _summary(_info(commit=None), 0) == "Nothing is committed yet."
    assert _summary(_info(commit=None), 1) == "Nothing is committed yet. 1 file to commit."
    assert _summary(_info(commit=None), 2) == "Nothing is committed yet. 2 files to commit."


# ---- notebooks and diffs

def _nb(*sources) -> str:
    return json.dumps({"cells": [{"cell_type": "code", "source": s, "outputs": []} for s in sources]})


def test_a_staged_notebook_is_compared_with_the_last_commit(repo):
    write(repo.root, "nb.ipynb", _nb(["x = 1\n"], ["y = 2\n"]))
    repo.stage(["nb.ipynb"])
    repo.commit("Notebook")
    write(repo.root, "nb.ipynb", _nb(["x = 1\n"], ["y = 3\n"]))
    repo.stage(["nb.ipynb"])
    d = repo.diff("nb.ipynb", staged=True)
    assert d["view"] == "notebook" and d["binary"] is False
    assert (d["added"], d["removed"]) == (1, 1)


def test_notebook_text_ends_each_cell_with_one_newline():
    text = notebook_text(json.dumps({"cells": [{"source": "a"}, {"cell_type": "markdown", "source": ""},
                                               {"source": ["b\n"]}]}).encode())
    assert text == "# ---- cell 1 (code) ----\na\n# ---- cell 2 (markdown) ----\n# ---- cell 3 (code) ----\nb\n"


def test_two_conflict_blocks_are_counted_in_the_plural():
    block = b"<<<<<<< HEAD\nmine\n=======\ntheirs\n>>>>>>> origin/main\n"
    view = conflict_view(block + b"middle\n" + block)
    assert view["hunks"][0]["header"] == "2 conflicts in this file" and view["conflicts"] == 2


def test_a_diff_is_cut_only_past_the_line_limit(monkeypatch):
    monkeypatch.setattr(repo_mod, "MAX_DIFF_LINES", 2)
    assert parse_unified("@@ -0,0 +1,2 @@\n+a\n+b\n")["truncated"] is False
    cut = parse_unified("@@ -0,0 +1,3 @@\n+a\n+b\n+c\n")
    assert cut["truncated"] is True and cut["added"] == 2 and cut["hunks"][0]["id"] is None
