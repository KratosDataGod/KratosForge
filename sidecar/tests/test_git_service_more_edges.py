"""Source control's service: the saved list when it can't be read, the capacity read and write
edges, and the background calls handed to the operations runner. Real temporary repositories and
stand-ins for Fabric; nothing reaches a remote or a tenant."""

from __future__ import annotations

import pytest

from fabric_desktop import fabric_api
from fabric_desktop.auth import CAPACITY_SCOPES
from fabric_desktop.git import capacity_files
from fabric_desktop.git.cli import GitError
from fabric_desktop.git.repo import Repository
from tests.test_git import Svc, first_commit, git_home, repo, write  # noqa: F401 - fixtures


@pytest.fixture
def svc(tmp_path, repo):  # noqa: F811
    s = Svc(tmp_path / "appdata")
    s.git_open(str(repo.root))
    return s


def test_a_saved_list_that_cant_be_read_starts_empty(tmp_path):
    s = Svc(tmp_path / "appdata")
    s.git_repos_file.parent.mkdir(parents=True, exist_ok=True)
    s.git_repos_file.mkdir()  # a folder where the file should be: reading it fails
    assert s._git_saved() == {"repos": [], "current": None}


def test_capacities_are_read_with_the_capacity_scope_only(monkeypatch, tmp_path):
    asked, made = [], []

    class Client:
        def __init__(self, token):
            made.append(token)

        def list_capacities(self):
            return [{"id": "cap-1"}]

    class Auth:
        def _token(self, scopes):
            asked.append(scopes)
            return "tok"

    class Fabric:
        def list_workspaces(self):
            return [{"id": "w-1"}]

    monkeypatch.setattr(fabric_api, "FabricClient", Client)
    s = Svc(tmp_path / "appdata")
    s.auth, s.fabric = Auth(), Fabric()
    assert s._fabric_capacities() == ([{"id": "cap-1"}], [{"id": "w-1"}])
    assert made[0]() == "tok" and asked == [CAPACITY_SCOPES]


@pytest.fixture
def capacity(tmp_path, monkeypatch):
    s = Svc(tmp_path / "appdata")
    root = tmp_path / "capacity"
    s.git_create(str(root), "capacity")
    write(root.resolve(), "capacity.toml", '[capacities."F8 Dev"]\ncapacity_id = "cap-2"\nsku = "F8"\n')
    s.git_stage_all(str(root.resolve()))
    s.git_commit(str(root.resolve()), "Capacities")
    monkeypatch.setattr(s, "_fabric_capacities", lambda: ([], []), raising=False)
    return s, str(root.resolve())


def test_a_capacity_write_that_needs_a_yes_asks_and_any_other_refusal_is_raised(capacity, monkeypatch):
    s, root = capacity

    def ask(*a, **k):
        raise GitError("This takes out F8 Dev. Write anyway?", kind="confirm")

    monkeypatch.setattr(capacity_files, "write", ask)
    assert s.git_capacity_write(root) == {"needs_confirm": True, "message": "This takes out F8 Dev. Write anyway?"}

    def refuse(*a, **k):
        raise GitError("The file is locked.", kind="invalid")

    monkeypatch.setattr(capacity_files, "write", refuse)
    with pytest.raises(GitError, match="locked"):
        s.git_capacity_write(root)


def test_fetch_pull_push_and_cancel_go_to_the_operations_runner(svc, repo):  # noqa: F811
    calls = []

    class Ops:
        def fetch(self, r, remote):
            calls.append(("fetch", r.root, remote))
            return {"id": "op-1"}

        def pull(self, r, mode):
            calls.append(("pull", r.root, mode))
            return {"id": "op-2"}

        def push(self, r, remote):
            calls.append(("push", r.root, remote))
            return {"id": "op-3"}

        def cancel(self, id):
            calls.append(("cancel", id))
            return {"cancelled": True}

    svc._git_ops_obj = Ops()
    root = str(repo.root)
    assert svc.git_fetch(root, "origin") == {"id": "op-1"}
    assert svc.git_pull(root, "rebase") == {"id": "op-2"}
    assert svc.git_push(root) == {"id": "op-3"}
    assert svc.git_op_cancel("op-3") == {"cancelled": True}
    assert calls == [("fetch", repo.root, "origin"), ("pull", repo.root, "rebase"), ("push", repo.root, None),
                     ("cancel", "op-3")]


def test_a_commit_or_switch_refused_for_another_reason_is_raised(svc, repo, monkeypatch):  # noqa: F811
    def nope(*a, **k):
        raise GitError("Git is busy.", kind="busy")

    monkeypatch.setattr(Repository, "commit", nope)
    monkeypatch.setattr(Repository, "switch", nope)
    with pytest.raises(GitError, match="busy"):
        svc.git_commit(str(repo.root), "Message")
    with pytest.raises(GitError, match="busy"):
        svc.git_switch(str(repo.root), "main")
