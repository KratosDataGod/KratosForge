"""Mutation-testing follow-up for usage_service.py (docs/quality/mutation-testing.md, batch 70).

Each test pins a behaviour that a surviving mutant changed without any test noticing.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from fabric_desktop import usage_service as us
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return Service(root=tmp_path / "app")


@pytest.fixture
def seen(monkeypatch):
    """Stand in for the reconciler and keep what it was given."""
    got: dict = {}

    def fake(imports, runs, estimates, since, until, ws_names=None, now=None, capacity_envs=None):
        got.update(runs=runs, estimates=estimates, since=since, until=until, ws_names=ws_names,
                   capacity_envs=capacity_envs)
        return {"matched": {}}
    monkeypatch.setattr(us.reconcile_mod, "reconcile", fake)
    return got


def project(**envs):
    return SimpleNamespace(environments=envs)


def test_a_long_file_name_is_cut_to_200_characters(svc):
    name = "u" * 250 + ".csv"
    assert svc.usage_preview(name, "a,b\n1,2\n")["file_name"] == "u" * 200


def test_a_missing_file_name_reads_as_usage_csv(svc):
    assert svc.usage_preview("", "a,b\n1,2\n")["file_name"] == "usage.csv"


def test_workspace_names_need_both_an_id_and_a_name(svc, monkeypatch):
    monkeypatch.setattr(Service, "_project", lambda self: project())
    runs = [{"workspace_id": "w1", "workspace_name": "Sales"}, {"workspace_id": "w2"}, {"workspace_name": "Lost"},
            {"workspace_id": "w3", "workspace_name": ""}]
    assert svc._workspace_names(runs) == {"w1": "Sales"}


def test_without_a_metadata_project_the_runs_names_are_kept(svc, monkeypatch):
    def none(self):
        raise FileNotFoundError("no project")
    monkeypatch.setattr(Service, "_project", none)
    assert svc._workspace_names([{"workspace_id": "w1", "workspace_name": "Sales"}]) == {"w1": "Sales"}


def test_the_projects_environments_fill_in_names_the_runs_left_out(svc, monkeypatch):
    monkeypatch.setattr(Service, "_project", lambda self: project(
        dev={"workspace_id": "w1", "workspace_name": "Dev from project"},
        test={"workspace_id": "w2", "workspace_name": "Test"},
        prod={"workspace_id": "w3"},
        other={"workspace_id": "w4", "workspace_name": "Not an environment"}))
    names = svc._workspace_names([{"workspace_id": "w1", "workspace_name": "Sales Dev"}])
    assert names == {"w1": "Sales Dev", "w2": "Test"}  # a run's own name wins; a half-set environment adds nothing


def test_an_environment_with_a_name_but_no_id_adds_nothing(svc, monkeypatch):
    monkeypatch.setattr(Service, "_project", lambda self: project(dev={"workspace_name": "Dev"}))
    assert svc._workspace_names([]) == {}


def test_only_fabric_runs_with_a_run_id_give_estimates(svc, seen, monkeypatch):
    monkeypatch.setattr(Service, "_project", lambda self: project())
    rows = [{"where": "fabric", "fabric_job_cu": {"run_id": "r1", "cu": 1}},
            {"where": "fabric", "fabric_job_cu": {"cu": 2}},
            {"where": "local", "fabric_job_cu": {"run_id": "r3", "cu": 3}}]
    svc._usage_reconcile(7, [], rows, caps={})
    assert seen["estimates"] == {"r1": {"run_id": "r1", "cu": 1}}


def test_capacity_names_map_to_environments_and_nameless_ones_are_skipped(svc, seen, monkeypatch):
    monkeypatch.setattr(Service, "_project", lambda self: project())
    caps = {"environments": [{"env": "dev", "capacity": {"name": "F8-Shared"}},
                             {"env": "test", "capacity": {"name": "f8-shared"}},
                             {"env": "prod", "capacity": {"name": "Prod"}},
                             {"env": "x", "capacity": None}, {"env": "y", "capacity": {}},
                             {"env": "z", "capacity": {"name": ""}}]}
    out = svc._usage_reconcile(7, [], [], caps=caps)
    assert seen["capacity_envs"] == {"f8-shared": ["dev", "test"], "prod": ["prod"]}
    assert out["imports_list"] == []


def test_reconciliation_reads_30_days_of_up_to_500_cached_runs(svc, seen, monkeypatch):
    monkeypatch.setattr(Service, "_project", lambda self: project())
    asked = {}

    def runs(self, limit=50, refresh=True, job_ids=None):
        asked.update(limit=limit, refresh=refresh)
        return []
    monkeypatch.setattr(Service, "fabric_runs", runs)
    svc.usage_reconciliation()
    assert asked == {"limit": 500, "refresh": False}
    assert seen["until"] - seen["since"] == dt.timedelta(days=30)
    svc.usage_reconciliation(7)
    assert seen["until"] - seen["since"] == dt.timedelta(days=7)
