"""Source control's service (git_service.py): what mutation testing (batch 19,
docs/quality/mutation-testing.md) showed no test pinned. The defaults each call passes on, the
list of repositories this PC opened, the platform read and deploy refusals, and the note a deploy
leaves. Real temporary repositories and the local platform only: nothing reaches a remote or Fabric."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.central.core import PlatformError
from fabric_desktop.git.cli import GitError
from fabric_desktop.git_service import MAX_REPOS, _newer_op
from tests.test_git import Svc, first_commit, git_home, raw, repo, write  # noqa: F401 - fixtures


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata-env"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def gsvc(tmp_path, repo):  # noqa: F811
    s = Svc(tmp_path / "appdata")
    s.git_open(str(repo.root))
    return s


# ---- the list of repositories this PC opened


def test_the_list_keeps_the_newest_fifty(tmp_path, repo):  # noqa: F811
    s = Svc(tmp_path / "appdata")
    s.root.mkdir(parents=True)
    old = [{"path": str(tmp_path / f"old{i}"), "name": f"old{i}", "opened_at": "2026-01-01T00:00:00+00:00"}
           for i in range(MAX_REPOS)]
    s.git_repos_file.write_text(json.dumps({"repos": old, "current": old[0]["path"]}), encoding="utf-8")
    s.git_open(str(repo.root))
    saved = json.loads(s.git_repos_file.read_text(encoding="utf-8"))["repos"]
    assert MAX_REPOS == 50 and len(saved) == 50
    assert saved[0]["path"] == str(repo.root) and saved[-1]["name"] == "old48"


def test_a_notebooks_place_uses_the_saved_name_or_else_the_folder(tmp_path):
    s = Svc(tmp_path / "appdata")
    named = s.git_create(str(tmp_path / "nb-one"), "notebooks")
    s.git_create(str(tmp_path / "nb-two"), "notebooks")
    assert named["branch"] == "main"  # git_create hands back the new repository's status
    data = json.loads(s.git_repos_file.read_text(encoding="utf-8"))
    for r in data["repos"]:
        if r["name"] == "nb-one":
            r["name"] = "Team notebooks"
        else:
            del r["name"]
    s.git_repos_file.write_text(json.dumps(data), encoding="utf-8")
    places = {name for _, name, _ in s._notebook_places()}
    assert places == {"Team notebooks", "nb-two"}


def test_git_ops_lists_one_repository_or_all(gsvc, repo):  # noqa: F811
    asked = []

    class FakeOps:
        def list(self, folder):
            asked.append(folder)
            return []

    gsvc._git_ops_obj = FakeOps()
    gsvc.git_ops(str(repo.root))
    gsvc.git_ops()
    assert asked == [str(repo.root), None]


def test_init_hands_back_the_new_repositorys_status(tmp_path):
    s = Svc(tmp_path / "appdata")
    (tmp_path / "plain").mkdir()
    st = s.git_init(str(tmp_path / "plain"), "trunk")
    assert st["branch"] == "trunk"


# ---- the defaults each call passes on


def test_changing_what_a_repository_holds_needs_confirm_by_default(gsvc, repo):  # noqa: F811
    root = str(repo.root)
    gsvc.git_set_kind(root, "project")
    with pytest.raises(GitError) as e:
        gsvc.git_set_kind(root, "notebooks")
    assert e.value.kind == "confirm"


def test_a_notebook_diff_compares_code_by_default(gsvc, repo):  # noqa: F811
    root = str(repo.root)
    nb = {"cells": [{"cell_type": "code", "source": ["x = 1\n"], "metadata": {}, "outputs": [],
                     "execution_count": None}], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    write(repo.root, "nb.ipynb", json.dumps(nb, indent=1))
    raw(root, "add", "nb.ipynb")
    raw(root, "commit", "-q", "-m", "notebook")
    nb["cells"][0]["outputs"] = [{"output_type": "stream", "name": "stdout", "text": ["1\n"]}]
    nb["cells"][0]["execution_count"] = 1
    write(repo.root, "nb.ipynb", json.dumps(nb, indent=1))
    assert gsvc.git_diff(root, "nb.ipynb")["added"] == 0  # only outputs changed: no code changed
    assert gsvc.git_diff(root, "nb.ipynb", notebook=False)["added"] > 0


def test_discarding_a_block_needs_confirm_by_default(gsvc, repo):  # noqa: F811
    root = str(repo.root)
    write(repo.root, "entities/orders.toml", "[load]\nmode = \"scd2\"\nkeys = [\"order_id\"]\n")
    hunk = gsvc.git_diff(root, "entities/orders.toml")["hunks"][0]["id"]
    with pytest.raises(GitError, match="Confirm"):
        gsvc.git_apply_hunk(root, "entities/orders.toml", hunk, "discard")
    assert "scd2" in (repo.root / "entities/orders.toml").read_text(encoding="utf-8")


def test_the_log_shows_fifty_commits_by_default(gsvc, repo):  # noqa: F811
    root = str(repo.root)
    for i in range(50):
        raw(root, "commit", "-q", "--allow-empty", "-m", f"c{i}")
    log = gsvc.git_log(root)
    assert len(log["commits"]) == 50 and log["more"] is True


def test_a_new_branch_is_switched_to_by_default(gsvc, repo):  # noqa: F811
    root = str(repo.root)
    gsvc.git_create_branch(root, "feature")
    assert gsvc.git_status(root)["branch"] == "feature"


def test_deleting_a_branch_needs_confirm_by_default(gsvc, repo):  # noqa: F811
    root = str(repo.root)
    gsvc.git_create_branch(root, "other", switch=False)
    with pytest.raises(GitError, match="This deletes the branch other"):
        gsvc.git_delete_branch(root, "other")
    assert "other" in {b["name"] for b in gsvc.git_branches(root)["local"]}


# ---- a platform read that came with an update


class _Cat:
    def __init__(self, exc):
        self.exc = exc

    def call(self, op, **kwargs):
        raise self.exc


def test_a_platform_error_about_another_operation_is_not_an_older_platform():
    with pytest.raises(PlatformError, match="list_other not found"):
        _newer_op(_Cat(PlatformError("list_other not found")), "list_landing_zones")
    with pytest.raises(PlatformError, match="timed out"):
        _newer_op(_Cat(PlatformError("list_landing_zones timed out")), "list_landing_zones")
    with pytest.raises(GitError, match="older than this app"):
        _newer_op(_Cat(PlatformError("list_landing_zones: no operation by that name")), "list_landing_zones")


def test_open_hands_back_the_repositorys_status(tmp_path, repo):  # noqa: F811
    s = Svc(tmp_path / "appdata")
    st = s.git_open(str(repo.root / "entities"))
    assert st["branch"] == "main" and st["root"] == str(repo.root)


def test_a_branch_with_commits_no_other_holds_is_kept_without_force(gsvc, repo):  # noqa: F811
    root = str(repo.root)
    gsvc.git_create_branch(root, "work")
    raw(root, "commit", "-q", "--allow-empty", "-m", "only on work")
    gsvc.git_switch(root, "main")
    with pytest.raises(GitError):
        gsvc.git_delete_branch(root, "work", confirm=True)
    assert "work" in {b["name"] for b in gsvc.git_branches(root)["local"]}
    gsvc.git_delete_branch(root, "work", confirm=True, force=True)
    assert "work" not in {b["name"] for b in gsvc.git_branches(root)["local"]}
