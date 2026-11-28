"""Mutation batch 120: runtime.py (docs/quality/mutation-testing.md). The survivors were an environment's
settings, the settings file's layout, where pipelines leave run requests, a pipeline run asked of an
environment on this PC, the entity names, and what a run hands the engine (keys, attempt, names, backfill)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import mailbox
from fabric_desktop.meta import OneLakeStore
from fabric_desktop.meta.store import META_LAKEHOUSE
from fabric_desktop.runtime import Runtime
from tests.test_agent import setup  # noqa: F401 - the agent.toml and sample project fixture
from tests.test_runtime_edges import Cred, FakeEngine, stubbed  # noqa: F401 - stand-ins and their fixture


def _with_project(r, monkeypatch, environments=None, entities=None):
    project = SimpleNamespace(environments=environments or {}, entities=entities or {})
    monkeypatch.setattr(r, "_project", lambda: project)
    return project


def test_an_environment_s_settings_are_a_copy_and_none_is_empty(setup, monkeypatch):  # noqa: F811
    r = Runtime(setup, Cred())
    project = _with_project(r, monkeypatch, {"dev": {"workspace_id": "ws-1"}})
    got = r.environment_config("dev")
    assert got == {"workspace_id": "ws-1"}
    got["workspace_id"] = "changed"
    assert project.environments["dev"] == {"workspace_id": "ws-1"}
    assert r.environment_config("prod") == {}


def test_the_settings_file_is_indented_by_two_and_keeps_out_what_agent_toml_says(setup):  # noqa: F811
    r = Runtime(setup, Cred())
    r.root.mkdir(parents=True, exist_ok=True)
    r._save_settings(theme="dark", meta_project="elsewhere", teams_webhook="https://x")
    assert r.settings_file.read_text(encoding="utf-8").replace("\r\n", "\n") == '{\n  "theme": "dark"\n}'


def test_run_requests_go_to_the_environment_s_lh_meta_or_its_folder(setup, monkeypatch):  # noqa: F811
    r = Runtime(setup, Cred())
    _with_project(r, monkeypatch)
    monkeypatch.setattr(r, "check_allowed", lambda project, env: None)
    asked = []
    monkeypatch.setattr(mailbox, "control_backend_for", lambda store, meta, token: asked.append(meta) or ("backend", store))
    lake = OneLakeStore.__new__(OneLakeStore)
    lake.lakehouse_ids = {META_LAKEHOUSE: "lh-meta"}
    monkeypatch.setattr(r, "_store", lambda project, env: lake)
    assert r.control_backend("prod") == ("backend", lake)
    folder = object()
    monkeypatch.setattr(r, "_store", lambda project, env: folder)
    assert r.control_backend("dev") == ("backend", folder)
    assert asked == ["lh-meta", None]


def test_a_pipeline_run_can_t_be_looked_up_for_an_environment_on_this_pc(setup, monkeypatch):  # noqa: F811
    r = Runtime(setup, Cred())
    monkeypatch.setattr(r, "_store", lambda project, env: object())
    with pytest.raises(LookupError, match="dev runs on this machine"):
        r.verify_pipeline_run("dev", "pl-1", "run-1")


def test_the_entity_names_are_the_project_s(setup, monkeypatch):  # noqa: F811
    r = Runtime(setup, Cred())
    _with_project(r, monkeypatch, entities={"silver.orders": 1, "gold.sales": 2})
    assert r.entity_names() == {"silver.orders", "gold.sales"}


def test_one_entity_runs_on_its_own_unless_a_cascade_is_asked(stubbed, monkeypatch):  # noqa: F811
    monkeypatch.setattr(stubbed, "_after_run", lambda *a: None)
    stubbed.run_environment("dev", ["silver.orders"])
    engine = FakeEngine.made[-1]
    assert engine.called == ("run_many", ["silver.orders"], None)
    assert engine.kw["run_context"]["attempt"] == 1 and engine.kw["keys_store"] is None


def test_names_and_a_backfill_reach_the_engine(stubbed, monkeypatch):  # noqa: F811
    monkeypatch.setattr(stubbed, "_after_run", lambda *a: None)
    stubbed.run_environment("dev", ["a", "b"], backfill="2026-01-01")
    assert FakeEngine.made[-1].called == ("run_many", ["a", "b"], "2026-01-01")


def test_the_keys_store_is_opened_only_when_the_run_uses_keys(stubbed, monkeypatch):  # noqa: F811
    monkeypatch.setattr(stubbed, "_after_run", lambda *a: None)

    def no_keys(project, env):
        raise AssertionError("the keys store was opened for a run that doesn't use keys")

    monkeypatch.setattr(stubbed, "_keys_store", no_keys)
    stubbed.run_environment("dev")
    keys = object()
    monkeypatch.setattr(stubbed, "_uses_keys", lambda p, e: True)
    monkeypatch.setattr(stubbed, "_keys_store", lambda project, env: keys)
    stubbed.run_environment("dev")
    assert FakeEngine.made[-1].kw["keys_store"] is keys
