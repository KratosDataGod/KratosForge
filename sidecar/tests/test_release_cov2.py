"""The release record's parts (meta/release.py) where things are missing: a package that isn't installed, a
process with no login name, a stored receipt that isn't one, and files read as Git's last commit has them."""

from __future__ import annotations

import importlib.metadata as md
import json
import subprocess

import pytest

from fabric_desktop import receipts
from fabric_desktop.git.cli import GitError
from fabric_desktop.meta import release


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    (root / "metadata").mkdir(parents=True)
    (root / "metadata" / "a.toml").write_bytes(b"name = 'a'\r\n")
    (root / "metadata" / "notes.md").write_bytes(b"notes\n")  # write_text would give CRLF on Windows
    (root / "metadata" / "blob.toml").write_bytes(b"\x00\x01\x02binary")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "config", "core.autocrlf", "false")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    return root


def test_a_package_that_is_not_installed_is_recorded_as_none(monkeypatch):
    real = md.version

    def version(name):
        if name == "polars":
            raise md.PackageNotFoundError(name)
        return real(name)

    monkeypatch.setattr(release.md, "version", version)
    got = release.engine()
    assert got["packages"]["polars"] is None and got["packages"]["duckdb"] == real("duckdb")
    assert len(got["lock_hash"]) == 64


def test_a_process_with_no_login_name_records_no_user(monkeypatch):
    def no_login():
        raise OSError("no login name")

    monkeypatch.setattr(release.getpass, "getuser", no_login)
    assert release._user() is None
    assert release._identity(release._user(), "oid-1") == {"user": None, "object_id": "oid-1"}


def test_a_receipt_is_verified_only_when_it_still_hashes_to_its_id():
    body = receipts.new_receipt("release", project={"name": "crm"})
    text = receipts.canonical(body).decode("utf-8")
    assert release.verified(text, receipts.receipt_id(body))
    assert not release.verified(text, "0" * 64)
    assert not release.verified("", receipts.receipt_id(body)) and not release.verified(None, None)
    assert not release.verified("{not json", "x")
    assert not release.verified(json.dumps([1, 2]), "x")


def test_committed_files_are_the_last_commits_text_files_with_the_suffix_asked_for(repo):
    (repo / "metadata" / "a.toml").write_text("changed since", encoding="utf-8")  # the commit's text, not the folder's
    assert release.committed(repo, "metadata", ".toml") == {"metadata/a.toml": "name = 'a'\n"}
    assert set(release.committed(repo, "metadata")) == {"metadata/a.toml", "metadata/notes.md"}  # binary left out


def test_committed_is_none_when_git_cannot_list_the_folder(repo, monkeypatch):
    real = release.cli.text

    def fails(root, args, **kwargs):
        if args[0] == "ls-tree":
            raise GitError("ls-tree failed")
        return real(root, args, **kwargs)

    monkeypatch.setattr(release.cli, "text", fails)
    assert release.committed(repo, "metadata") is None
    assert release.committed_file(repo / "metadata" / "notes.md") == "notes\n"  # the commit itself still reads


def test_a_file_outside_any_folder_or_git_has_no_committed_text(tmp_path, repo):
    assert release.committed_file(tmp_path / "missing" / "x.toml") is None
    assert release.committed_file(repo / "metadata" / "never-added.toml") is None
    assert release.committed_file(repo / "metadata" / "notes.md") == "notes\n"
