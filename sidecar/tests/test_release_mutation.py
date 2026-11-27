"""meta/release.py's words and records, read directly (mutation testing, batch 338).

test_release.py promotes through the service; these pin the status line for each state, the problems'
counts, a folder outside Git, more changes than are listed, a rollback's record, and the reads of the last
commit where there is none or the file is too big.
"""

from __future__ import annotations

import getpass
import subprocess

import pytest

from fabric_desktop.git import repo as git_repo
from fabric_desktop.meta import release
from fabric_desktop.service import Service

C = "0123456789abcdef"


@pytest.mark.parametrize(("args", "line"), [
    ((None, None, None, None, None, 0), "Nothing is committed yet."),
    (("main", C, "origin/main", 0, 0, 0), "main at 0123456."),
    ((None, C, "origin/main", 0, 0, 0), "0123456 (no branch)."),
    (("main", C, "origin/main", 0, 0, 1), "main at 0123456, 1 uncommitted file."),
    (("main", C, "origin/main", 0, 0, 2), "main at 0123456, 2 uncommitted files."),
    (("main", C, "origin/main", 1, 0, 0), "main at 0123456, 1 commit not pushed."),
    (("main", C, "origin/main", 3, 2, 0), "main at 0123456, 3 commits not pushed, 2 behind origin/main."),
    (("main", C, None, None, None, 0), "main at 0123456, not pushed (no upstream branch)."),
])
def test_the_status_line_says_where_the_files_stand(args, line):
    assert release._summary(*args) == line


def test_a_dirty_tree_without_a_count_is_named_as_such():
    git = {"repo": True, "commit": C, "dirty": True, "upstream": "origin/main"}
    assert release.problems(git) == ["0 files changed since the last commit; commit them"]


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "config", "commit.gpgsign", "false")
    return root


def test_a_folder_outside_git_is_available_but_no_repo(tmp_path):
    out = release.status(tmp_path)
    assert (out["available"], out["repo"]) == (True, False)
    assert release.committed(tmp_path, "metadata") is None
    assert release.committed_file(tmp_path / "gone" / "a.toml") is None  # a folder that isn't there


def test_more_changes_than_are_listed_are_counted(repo):
    (repo / "a.toml").write_text("a", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "start")
    for i in range(release.MAX_CHANGES + 1):
        (repo / f"f{i:03}.toml").write_text("x", encoding="utf-8")
    out = release.status(repo)
    assert out["available"] is True and out["repo"] is True
    assert len(out["changes"]) == 200 and out["change_count"] == 201


def test_a_file_too_big_to_read_at_the_last_commit_is_none(repo, monkeypatch):
    (repo / "big.toml").write_text("x" * 50, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "start")
    monkeypatch.setattr(git_repo, "MAX_FILE_BYTES", 10)
    assert release.committed_file(repo / "big.toml") is None
    monkeypatch.setattr(git_repo, "MAX_FILE_BYTES", 100)
    assert release.committed_file(repo / "big.toml") == "x" * 50


def test_the_login_name_and_a_receipt_that_isnt_one():
    assert release._user() == getpass.getuser()
    assert release.verified(None, "r") is False and release.verified("not json", "r") is False


def test_a_rollback_records_both_releases_and_who_rolled_back(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    project = s._project()
    earlier = {"commit": "abc", "branch": "main", "dirty": False, "pushed": True, "receipt_id": "r1"}
    body = release.rollback_record(project, "test", "7", {"version": "9", "receipt_id": "r9"}, earlier, by="pat")
    assert body["project"] == {"name": "crm", "metadata_version": "7", "source_env": "test", "target_env": "test"}
    assert body["rollback"] == {"from_version": "9", "from_receipt_id": "r9", "to_receipt_id": "r1"}
    assert body["code"] == {"repo": True, "commit": "abc", "branch": "main", "dirty": False, "pushed": True,
                            "uncommitted": []}
    assert body["identity"] == {"user": "pat"}
    assert release.deployment_columns(body)["by"] == "pat"
    unnamed = release.rollback_record(project, "test", "7", {"version": "9"}, {}, object_id="oid")
    assert unnamed["identity"] == {"user": getpass.getuser(), "object_id": "oid"}
