"""Mutation batch 119: prod_writes_service.py (docs/quality/mutation-testing.md). The survivors were the
time limits and the cap on remembered workspaces as numbers, the moments exactly at a limit, a project's
keys workspace, what is remembered and when the file is written, and the plain False of "not marked"."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import prod_writes_service
from fabric_desktop.prod_writes_service import ProdWritesMixin


class _Project:
    def __init__(self, prod):
        self.environments = {"prod": prod}


class _Broken:
    @property
    def environments(self):
        raise RuntimeError("the project file is torn")


class Svc(ProdWritesMixin):
    def __init__(self, root, project=None, error=LookupError):
        self.root, self.project, self.error = root, project, error

    def _project(self):
        if self.project is None:
            raise self.error("No metadata project is open.")
        return self.project


def _remembered(tmp_path):
    return json.loads((tmp_path / "prod_workspaces.json").read_text())["prod"]


def test_the_limits_are_two_minutes_ten_minutes_and_500_workspaces():
    assert prod_writes_service.ALLOW_SECONDS == 120
    assert prod_writes_service.PENDING_SECONDS == 600
    assert prod_writes_service.MAX_REMEMBERED == 500


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(prod_writes_service.time, "monotonic", lambda: now[0])
    return now


def test_an_allowance_holds_until_exactly_120_seconds(tmp_path, clock):
    (tmp_path / "prod_workspaces.json").write_text(json.dumps({"prod": ["ws-prod"]}))
    svc = Svc(tmp_path)
    for wait, allowed in ((120.0, {"ws-prod"}), (120.5, set())):
        svc._prod_write_refusal("merge", "ws-prod", "Prod", "dbo.t", "nb")
        svc.notebook_prod_confirm("nb", "ws-prod", "Prod")
        clock[0] += wait
        assert svc._take_prod_allowance("nb") == frozenset(allowed)


def test_a_refusal_can_be_confirmed_until_exactly_600_seconds(tmp_path, clock):
    (tmp_path / "prod_workspaces.json").write_text(json.dumps({"prod": ["ws-prod"]}))
    svc = Svc(tmp_path)
    svc._prod_write_refusal("merge", "ws-prod", "Prod", "dbo.t", "nb")
    clock[0] += 600.0
    assert svc.notebook_prod_pending("nb")["table"] == "dbo.t"
    assert svc.notebook_prod_confirm("nb", "ws-prod", "Prod")["allowed"] is True
    svc._prod_write_refusal("merge", "ws-prod", "Prod", "dbo.t", "nb")
    clock[0] += 600.5
    assert svc.notebook_prod_pending("nb") is None
    with pytest.raises(ValueError, match="Nothing in this notebook"):
        svc.notebook_prod_confirm("nb", "ws-prod", "Prod")


def test_a_projects_keys_workspace_is_marked_prod(tmp_path):
    svc = Svc(tmp_path, _Project({"keys_workspace_id": "WS-Keys"}))
    assert svc._prod_marked("ws-keys") is True
    assert _remembered(tmp_path) == ["ws-keys"]


def test_new_ids_are_added_after_the_ones_remembered_and_only_500_are_kept(tmp_path):
    old = [f"old-{i:03d}" for i in range(500)]
    (tmp_path / "prod_workspaces.json").write_text(json.dumps({"prod": old}))
    svc = Svc(tmp_path, _Project({"workspace_id": "new-ws"}))
    assert svc._prod_marked("new-ws") is True
    assert _remembered(tmp_path) == old[1:] + ["new-ws"]


def test_ids_already_remembered_are_not_written_again(tmp_path, monkeypatch):
    (tmp_path / "prod_workspaces.json").write_text(json.dumps({"prod": ["ws-a", "ws-b"]}))
    writes = []
    monkeypatch.setattr(prod_writes_service.jsonfile, "write_json", lambda *a, **k: writes.append(a))
    svc = Svc(tmp_path, _Project({"workspace_id": "ws-b", "workspaces": {"gold": {"id": "ws-a"}}}))
    assert svc._remember_prod_workspaces(svc.project) == {"ws-a", "ws-b"}
    assert writes == []


def test_only_non_empty_text_ids_are_remembered(tmp_path):
    (tmp_path / "prod_workspaces.json").write_text(json.dumps({"prod": ["WS-A", 5, "", None]}))
    assert Svc(tmp_path)._remembered_prod() == ["ws-a"]


def test_not_marked_is_false(tmp_path):
    assert Svc(tmp_path)._prod_marked("") is False
    assert Svc(tmp_path)._prod_marked("ws-x") is False  # no project open
    assert Svc(tmp_path, error=RuntimeError)._prod_marked("ws-x") is False  # the project can't be read
    assert Svc(tmp_path, _Broken())._prod_marked("ws-x") is False  # its environments can't be read
    assert Svc(tmp_path, _Broken())._remember_prod_workspaces(_Broken()) == set()
