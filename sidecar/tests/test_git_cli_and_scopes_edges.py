"""Running Git and reading a repository's kind and locations: the edges the main tests don't reach.
subprocess is replaced where Git would run; nothing reaches a remote."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop.git import cli, scopes
from fabric_desktop.git.cli import GitError


def test_no_git_on_this_pc(monkeypatch):
    monkeypatch.setattr(cli, "git_path", lambda: None)
    assert cli.version() is None
    with pytest.raises(GitError) as raised:
        cli.command(None, ["status"])
    assert raised.value.kind == "no_git" and "Install Git for Windows" in str(raised.value)


@pytest.mark.parametrize("error", [OSError("blocked"), subprocess.TimeoutExpired(["git"], 10)])
def test_a_git_that_wont_say_its_version_has_none(monkeypatch, error):
    monkeypatch.setattr(cli, "git_path", lambda: "/usr/bin/git")

    def run(*a, **k):
        raise error

    monkeypatch.setattr(cli.subprocess, "run", run)
    assert cli.version() is None


def test_a_version_git_prints_oddly_reads_as_none(monkeypatch):
    monkeypatch.setattr(cli, "git_path", lambda: "/usr/bin/git")
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"git version unknown"))
    assert cli.version() is None
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"git version 2.47.1.windows.1"))
    assert cli.version() == "2.47.1"


@pytest.mark.parametrize("arg", [b"bytes", "nul\0inside", 3])
def test_an_argument_that_isnt_plain_text_is_refused(monkeypatch, arg):
    monkeypatch.setattr(cli, "git_path", lambda: "/usr/bin/git")
    with pytest.raises(GitError) as raised:
        cli.command("/repo", ["log", arg])
    assert raised.value.kind == "invalid"


def test_a_git_that_runs_too_long_is_stopped(monkeypatch):
    monkeypatch.setattr(cli, "git_path", lambda: "/usr/bin/git")

    def run(*a, **k):
        raise subprocess.TimeoutExpired(a[0], k["timeout"])

    monkeypatch.setattr(cli.subprocess, "run", run)
    with pytest.raises(GitError) as raised:
        cli.run("/repo", ["fetch"], timeout=5)
    assert raised.value.kind == "timeout" and "5 seconds" in str(raised.value)


def test_a_git_that_cant_start_says_so(monkeypatch):
    monkeypatch.setattr(cli, "git_path", lambda: "/usr/bin/git")

    def run(*a, **k):
        raise PermissionError("Access is denied")

    monkeypatch.setattr(cli.subprocess, "run", run)
    with pytest.raises(GitError) as raised:
        cli.run("/repo", ["status"])
    assert raised.value.kind == "no_git" and "Access is denied" in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


@pytest.mark.parametrize("marker, kind", [("platform.toml", "platform"), ("capacity.toml", "capacity")])
def test_a_folder_s_kind_is_guessed_from_its_files(tmp_path, marker, kind):
    (tmp_path / marker).write_text("", encoding="utf-8")
    assert scopes.guess_kind(tmp_path) == kind


def test_notebooks_in_a_notebooks_folder_make_a_notebooks_repository(tmp_path):
    assert scopes.guess_kind(tmp_path) is None
    (tmp_path / "notebooks").mkdir()
    (tmp_path / "notebooks" / "load.ipynb").write_text("{}", encoding="utf-8")
    assert scopes.guess_kind(tmp_path) == "notebooks"


def test_a_folder_that_cant_be_listed_has_no_guess(tmp_path, monkeypatch):
    def glob(self, pattern):
        raise PermissionError(self)

    monkeypatch.setattr(Path, "glob", glob)
    assert scopes.guess_kind(tmp_path) is None


def test_no_locations_file_means_no_locations_yet(tmp_path):
    assert scopes.read_locations(tmp_path) == {"envs": {"dev": None, "test": None, "prod": None},
                                               "problem": None, "exists": False}


def test_a_location_that_isnt_a_table_is_refused(tmp_path):
    with pytest.raises(GitError) as raised:
        scopes.write_locations(tmp_path, {"dev": {"this_pc": True}, "test": "ws-1"})
    assert raised.value.kind == "invalid" and "test location" in str(raised.value)
    assert not (tmp_path / scopes.LOCATIONS).exists()
