"""A Git repository on this PC: the edges the main tests don't reach. Folders that aren't there,
paths that name nothing or .git, blocks that can't be applied, a staged file Git can't show, a
version without the file, an odd branch listing, a stash that doesn't take every change, and long
conflict files. Git runs on a copied local repository; nothing reaches a remote."""

from __future__ import annotations

import pytest

from fabric_desktop.git import cli, repo as repo_mod
from fabric_desktop.git.cli import GitError
from tests.test_git import first_commit, git_home, repo, write  # noqa: F401  (fixtures)


def test_a_folder_that_isnt_there_is_named(tmp_path):
    with pytest.raises(GitError, match="doesn't exist") as raised:
        repo_mod._folder(tmp_path / "nowhere")
    assert raised.value.kind == "invalid"
    assert repo_mod._folder(tmp_path / "nowhere", must_exist=False) == (tmp_path / "nowhere").resolve()


@pytest.mark.parametrize("path", ["entities/..", ".git/config", ".GIT/HEAD"])
def test_a_path_that_names_nothing_or_git_s_own_folder_is_refused(repo, path):
    with pytest.raises(GitError, match="can't be changed from here"):
        repo.rel(path)


def test_a_block_action_that_isnt_one_is_refused(repo):
    with pytest.raises(GitError, match="stage, unstage or discard") as raised:
        repo.apply_hunk("entities/orders.toml", "h1", "delete")
    assert raised.value.kind == "invalid"


def test_a_file_git_doesnt_track_has_no_blocks(repo):
    write(repo.root, "notes.txt", "new\n")
    with pytest.raises(GitError, match="doesn't track this file yet"):
        repo.apply_hunk("notes.txt", "h1", "stage")


def test_a_staged_file_git_cant_show_is_skipped_in_the_secret_scan(repo, monkeypatch):
    write(repo.root, "a.txt", "AKIA" + "ABCDEFGHIJKLMNOP\n")
    write(repo.root, "b.txt", "nothing secret\n")
    repo.stage(["a.txt", "b.txt"])
    real = cli.run

    def run(cwd, args, **kw):
        if args[:2] == ["show", ":a.txt"]:
            raise GitError("fatal: path 'a.txt' exists on disk, but not in the index")
        return real(cwd, args, **kw)

    monkeypatch.setattr(cli, "run", run)
    assert repo.scan_staged() == []
    monkeypatch.setattr(cli, "run", real)
    assert [f["path"] for f in repo.scan_staged()] == ["a.txt"]


def test_a_side_that_isnt_there_reads_as_none(repo):
    assert repo._side(None) is None
    assert repo._side("HEAD:no/such/file.txt") is None
    assert repo._side("worktree", "no/such/file.txt") is None
    assert repo._side("HEAD:entities/orders.toml").startswith(b"[load]")


def test_a_branch_line_git_cut_short_is_skipped(repo, monkeypatch):
    real = repo.git

    def git(*args, **kw):
        out = real(*args, **kw)
        return out + "refs/heads/broken\0broken\n" if args and args[0] == "for-each-ref" else out

    monkeypatch.setattr(repo, "git", git)
    names = [b["name"] for b in repo.branches()["local"]]
    assert "main" in names and "broken" not in names


def test_a_stash_that_leaves_changes_behind_puts_everything_back(repo, monkeypatch):
    repo.create_branch("other", switch=False)
    write(repo.root, "entities/orders.toml", "[load]\nmode = \"append\"\n")
    real_status, calls = repo.status, []

    def status():
        calls.append(1)
        st = real_status()
        return st | {"dirty": True} if len(calls) == 2 else st  # the check right after the stash

    monkeypatch.setattr(repo, "status", status)
    with pytest.raises(GitError, match="couldn't put every change aside") as raised:
        repo.switch("other", mode="stash")
    assert raised.value.kind == "stash"
    monkeypatch.setattr(repo, "status", real_status)
    assert repo.branch() == "main" and repo.stashes() == []
    assert "append" in (repo.root / "entities/orders.toml").read_text(encoding="utf-8")


def test_no_file_on_a_side_is_empty_notebook_text():
    assert repo_mod.notebook_text(None) == ""


def test_a_long_conflict_file_is_cut_at_the_line_limit(monkeypatch):
    monkeypatch.setattr(repo_mod, "MAX_DIFF_LINES", 3)
    data = b"<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> origin/main\n"
    view = repo_mod.conflict_view(data)
    assert view["truncated"] is True and len(view["hunks"][0]["lines"]) == 3


def _conflict(repo):
    """entities/orders.toml changed on two branches, then merged: Git stops with a conflict."""
    repo.create_branch("other")
    write(repo.root, "entities/orders.toml", "[load]\nmode = \"append\"\n")
    repo.stage_all()
    repo.commit("Append on other")
    repo.switch("main")
    write(repo.root, "entities/orders.toml", "[load]\nmode = \"scd2\"\n")
    repo.stage_all()
    repo.commit("SCD2 on main")
    with pytest.raises(GitError):
        repo.git("merge", "other")
    assert repo.merging()


def test_a_file_with_conflicts_cant_be_discarded_or_staged_in_blocks(repo):
    _conflict(repo)
    with pytest.raises(GitError, match="before discarding a file with conflicts") as raised:
        repo.discard(["entities/orders.toml"], confirm=True)
    assert raised.value.kind == "conflict"
    with pytest.raises(GitError, match="Fix the conflicts in this file first") as raised:
        repo.apply_hunk("entities/orders.toml", "h1", "stage")
    assert raised.value.kind == "conflict"


def test_a_file_that_isnt_text_has_no_blocks(repo):
    (repo.root / "logo.bin").write_bytes(b"\x89PNG\0\0\x01")
    repo.stage(["logo.bin"])
    repo.commit("Logo")
    (repo.root / "logo.bin").write_bytes(b"\x89PNG\0\0\x02")
    with pytest.raises(GitError, match="isn't text"):
        repo.apply_hunk("logo.bin", "h1", "stage")


@pytest.mark.skipif(repo_mod.os.name == "nt", reason="making a symlink on Windows needs Developer Mode or admin")
def test_a_path_through_a_link_that_leaves_the_repository_is_refused(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo.root / "shortcut").symlink_to(outside, target_is_directory=True)
    with pytest.raises(GitError, match="isn't in this repository"):
        repo.rel("shortcut/secret.txt")
