"""Where a project's files stand in Git, and the release record (meta/release.py), pinned so a changed
word, number or check fails a test: which files count, how a status reads its Git layer (a stand-in
Repository here), the summary and problems in words, the identity, the deployment columns, a receipt's
check, and a file as the last commit has it."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop.git.cli import GitError
from fabric_desktop.meta import release


def test_which_files_count_toward_the_version():
    assert release._counts("entities/a.toml") is True
    assert release._counts("environments.toml") is False
    assert release._counts("sub/environments.toml") is True  # only the project's own
    assert release._counts("x/__pycache__/a.py") is False
    assert release._counts("transforms/a.pyc") is False


def fake_git(monkeypatch, root, status, *, repo_root=None, git_path="git"):
    class Repo:
        def __init__(self, folder):
            if status is None:
                raise GitError("not a repository")
            self.root = Path(repo_root or folder)

        def status(self):
            return status

    monkeypatch.setattr(release, "cli", SimpleNamespace(git_path=lambda: git_path))
    monkeypatch.setattr(release, "Repository", Repo)


def git_status(**kw):
    return {"conflicts": [], "staged": [], "unstaged": [], "untracked": [], "commit": "0123456789abcdef",
            "branch": "main", "upstream": "origin/main", "ahead": 0, "behind": 0} | kw


def test_no_git_or_no_repository_says_why(tmp_path, monkeypatch):
    fake_git(monkeypatch, tmp_path, git_status(), git_path=None)
    assert release.status(tmp_path) == {"available": False, "repo": False,
                                        "message": "Git isn't installed on this PC, so releases can't name a commit."}
    fake_git(monkeypatch, tmp_path, None)
    out = release.status(tmp_path)
    assert out["available"] is True and out["repo"] is False and "isn't in a Git repository" in out["message"]


def test_a_clean_pushed_project(tmp_path, monkeypatch):
    fake_git(monkeypatch, tmp_path, git_status())
    out = release.status(tmp_path)
    assert out == {"available": True, "repo": True, "branch": "main", "commit": "0123456789abcdef",
                   "upstream": "origin/main", "ahead": 0, "behind": 0, "dirty": False, "changes": [],
                   "change_count": 0, "pushed": True, "message": "main at 0123456."}


def test_changes_inside_the_project_folder_are_named_by_their_state(tmp_path, monkeypatch):
    project = tmp_path / "repo" / "crm"
    project.mkdir(parents=True)
    st = git_status(
        conflicts=[{"path": "crm/a.toml", "state": "modified"}],
        staged=[{"path": "crm/a.toml", "state": "added"}, {"path": "crm/b.toml", "state": "added"},
                {"path": "other/c.toml", "state": "added"}, {"path": "crmx/d.toml", "state": "added"}],
        unstaged=[{"path": "crm/c.toml", "state": "type changed"}, {"path": "crm/e.toml", "state": "odd"},
                  {"path": "crm/environments.toml", "state": "modified"}],
        untracked=[{"path": "crm/new.py", "state": "untracked"}],
        ahead=2, behind=1)
    fake_git(monkeypatch, project, st, repo_root=tmp_path / "repo")
    out = release.status(project)
    assert out["changes"] == [{"path": "a.toml", "state": "conflict"}, {"path": "b.toml", "state": "added"},
                              {"path": "c.toml", "state": "changed"}, {"path": "e.toml", "state": "changed"},
                              {"path": "new.py", "state": "new"}]
    assert out["dirty"] is True and out["change_count"] == 5 and out["pushed"] is False
    assert out["message"] == "main at 0123456, 5 uncommitted files, 2 commits not pushed, 1 behind origin/main."


def test_a_project_at_the_repository_root_keeps_its_paths_whole(tmp_path, monkeypatch):
    fake_git(monkeypatch, tmp_path, git_status(untracked=[{"path": "a.toml", "state": "untracked"}]))
    assert release.status(tmp_path)["changes"] == [{"path": "a.toml", "state": "new"}]


def test_a_branch_with_no_upstream_has_no_ahead_or_behind(tmp_path, monkeypatch):
    fake_git(monkeypatch, tmp_path, git_status(upstream=None, ahead=3, behind=4))
    out = release.status(tmp_path)
    assert (out["ahead"], out["behind"], out["pushed"]) == (None, None, False)


def test_only_200_changes_are_listed_but_all_are_counted(tmp_path, monkeypatch):
    fake_git(monkeypatch, tmp_path, git_status(untracked=[{"path": f"f{i}.toml", "state": "untracked"}
                                                          for i in range(201)]))
    out = release.status(tmp_path)
    assert len(out["changes"]) == 200 and out["change_count"] == 201


@pytest.mark.parametrize("args, says", [
    ((None, None, None, None, None, 0), "Nothing is committed yet."),
    (("main", "abcdef0123", "origin/main", 0, 0, 1), "main at abcdef0, 1 uncommitted file."),
    ((None, "abcdef0123", None, None, None, 0), "abcdef0 (no branch), not pushed (no upstream branch)."),
    (("dev", "abcdef0123", "origin/dev", 1, 0, 0), "dev at abcdef0, 1 commit not pushed."),
])
def test_the_summary_in_words(args, says):
    assert release._summary(*args) == says


def test_problems_in_words():
    assert release.problems({"repo": False}) == ["The project isn't in Git."]
    assert release.problems({"repo": False, "message": "No Git."}) == ["No Git."]
    assert release.problems({"repo": True, "commit": "abc", "upstream": "o/m"}) == []
    assert release.problems({"repo": True, "dirty": True, "change_count": 1}) == [
        "nothing is committed yet", "1 file changed since the last commit; commit them"]
    assert release.problems({"repo": True, "commit": "abc", "dirty": True, "change_count": 2}) == [
        "2 files changed since the last commit; commit them", "branch (none) has no upstream; push it"]
    assert release.problems({"repo": True, "commit": "abc", "upstream": "o/m", "ahead": 1}) == [
        "1 commit not pushed; push them"]
    assert release.problems({"repo": True, "commit": "abc", "upstream": "o/m", "ahead": 3}) == [
        "3 commits not pushed; push them"]


def test_the_identity_names_the_object_id_only_when_there_is_one():
    assert release._identity("me", None) == {"user": "me"}
    assert release._identity("me", "oid") == {"user": "me", "object_id": "oid"}


def test_a_receipt_with_no_text_or_the_wrong_id_isnt_verified():
    assert release.verified("", "x") is False and release.verified(None, None) is False
    assert release.verified("[1]", "x") is False
    assert release.verified("{not json", "x") is False


def test_a_file_too_big_or_binary_at_head_has_no_text():
    def repo(f):
        return SimpleNamespace(file_at=lambda rev, path: f)
    assert release._at_head(repo({"binary": False, "truncated": False, "text": "t"}), Path("a")) == "t"
    assert release._at_head(repo({"binary": True, "truncated": False, "text": "t"}), Path("a")) is None
    assert release._at_head(repo({"binary": False, "truncated": True, "text": "t"}), Path("a")) is None


def test_the_user_is_the_login_name(monkeypatch):
    monkeypatch.setattr(release, "getpass", SimpleNamespace(getuser=lambda: "ana"))
    assert release._user() == "ana"


def test_nothing_is_committed_with_no_repository_or_no_head(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "cli", SimpleNamespace(text=lambda *a, **kw: "a.toml\0"))
    monkeypatch.setattr(release, "_repo", lambda root: None)
    assert release.committed(tmp_path, "entities") is None
    monkeypatch.setattr(release, "_repo", lambda root: SimpleNamespace(head=lambda: None))
    assert release.committed(tmp_path, "entities") is None


def test_a_folder_that_isnt_there_or_no_git_has_no_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "Repository", lambda folder: "repo")
    monkeypatch.setattr(release, "cli", SimpleNamespace(git_path=lambda: "git"))
    assert release._repo(tmp_path) == "repo"
    assert release._repo(tmp_path / "missing") is None
    monkeypatch.setattr(release, "cli", SimpleNamespace(git_path=lambda: None))
    assert release._repo(tmp_path) is None


def test_a_dirty_status_with_no_count_says_0_files():
    assert release.problems({"repo": True, "commit": "abc", "upstream": "o/m", "dirty": True}) == [
        "0 files changed since the last commit; commit them"]


def test_the_deployment_columns_name_who_promoted_or_no_one():
    body = {"code": {"commit": "c", "branch": "b", "dirty": False, "pushed": True},
            "environment": {"engine_version": "1", "lock_hash": "h"}, "identity": {"user": "ana"}}
    cols = release.deployment_columns(body)
    assert cols["by"] == "ana" and release.verified(cols["receipt"], cols["receipt_id"]) is True
    assert release.deployment_columns({k: v for k, v in body.items() if k != "identity"})["by"] is None


def test_a_rollback_names_who_asked_for_it_before_the_login_name(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "_user", lambda: "login")
    project = SimpleNamespace(entities={}, root=tmp_path, local_data=tmp_path / "crm" / ".local")
    current, earlier = {"version": "v2"}, {"commit": "c1", "receipt_id": "r1"}
    assert release.rollback_record(project, "prod", "v1", current, earlier, by="ana")["identity"] == {"user": "ana"}
    assert release.rollback_record(project, "prod", "v1", current, earlier)["identity"] == {"user": "login"}


def test_committed_files_are_listed_read_only(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(release, "_repo", lambda root: SimpleNamespace(
        head=lambda: "abc", file_at=lambda rev, path: {"binary": False, "truncated": False, "text": "x\r\n"}))
    monkeypatch.setattr(release, "cli", SimpleNamespace(
        text=lambda root, args, **kw: calls.append(kw) or "e/a.toml\0e/b.py\0"))
    assert release.committed(tmp_path, "e", ".toml") == {"e/a.toml": "x\n"}
    assert calls == [{"read_only": True}]
