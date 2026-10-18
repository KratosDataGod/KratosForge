"""Kinds of repository: each kind of definition (notebooks, data mesh projects, platform,
capacity) in a repository of its own, saying so in fabric-desktop.repo.toml."""

from __future__ import annotations

import shutil
import tomllib

import pytest

from fabric_desktop.git import scopes
from fabric_desktop.git.cli import GitError
from fabric_desktop.git.repo import init
from fabric_desktop.git_service import GitMixin

from test_git import Svc, git_home, nb, write  # noqa: F401  (git_home is an autouse fixture)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.mark.parametrize("kind", list(scopes.KINDS))
def test_a_new_repository_starts_with_its_kinds_layout_and_commits_nothing(tmp_path, kind):
    repo = scopes.create(tmp_path / kind, kind)
    assert scopes.read_kind(repo.root) == kind
    assert tomllib.loads((repo.root / scopes.MARKER).read_text())["kind"] == kind
    assert ".local/" in (repo.root / ".gitignore").read_text()
    st = repo.status()
    assert st["commit"] is None and scopes.MARKER in [e["path"] for e in st["untracked"]]
    if kind == "notebooks":
        assert (repo.root / "notebooks").is_dir() and "[dev]" in (repo.root / "locations.toml").read_text()
    if kind == "platform":
        assert all((repo.root / d).is_dir() for d in ("domains", "sources", "key_tables", "landing_zones", "policies"))
    for text in [p.read_text() for p in repo.root.rglob("*.toml")]:
        tomllib.loads(text)  # every starting file is valid TOML


def test_create_refuses_unknown_kinds_and_folders_with_files(tmp_path):
    with pytest.raises(GitError):
        scopes.create(tmp_path / "x", "everything")
    write(tmp_path / "full", "keep.txt", "mine\n")
    with pytest.raises(GitError) as e:
        scopes.create(tmp_path / "full", "notebooks")
    assert e.value.kind == "exists" and not (tmp_path / "full" / ".git").exists()


def test_an_existing_repository_only_gets_the_marker_and_changing_it_asks(tmp_path):
    repo = init(tmp_path / "proj")
    write(repo.root, "environments.toml", "[dev]\n")
    assert scopes.describe(repo.root) == {"kind": None, "suggested": "project"}
    before = sorted(p.name for p in repo.root.iterdir())
    assert scopes.set_kind(repo, "project")["changed"]
    assert sorted(p.name for p in repo.root.iterdir()) == sorted(before + [scopes.MARKER])
    assert scopes.set_kind(repo, "project")["changed"] is False
    with pytest.raises(GitError) as e:
        scopes.set_kind(repo, "platform")
    assert e.value.kind == "confirm" and scopes.read_kind(repo.root) == "project"
    scopes.set_kind(repo, "platform", confirm=True)
    assert scopes.read_kind(repo.root) == "platform"


def test_a_bad_marker_is_no_kind(tmp_path):
    write(tmp_path, scopes.MARKER, "kind = [not toml")
    assert scopes.read_kind(tmp_path) is None
    write(tmp_path, scopes.MARKER, 'kind = "everything"\n')
    assert scopes.read_kind(tmp_path) is None


def test_mixing_scopes_is_named(tmp_path):
    assert scopes.wrong_place("platform", "notebooks").startswith("This repository holds platform")
    assert scopes.wrong_place("notebooks", "notebooks") is None
    assert scopes.wrong_place(None, "notebooks") is None


def test_service_lists_kinds_and_warns_when_notebooks_sit_in_another_kind(tmp_path):
    svc = Svc(tmp_path / "appdata")
    svc.git_create(str(tmp_path / "plat"), "platform")
    listed = svc.git_repos()
    assert listed["repos"][0]["kind"] == "platform" and set(listed["kinds"]) == set(scopes.KINDS)
    nbdir = svc.notebooks.folder
    write(nbdir, "Sales.ipynb", nb([]))
    svc.git_init(str(nbdir), kind="notebooks")
    info = svc.git_notebook_info("Sales")
    assert info["kind"] == "notebooks" and info["wrong_place"] is None
    out = svc.git_set_kind(str(nbdir), "capacity", confirm=True)
    assert out["changed"] and svc.git_notebook_info("Sales")["wrong_place"]
    assert (nbdir / "Sales.ipynb").exists()  # the notebooks folder's own files are untouched
