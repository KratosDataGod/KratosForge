"""The headless agent's runtime: its Fabric clients, the workspace allowlist, a pipeline run looked
up in Fabric, a cascade run, maintenance that fails after the tables are written, and what
``agent check`` reports. The credential and Fabric are stand-ins; nothing signs in."""

from __future__ import annotations

import dataclasses
import os
from types import SimpleNamespace

import pytest

from fabric_desktop import runtime as rt
from fabric_desktop.auth import EXECUTE_SCOPES, ONELAKE_SCOPES
from fabric_desktop.fabric_api import FabricClient
from fabric_desktop.meta import OneLakeStore
from fabric_desktop.runtime import Runtime
from tests.test_agent import setup  # noqa: F401 - the agent.toml and sample project fixture


class Cred:
    def __init__(self):
        self.asked = []

    def _token(self, scopes):
        self.asked.append(scopes)
        return "tok"

    def fabric_token(self):
        return "fab"

    def storage_token(self):
        return "sto"

    def signed_in_user(self):
        return "etl@contoso.com"


def test_the_fabric_clients_use_the_agent_s_own_credential(setup):  # noqa: F811
    cred = Cred()
    r = Runtime(setup, cred)
    assert isinstance(r.fabric, FabricClient) and r.fabric._token() == "fab"
    assert r.fabric_jobs._token() == "tok" and cred.asked[-1] == EXECUTE_SCOPES
    assert r.onelake_token() == "tok" and cred.asked[-1] == ONELAKE_SCOPES


def test_the_workspace_allowlist_refuses_any_other_workspace(setup):  # noqa: F811
    r = Runtime(dataclasses.replace(setup, workspaces=["ws-ok"]), Cred())
    project = SimpleNamespace(environments={"dev": {"workspace_id": "ws-ok", "keys_workspace_id": "ws-ok",
                                                    "workspaces": {"gold": {"id": "ws-other"}}}})
    with pytest.raises(PermissionError, match="Dev's gold workspace ws-other isn't in agent.workspaces"):
        r.check_allowed(project, "dev")
    project.environments["dev"]["workspaces"]["gold"]["id"] = "ws-ok"
    r.check_allowed(project, "dev")  # every workspace allowed: nothing raised
    project.environments["dev"] = {}
    r.check_allowed(project, "dev")  # nothing named yet: nothing to refuse


def test_a_pipeline_run_is_looked_up_in_the_environment_s_workspace(setup, monkeypatch):  # noqa: F811
    r = Runtime(setup, Cred())
    store = OneLakeStore.__new__(OneLakeStore)
    store.workspace_id = "ws-1"
    monkeypatch.setattr(r, "_store", lambda project, env: store)
    asked = []
    monkeypatch.setattr(FabricClient, "job_instance", lambda self, ws, item, run: asked.append((ws, item, run)) or {"status": "Completed"})
    assert r.verify_pipeline_run("dev", "pl-1", "run-1") == {"status": "Completed"}
    assert asked == [("ws-1", "pl-1", "run-1")]


class FakeEngine:
    made = []

    def __init__(self, project, store, **kw):
        self.kw = kw
        FakeEngine.made.append(self)

    def backfill(self, entity):
        self.called = ("backfill", entity)
        return [SimpleNamespace(to_dict=lambda: {"entity": entity})]

    def run_many(self, names, backfill=None):
        self.called = ("run_many", names, backfill)
        return [SimpleNamespace(to_dict=lambda: {"entity": "a"}), SimpleNamespace(to_dict=lambda: {"entity": "b"})]


@pytest.fixture
def stubbed(setup, monkeypatch):  # noqa: F811
    r = Runtime(setup, Cred())
    FakeEngine.made.clear()
    monkeypatch.setattr(rt, "Engine", FakeEngine)
    for name, value in {"_store": lambda p, e: object(), "_released": lambda p, e, s: p,
                        "_uses_keys": lambda p, e: False, "_table_lock": lambda *a, **k: None,
                        "_link_table": lambda p, e: None}.items():
        monkeypatch.setattr(r, name, value)
    return r


def test_a_cascade_of_one_entity_backfills_what_depends_on_it(stubbed, monkeypatch):
    monkeypatch.setattr(stubbed, "_after_run", lambda *a: None)
    out = stubbed.run_environment("dev", ["silver.orders"], cascade=True, job={"attempts": 2, "id": "j1"})
    assert out == [{"entity": "silver.orders"}]
    engine = FakeEngine.made[-1]
    assert engine.called == ("backfill", "silver.orders")
    assert engine.kw["run_context"]["attempt"] == 2 and engine.kw["run_context"]["job_id"] == "j1"


def test_maintenance_failing_after_the_run_is_noted_on_each_result(stubbed, monkeypatch):
    def broken(*a):
        raise RuntimeError("vacuum refused")

    monkeypatch.setattr(stubbed, "_after_run", broken)
    out = stubbed.run_environment("dev", cascade=True)  # cascade needs one entity: this is a normal run
    assert FakeEngine.made[-1].called == ("run_many", None, None)
    assert [r["maintenance_message"] for r in out] == ["RuntimeError: vacuum refused"] * 2


def test_check_signs_in_for_a_cloud_environment_and_warns_about_an_open_key_file(setup, monkeypatch):  # noqa: F811
    r = Runtime(dataclasses.replace(setup, environments=["dev", "prod"]), Cred())
    monkeypatch.setattr(rt, "key_file_open", lambda auth: True)
    out = r.check()
    assert out["environments"]["prod"]["target"] == "onelake" and out["pid"] == os.getpid()
    assert out["warnings"] == [rt.KEY_FILE_OPEN]
    assert out["identity"] == "etl@contoso.com" and out["fabric_token"] is True and out["storage_token"] is True


def test_check_for_this_pc_only_signs_in_to_nothing(setup):  # noqa: F811
    out = Runtime(setup, Cred()).check()
    assert out["environments"] == {"dev": {"target": "local", "workspace_id": ""}}
    assert "identity" not in out and "warnings" not in out
