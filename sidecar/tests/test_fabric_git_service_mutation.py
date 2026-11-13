"""Mutation batch 6: the gates on Update from Git and the reads around it (fabric_git_service.py).

Reuses test_fabric_git's stand-in Fabric and its temporary repository (git_home applies here too)."""

from __future__ import annotations

import pytest

from fabric_desktop import artifacts, fabric_git
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.git.cli import GitError
from tests.test_fabric_git import (  # noqa: F401 - fixtures used by name
    GITHUB, H1, H2, STATUS, WS, ScopedSvc, UpdateFabric, fp, git_home, repo, ssvc, svc, usvc,
)


def _break_links(root):
    (root / fabric_git.LINK_FILE).write_text("not = [valid", encoding="utf-8")


def test_item_support_gives_the_whole_registry_or_one_type(svc):
    assert svc.fabric_item_support() == artifacts.registry()
    one = svc.fabric_item_support("Notebook")
    assert one == {"checked": artifacts.SOURCES_CHECKED, "item": artifacts.capability("Notebook").as_dict()}


def test_overview_with_a_link_says_it_is_linked(svc, repo):
    svc.fabric_git_link(str(repo.root), "dev", WS)
    out = svc.fabric_git_overview(str(repo.root), "dev")
    assert out["linked"] is True and out["problem"] is None


def test_status_with_a_broken_link_file_names_its_problem(ssvc, repo):
    _break_links(repo.root)
    with pytest.raises(GitError, match="can't be read"):
        ssvc.fabric_git_status(str(repo.root), "dev")
    assert ssvc.fabric.status_calls == []


def test_status_passes_on_any_other_fabric_error(ssvc, repo):
    ssvc.fabric_git_link(str(repo.root), "dev", WS)
    ssvc.fabric.status_answer = FabricError("throttled", "TooManyRequests", 429)
    with pytest.raises(FabricError, match="throttled"):
        ssvc.fabric_git_status(str(repo.root), "dev")


def test_update_with_a_broken_link_file_names_its_problem(usvc, repo):
    _break_links(repo.root)
    with pytest.raises(GitError, match="can't be read"):
        usvc.fabric_git_update(str(repo.root), "dev", fp())
    assert usvc.fabric.updates == []


def test_update_without_a_link_for_that_environment_says_to_link_it(usvc, repo):
    with pytest.raises(GitError, match="Link this repository to a Test workspace first"):
        usvc.fabric_git_update(str(repo.root), "test", fp())
    assert usvc.fabric.updates == []


def test_every_gate_answer_says_nothing_was_done(usvc, repo):
    r = str(repo.root)
    answers = [usvc.fabric_git_update(r, "dev", "old"),
               usvc.fabric_git_update(r, "dev", fp()),
               usvc.fabric_git_update(r, "dev", fp(), "PreferRemote"),
               usvc.fabric_git_update(r, "dev", fp(), "PreferRemote", "wrong", True)]
    assert [a["done"] for a in answers] == [False] * 4
    assert usvc.fabric.updates == []


def test_nothing_to_update_writes_nothing_and_says_so(tmp_path, repo):
    s = ScopedSvc(tmp_path / "app", UpdateFabric({WS: GITHUB}, {"workspaceHead": H1, "remoteCommitHash": H2,
                                                                "changes": []}))
    s.root.mkdir()
    s.git_open(str(repo.root))
    s.fabric_git_link(str(repo.root), "dev", WS, "Sales Dev")
    fingerprint = fabric_git.status(s.fabric.status_answer)["fingerprint"]
    out = s.fabric_git_update(str(repo.root), "dev", fingerprint, "PreferRemote", "Sales Dev", True)
    assert out["nothing"] is True and out["done"] is False
    assert s.fabric.updates == []


def test_overview_when_fabric_refuses_the_read_is_still_linked_and_says_why(svc, repo):
    svc.fabric_git_link(str(repo.root), "dev", WS)
    svc.fabric.answers[WS] = FabricError("no access", "Forbidden", 403)
    out = svc.fabric_git_overview(str(repo.root), "dev")
    assert out["linked"] is True and out["connection"] is None and "no access" in out["problem"]
