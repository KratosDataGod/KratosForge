"""Metadata runs on Fabric capacity, inside Fabric: the OneLake and platform-keys stores the runner
builds, links that weren't resolved, a cascade, and the table maintenance queued after a run. The
engine, OneLake and Fabric are stand-ins; nothing reaches a tenant."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fabric_desktop import fabric_api, meta
from fabric_desktop.central import client as central_client
from fabric_desktop.fabric_compute import bundle_files, run_bundle
from fabric_desktop.meta.templates import init_project
from tests.test_fabric_compute import write_bundle


class Result:
    def __init__(self, entity, status="ok", rows=5, message=""):
        self.d = {"entity": entity, "status": status, "rows_written": rows, "message": message}

    def to_dict(self):
        return dict(self.d)


class FakeEngine:
    last = None

    def __init__(self, project, store, keys_store=None, link_table=None, run_context=None):
        self.project, self.store, self.keys_store = project, store, keys_store
        self.link_table, self.run_context = link_table, run_context
        FakeEngine.last = self

    def backfill(self, name):
        self.called = ("backfill", name)
        return [Result(name)]

    def run_many(self, names, backfill=None):
        self.called = ("run_many", names, backfill)
        return [Result(n) for n in (names or ["silver.orders", "gold.fact_sales"])]


class FakeStore:
    made = []

    def __init__(self, env, workspace_id, lakehouse_ids, token, cache, lakehouse_workspaces=None):
        self.args = (env, workspace_id, lakehouse_ids, lakehouse_workspaces)
        self.token = token
        FakeStore.made.append(self)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(meta, "Engine", FakeEngine)
    monkeypatch.setattr(meta, "OneLakeStore", FakeStore)
    FakeStore.made.clear()
    proj = tmp_path / "proj"
    init_project(proj, "3", "Chris", True)

    def make(config):
        return write_bundle(bundle_files(proj, config), tmp_path / "bundle")

    return make


CONFIG = {"env": "test", "workspace_id": "ws-main", "lakehouse_ids": {"LH_Silver": "lh-s", "LH_Gold": "lh-g"},
          "lakehouse_workspaces": {"LH_Gold": "ws-gold"}}


def test_inside_fabric_the_runner_reads_onelake_with_the_notebook_s_token(bundle, tmp_path):
    b = bundle(CONFIG | {"keys_workspace_id": "ws-keys", "keys_lakehouse_ids": {"LH_Keys": "lh-k"},
                         "optimize_after_run": False, "links": {"crm": "crm.dbo.customers"}})
    out = run_bundle(b, storage_token=lambda: "sto", fabric_token=lambda: "fab", results_dir=tmp_path / "r")
    main, keys = FakeStore.made
    assert main.args == ("test", "ws-main", CONFIG["lakehouse_ids"], {"LH_Gold": "ws-gold"})
    assert keys.args[:3] == ("test", "ws-keys", {"LH_Keys": "lh-k"}) and keys.token() == "sto"
    engine = FakeEngine.last
    assert engine.store is main and engine.keys_store is keys
    assert engine.link_table("crm") == "crm.dbo.customers"
    with pytest.raises(LookupError, match="link erp wasn't resolved"):
        engine.link_table("erp")
    assert all("maintenance" not in r for r in out["results"])  # optimize_after_run off


def test_keys_kept_in_the_platform_are_read_through_its_functions(bundle, tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(central_client, "FabricPlatform", lambda token, ws, fn: made.append((token(), ws, fn)) or "platform")
    monkeypatch.setattr(central_client, "PlatformKeys", lambda platform, env: ("keys", platform, env))
    b = bundle(CONFIG | {"platform_keys": {"workspace_id": "ws-plat", "functions_id": "fn-1"}, "optimize_after_run": False})
    run_bundle(b, storage_token=lambda: "sto", fabric_token=lambda: "fab", results_dir=tmp_path / "r")
    assert made == [("fab", "ws-plat", "fn-1")]
    assert FakeEngine.last.keys_store == ("keys", "platform", "test")


def test_platform_keys_without_a_fabric_token_keep_the_onelake_keys(bundle, tmp_path):
    b = bundle(CONFIG | {"platform_keys": {"workspace_id": "ws-plat", "functions_id": "fn-1"}})
    run_bundle(b, storage_token=lambda: "sto", results_dir=tmp_path / "r")
    assert FakeEngine.last.keys_store is None  # no keys workspace either


def test_a_cascade_of_one_entity_backfills_it(bundle, tmp_path):
    b = bundle(CONFIG)
    out = run_bundle(b, entities=" silver.orders ,", cascade=True, stores=("s", None), results_dir=tmp_path / "r")
    assert FakeEngine.last.called == ("backfill", "silver.orders")
    assert [r["entity"] for r in out["results"]] == ["silver.orders"]


def test_after_a_run_each_written_table_gets_maintenance_queued_in_its_workspace(bundle, tmp_path, monkeypatch):
    asked = []

    def maintain(self, ws, lakehouse_id, table, schema=None, vacuum_retention=None):
        asked.append((ws, lakehouse_id, table, schema, vacuum_retention))
        if table == "fact_sales":
            raise fabric_api.FabricError("capacity paused", status=430)

    monkeypatch.setattr(fabric_api.FabricClient, "run_table_maintenance", maintain)

    def run_many(self, names, backfill=None):
        return [Result("silver.orders"), Result("gold.fact_sales"), Result("silver.customers", rows=0),
                Result("gold.dim_customer", status="failed", message="bad"), Result("not.an_entity")]

    monkeypatch.setattr(FakeEngine, "run_many", run_many)
    out = run_bundle(bundle(CONFIG), stores=("s", None), fabric_token=lambda: "fab", results_dir=tmp_path / "r")
    by = {r["entity"]: r for r in out["results"]}
    assert by["silver.orders"]["maintenance"] == "queued"
    assert by["gold.fact_sales"]["maintenance"] == "Failed" and "capacity paused" in by["gold.fact_sales"]["maintenance_message"]
    assert all("maintenance" not in by[n] for n in ("silver.customers", "gold.dim_customer", "not.an_entity"))
    (silver, gold) = asked
    assert silver[:3] == ("ws-main", "lh-s", "orders")
    assert gold[0] == "ws-gold" and gold[1] == "lh-g" and gold[4].endswith(".01:00:00")
    saved = json.loads((tmp_path / "r" / f"{out['request_id']}.json").read_text(encoding="utf-8"))
    assert saved["results"][1]["maintenance"] == "Failed"
    with pytest.raises(RuntimeError, match="gold.dim_customer: bad"):
        out["raise_if_failed"]()


def test_central_landing_zones_come_from_the_run_s_config(bundle, tmp_path):
    store = SimpleNamespace()
    run_bundle(bundle(CONFIG | {"landing": {"crm": "zone-1"}}), stores=(store, None), results_dir=tmp_path / "r")
    assert store.landing_zones() == {"crm": "zone-1"}
