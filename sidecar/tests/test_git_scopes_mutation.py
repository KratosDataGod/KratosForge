"""Kinds of repository and where notebooks find their data: pins what mutation testing (batch 33,
docs/quality/mutation-testing.md) showed no test held. The checked kind returned, the message when
a stated kind would change, and how locations.toml is read and limited."""

from __future__ import annotations

import shutil

import pytest

from fabric_desktop.git import scopes
from fabric_desktop.git.cli import GitError
from fabric_desktop.git.repo import init

from test_git import git_home, write  # noqa: F401  (git_home is an autouse fixture)


@pytest.fixture(autouse=True)
def _own_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))


def test_check_kind_returns_the_kind():
    assert scopes.check_kind("capacity") == "capacity"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_changing_a_stated_kind_names_both_kinds(tmp_path):
    repo = init(tmp_path / "proj")
    scopes.set_kind(repo, "notebooks")
    with pytest.raises(GitError) as e:
        scopes.set_kind(repo, "capacity")
    assert e.value.kind == "confirm"
    assert str(e.value) == "This repository says it holds notebooks. Confirm to change it to capacity."


def test_an_unreadable_locations_file_exists_and_says_why(tmp_path):
    write(tmp_path, scopes.LOCATIONS, "[dev\n")
    out = scopes.read_locations(tmp_path)
    assert out["exists"] is True and out["problem"].startswith("locations.toml can't be read: ")
    assert out["envs"] == {"dev": None, "test": None, "prod": None}


def test_a_readable_locations_file_exists_with_no_problem(tmp_path):
    write(tmp_path, scopes.LOCATIONS, '[dev]\nruns_on = "this_pc"\n')
    out = scopes.read_locations(tmp_path)
    assert out["exists"] is True and out["problem"] is None
    assert scopes.read_locations(tmp_path / "missing")["exists"] is False


def test_runs_on_this_pc_wins_over_a_workspace_and_attaches_no_lakehouse(tmp_path):
    write(tmp_path, scopes.LOCATIONS, '[test]\nruns_on = "this_pc"\nworkspace_id = "w"\nlakehouse_id = "l"\n'
                                      '[prod]\nworkspace_id = "w"\n')
    envs = scopes.read_locations(tmp_path)["envs"]
    assert envs["test"]["this_pc"] is True and envs["test"]["lakehouse"] is None
    # A workspace with no lakehouse is in Fabric, but there's still no lakehouse to attach.
    assert envs["prod"]["this_pc"] is False and envs["prod"]["lakehouse"] is None


def test_a_location_field_may_be_256_characters_and_no_more(tmp_path):
    ok = scopes.write_locations(tmp_path, {"dev": {"workspace_id": "w", "workspace_name": "n" * 256}})
    assert ok["envs"]["dev"]["workspace_name"] == "n" * 256
    with pytest.raises(GitError) as e:
        scopes.write_locations(tmp_path, {"dev": {"workspace_id": "w", "workspace_name": "n" * 257}})
    assert e.value.kind == "invalid" and str(e.value) == "The dev workspace name isn't valid."
