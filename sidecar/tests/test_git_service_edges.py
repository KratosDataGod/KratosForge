"""Source control's service and repository (git_service.py, git/repo.py), the paths the main tests
don't reach: each service call on a real temporary repository, the folder and path checks that refuse
before Git runs, and the branch, stash and merge asks Git would get wrong. Nothing reaches a remote."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.central.core import PlatformError
from fabric_desktop.git import scopes
from fabric_desktop.git.cli import GitError
from fabric_desktop.git.repo import Repository, conflict_view, init, notebook_text
from fabric_desktop.git_service import _newer_op, _same
from tests.test_git import Svc, first_commit, git_home, raw, repo, write  # noqa: F401 - fixtures


@pytest.fixture
def svc(tmp_path, repo):  # noqa: F811
    s = Svc(tmp_path / "appdata")
    s.git_open(str(repo.root))
    return s


# ---- the service, call by call


def test_each_local_call_works_through_the_service(svc, repo):  # noqa: F811
    root = str(repo.root)
    write(repo.root, "entities/orders.toml", "[load]\nmode = \"scd2\"\n")
    assert svc.git_diff(root, "entities/orders.toml")["added"] >= 1
    assert svc.git_stage(root, ["entities/orders.toml"])["staged"]
    assert svc.git_scan(root) == {"findings": []}
    assert not svc.git_unstage(root, ["entities/orders.toml"])["staged"]
    svc.git_stage_all(root)
    first = svc.git_log(root)["commits"][0]["commit"]
    second = svc.git_commit(root, "Second")["commit"]
    assert svc.git_commit_detail(root, second)["files"]
    assert svc.git_compare(root, first, second)["files"]
    assert "scd2" in svc.git_file_at(root, second, "entities/orders.toml")["text"]
    assert svc.git_ops(root) == []
    svc.git_set_identity(root, "Repo Person", "repo@example.com")
    assert svc.git_settings(root)["identity"]["local_name"] == "Repo Person"
    remotes = svc.git_add_remote(root, "https://github.com/contoso/data.git")["remotes"]
    assert remotes[0]["name"] == "origin" and remotes[0]["provider"] == "github"


def test_branches_stashes_and_merges_through_the_service(svc, repo):  # noqa: F811
    root = str(repo.root)
    svc.git_create_branch(root, "other", switch=False)
    write(repo.root, "entities/orders.toml", "changed\n")
    assert svc.git_switch(root, "other", mode="stash")["stashed"]
    stashes = svc.git_branches(root)["stashes"]
    assert len(stashes) == 1 and stashes[0]["ref"] == "stash@{0}"
    svc.git_switch(root, "main")
    assert svc.git_restore_stash(root, "stash@{0}")["unstaged"]
    with pytest.raises(GitError, match="Confirm to go ahead"):
        svc.git_discard(root, ["entities/orders.toml"])
    assert not svc.git_discard(root, ["entities/orders.toml"], confirm=True)["unstaged"]
    with pytest.raises(GitError, match="No merge is in progress"):
        svc.git_abort_merge(root)
    assert "other" not in [b["name"] for b in svc.git_delete_branch(root, "other", confirm=True)["local"]]


def test_one_block_of_a_change_is_staged_through_the_service(svc, repo):  # noqa: F811
    root = str(repo.root)
    write(repo.root, "entities/orders.toml", "[load]\nmode = \"scd1\"\nkeys = [\"order_id\"]\nextra = 1\n")
    hunk = svc.git_diff(root, "entities/orders.toml")["hunks"][0]["id"]
    assert svc.git_apply_hunk(root, "entities/orders.toml", hunk, "stage")["staged"]


def test_set_up_for_vs_code_refuses_a_file_it_doesnt_list(svc, repo):  # noqa: F811
    assert svc.git_vscode_plan(str(repo.root))["name"] == "project"
    with pytest.raises(GitError, match="Only the files Set up for VS Code lists"):
        svc.git_vscode_apply(str(repo.root), ["../outside.txt"])


# ---- the list of opened repositories


def test_a_garbled_list_reads_as_empty_and_entries_without_a_folder_are_skipped(tmp_path, repo):  # noqa: F811
    s = Svc(tmp_path / "appdata")
    s.git_repos_file.parent.mkdir(parents=True)
    s.git_repos_file.write_text("{not json")
    assert s.git_repos()["repos"] == []
    s.git_repos_file.write_text(json.dumps({"repos": [{"name": "no path"}, {"path": str(tmp_path / "gone")}],
                                            "current": None}))
    listed = s.git_repos()["repos"]
    assert len(listed) == 1 and listed[0]["exists"] is False and listed[0]["kind"] is None


def test_forgetting_the_current_repository_makes_the_next_one_current(tmp_path, svc, repo):  # noqa: F811
    other = init(tmp_path / "other")
    svc.git_open(str(other.root))
    assert svc.git_repos()["current"] == str(other.root)
    assert svc.git_forget(str(other.root))["current"] == str(repo.root)
    assert svc.git_forget(str(repo.root))["current"] is None


def test_a_folder_that_already_says_what_it_holds_isnt_given_a_kind_again(tmp_path, svc):
    folder = tmp_path / "marked"
    folder.mkdir()
    (folder / scopes.MARKER).write_text('kind = "notebooks"\n')
    with pytest.raises(GitError, match="already says what it holds"):
        svc.git_init(str(folder), kind="notebooks")
    with pytest.raises(GitError, match="A repository holds"):
        svc.git_init(str(folder), kind="spreadsheets")


@pytest.mark.parametrize("call, says", [
    (lambda s, r: s.git_locations(r), "Locations belong to a notebooks repository"),
    (lambda s, r: s.git_capacity_diff(r), "doesn't hold capacity definitions"),
    (lambda s, r: s.git_platform_deploy(r), "doesn't hold platform definitions"),
])
def test_a_repository_of_another_kind_is_refused(svc, repo, call, says):  # noqa: F811
    with pytest.raises(GitError, match=says):
        call(svc, str(repo.root))


def test_locations_are_given_per_environment(tmp_path, svc):
    svc.git_create(str(tmp_path / "nbs"), "notebooks")
    with pytest.raises(GitError, match="given per environment"):
        svc.git_set_locations(str(tmp_path / "nbs"), ["dev"])


def test_a_path_in_a_repository_git_cant_read_has_no_branch(tmp_path, svc, repo):  # noqa: F811
    (repo.root / ".git" / "HEAD").write_text("garbage\n")
    assert svc.git_path_info(str(repo.root / "entities" / "orders.toml")) is None
    assert svc.git_path_info("bad\0path") is None


def test_same_folder_needs_two_paths():
    assert _same(None, "C:/x") is False and _same("", "") is False
    assert _same("bad\0path", "bad\0path") is False


# ---- platform reads a platform made before them doesn't have


class _Catalog:
    def __init__(self, exc):
        self.exc = exc

    def call(self, op, **kwargs):
        raise self.exc


@pytest.mark.parametrize("exc", [LookupError("404"), PlatformError("list_zones not found")])
def test_a_platform_older_than_the_app_says_to_repair_it(exc):
    with pytest.raises(GitError, match="Finish or repair"):
        _newer_op(_Catalog(exc), "list_zones")


def test_any_other_platform_refusal_is_passed_on():
    with pytest.raises(PlatformError, match="Only platform admins"):
        _newer_op(_Catalog(PlatformError("Only platform admins can do that")), "list_zones")


# ---- folders and paths refused before Git runs


@pytest.mark.parametrize("path, says", [
    ("", "Choose a folder"),
    ("relative/folder", "full path"),
])
def test_a_folder_must_be_a_full_path(path, says):
    with pytest.raises(GitError, match=says):
        init(path)


def test_a_file_isnt_made_a_repository(tmp_path):
    (tmp_path / "file.txt").write_text("x")
    with pytest.raises(GitError, match="is a file, not a folder"):
        init(tmp_path / "file.txt")


@pytest.mark.parametrize("path", ["../outside.txt", ".git/config", "."])
def test_paths_outside_the_work_tree_are_refused(repo, path):  # noqa: F811
    with pytest.raises(GitError):
        repo.rel(path)
    with pytest.raises(GitError, match="Choose at least one file"):
        repo.rels([])


@pytest.mark.parametrize("rev", ["", "-x", "a b", "a..b", "nosuchbranch"])
def test_a_revision_git_cant_find_or_that_looks_like_an_option_is_refused(repo, rev):  # noqa: F811
    with pytest.raises(GitError):
        repo.resolve(rev)


# ---- identity, settings, staging and history


@pytest.mark.parametrize("name, email, says", [
    ("", "a@b.c", "Enter your name"),
    ("A\nB", "a@b.c", "Enter your name"),
    ("Ann", "not-an-email", "Enter an email address"),
])
def test_a_commit_identity_must_be_a_name_and_an_email(repo, name, email, says):  # noqa: F811
    with pytest.raises(GitError, match=says):
        repo.set_identity(name, email)


def test_settings_warn_about_a_missing_or_plain_text_credential_helper(repo):  # noqa: F811
    assert any("no credential helper" in n for n in repo.settings()["notes"])
    raw(repo.root, "config", "--local", "credential.helper", "store")
    s = repo.settings()
    assert s["credential_helpers"] == ["store"] and any("plain text" in n for n in s["notes"])


def test_a_remote_name_is_checked_and_not_added_twice(repo):  # noqa: F811
    with pytest.raises(GitError, match="A remote name is letters"):
        repo.add_remote("bad name", "https://github.com/contoso/data.git")
    repo.add_remote("origin", "https://github.com/contoso/data.git")
    with pytest.raises(GitError, match="already has a remote called origin"):
        repo.add_remote("origin", "https://github.com/contoso/other.git")


def test_a_block_of_changes_is_refused_when_it_cant_be_chosen(repo):  # noqa: F811
    with pytest.raises(GitError, match="stage, unstage or discard"):
        repo.apply_hunk("entities/orders.toml", "h1", "squash")
    write(repo.root, "new.txt", "new\n")
    with pytest.raises(GitError, match="doesn't track this file yet"):
        repo.apply_hunk("new.txt", "h1", "stage")


def test_a_new_repository_has_no_history_and_its_first_branch_cant_be_branched(tmp_path):
    r = init(tmp_path / "empty")
    assert r.log() == {"commits": [], "more": False}
    assert r.default_branch() is None
    with pytest.raises(GitError, match="Commit something first"):
        r.create_branch("other", switch=False)


def test_a_renamed_file_shows_where_it_came_from(repo):  # noqa: F811
    first = repo.head()
    raw(repo.root, "mv", "entities/orders.toml", "entities/sales.toml")
    repo.commit("Rename")
    moved = repo.compare(first, "HEAD")["files"]
    assert [(f["path"], f["code"], f.get("from")) for f in moved] == [("entities/sales.toml", "R", "entities/orders.toml")]


def test_diffs_of_an_untracked_binary_and_of_one_commit_without_another(repo):  # noqa: F811
    (repo.root / "blob.bin").write_bytes(b"\0\1\2")
    assert repo.diff("blob.bin")["binary"] is True
    with pytest.raises(GitError, match="Choose the commit to compare with"):
        repo.diff("entities/orders.toml", base="HEAD")


# ---- branches, stashes and merges


def test_a_branch_starts_where_its_asked_to(repo):  # noqa: F811
    head = repo.head()
    write(repo.root, "more.txt", "x\n")
    repo.stage_all()
    repo.commit("More")
    branches = repo.create_branch("from-first", start=head, switch=False)
    assert next(b for b in branches["local"] if b["name"] == "from-first")["commit"] == head


def test_switching_to_a_branch_that_isnt_there_or_is_already_current(repo):  # noqa: F811
    with pytest.raises(GitError, match="no branch called nowhere"):
        repo.switch("nowhere")
    assert repo.switch("main")["stashed"] is False


def test_a_remote_branch_with_a_local_one_of_its_name_isnt_switched_to(repo):  # noqa: F811
    raw(repo.root, "update-ref", "refs/remotes/origin/feature", repo.head())
    repo.create_branch("feature", switch=False)
    with pytest.raises(GitError, match="A local branch called feature already exists"):
        repo.switch("origin/feature")


def test_deleting_a_branch_that_isnt_there_and_put_aside_changes_by_name(repo):  # noqa: F811
    with pytest.raises(GitError, match="no local branch called nowhere"):
        repo.delete_branch("nowhere")
    with pytest.raises(GitError, match="Choose a set of put-aside changes"):
        repo.restore_stash("HEAD")


# ---- notebooks and conflicts as text


def test_notebook_text_of_what_isnt_a_notebook():
    assert notebook_text(b"not json") is None
    assert notebook_text(b"[1, 2]") is None
    text = notebook_text(json.dumps({"cells": ["junk", {"cell_type": "markdown", "source": ["# Hi"]}]}).encode())
    assert text == "# ---- cell 2 (markdown) ----\n# Hi\n"


def test_a_conflict_in_a_binary_or_missing_file_shows_no_lines():
    assert conflict_view(None)["binary"] is False
    assert conflict_view(b"\0bin")["binary"] is True
    shown = conflict_view(b"<<<<<<< HEAD\na\n||||||| base\nb\n=======\nc\n>>>>>>> theirs\n")
    assert shown["conflicts"] == 1


def test_repository_opens_from_a_subfolder(repo):  # noqa: F811
    assert Repository(repo.root / "entities").root == repo.root
