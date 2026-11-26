"""Mutation batch 274: releases and rollback in meta/promote.py, and promote's check that a later
environment is on the project's version. The store, the kept release files and the release receipts
are stand-ins, so each rule is checked on its own."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import polars as pl
import pytest

from fabric_desktop.meta.project import GateError

pm = importlib.import_module("fabric_desktop.meta.promote")  # meta's own promote() hides the module


class Store:
    def __init__(self, env, deployments=(), run_log=None):
        self.env = env
        self.rows = list(deployments)
        self.run_log = run_log if run_log is not None else pl.DataFrame(
            {"entity": [], "logic": [], "status": []}, schema={"entity": pl.Utf8, "logic": pl.Utf8, "status": pl.Utf8})
        self.appended = []

    def meta(self, name):
        if name == "deployments":
            return pl.DataFrame(self.rows) if self.rows else pl.DataFrame()
        return self.run_log

    def meta_where(self, name, predicate):
        return self.meta(name).filter(predicate) if self.rows else pl.DataFrame()

    def meta_append(self, name, rows):
        self.appended.append((name, rows))


@pytest.fixture
def kept(monkeypatch):
    """Release files kept for these versions; rollback "restores" by naming the version."""
    versions: set[str] = set()
    monkeypatch.setattr(pm, "snapshot", SimpleNamespace(versions=lambda target: set(versions),
                                                        restore=lambda project, target, v: f"files of {v}"))
    monkeypatch.setattr(pm, "release_mod", SimpleNamespace(
        verified=lambda receipt, rid: receipt == "good",
        rollback_record=lambda released, env, version, current, earlier, by, object_id: {"released": released},
        deployment_columns=lambda body: {"receipt_id": "r-" + body["released"]}))
    return versions


def dep(env, version, **kw):
    return {"env": env, "version": version, "source_env": "dev", "at": "t", "commit": None, "branch": None,
            "dirty": None, "pushed": None, "by": None, "receipt_id": None, "receipt": None} | kw


def test_releases_are_this_environments_only_newest_first(kept):
    kept.update({"v1"})
    target = Store("test", [dep("test", "v1", receipt="good"), dep("prod", "v9"), dep("test", "v2")])
    out = pm.releases(target)
    assert [(r["version"], r["current"], r["kept"], r["verified"]) for r in out] == \
        [("v2", True, False, False), ("v1", False, True, True)]


def test_no_deployments_is_no_releases(kept):
    assert pm.releases(Store("test")) == []


def test_rollback_needs_something_promoted_first(kept):
    with pytest.raises(GateError) as e:
        pm.rollback(SimpleNamespace(), Store("test"), "v1")
    assert e.value.errors == ["Nothing has been promoted to test yet."]


def _errors(target, version):
    with pytest.raises(GateError) as e:
        pm.rollback(SimpleNamespace(), target, version)
    return e.value.errors


def test_a_rollback_to_the_current_version_or_one_never_released_is_refused(kept):
    kept.update({"v1", "v2"})
    target = Store("test", [dep("test", "v1"), dep("test", "v2")])
    assert _errors(target, "v2") == ["Test is already on v2."]
    assert _errors(target, "v7") == ["v7 was never released to Test."]
    assert target.appended == []


def test_a_rollback_to_a_release_whose_files_arent_kept_is_refused(kept):
    kept.update({"v2"})
    target = Store("test", [dep("test", "v1"), dep("test", "v2")])
    assert _errors(target, "v1") == [("Test doesn't keep the files of v1; it was promoted before releases kept "
                                      "them, so promote it again instead.")]


def test_a_rollback_records_a_release_naming_the_one_it_replaced(kept):
    kept.update({"v1", "v2"})
    target = Store("test", [dep("test", "v1"), dep("test", "v2")])
    out = pm.rollback(SimpleNamespace(), target, "v1", by="dana")
    assert out == {"env": "test", "version": "v1", "replaced": "v2", "receipt_id": "r-files of v1",
                   "project": "files of v1"}
    [(name, [row])] = target.appended
    assert name == "deployments" and row["version"] == "v1" and row["source_env"] == "test"


def test_a_later_environment_must_be_on_the_projects_version_first(monkeypatch):
    monkeypatch.setattr(pm, "validate", lambda project, today: [])
    project = SimpleNamespace(version="v2", entities={})
    on_v1 = Store("test", [dep("test", "v1")])
    assert pm.promote_checks(project, on_v1, "prod") == ["test is not on metadata v2; promote it to test first"]
    on_v2 = Store("test", [dep("test", "v1"), dep("test", "v2")])
    assert pm.promote_checks(project, on_v2, "prod") == []
    assert pm.promote_checks(project, Store("test"), "prod") == [
        "test is not on metadata v2; promote it to test first"]
