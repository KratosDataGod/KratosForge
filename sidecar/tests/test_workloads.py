"""Workloads: one inventory of entities, notebooks and Fabric items, with honest health.

Local stores are real (a metadata project, notebooks, schedules, the management file);
Fabric is a fake. Nothing here talks to a tenant."""

import datetime as dt
import json
import time
import types

import pytest

from fabric_desktop import schedule_service, workload_service
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.local_schedules import LocalSchedules
from fabric_desktop.service import Service
from fabric_desktop.workloads import health, inventory, model, query
from fabric_desktop.workloads.store import ManagementStore

USER = "chris@contoso.com"


class FakeScheduler:
    available = True

    def register(self, schedule, executable, workdir):
        pass

    def unregister(self, schedule_id):
        pass


def signed_in(svc, user=USER, token="tok"):
    svc._auth = types.SimpleNamespace(signed_in_user=lambda: user, silent_token=lambda scopes: token,
                                      fabric_token=lambda: token, storage_token=lambda: token)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(Service, "pc_schedules",
                        property(lambda self: LocalSchedules(self.root, FakeScheduler(), "C:/engine/python.exe")))
    s = Service(root=tmp_path / "app")
    signed_in(s, user="", token=None)  # signed out, and never prompting
    return s


def everything(svc, **kw):
    return svc.workloads_list(view="all", limit=500, **kw)


def by_name(page):
    return {r["name"]: r for r in page["rows"]}


# --------------------------------------------------------------------------- fresh install


def test_a_fresh_install_lists_nothing_and_never_asks_to_sign_in(svc):
    page = everything(svc)
    assert page["total"] == 0 and page["rows"] == []
    fabric = next(s for s in page["sources"] if s["source"].startswith("Fabric items"))
    assert fabric["state"] == "signed_out"
    assert {v["id"] for v in page["views"]} == set(query.VIEWS)


def test_local_notebooks_are_listed_with_what_is_known_about_them(svc):
    svc.notebooks.save("Daily load", [{"type": "code", "source": "x = 1"}])
    [row] = everything(svc)["rows"]
    assert (row["kind"], row["health"], row["unowned"]) == ("notebook", "idle", True)
    assert row["health_label"] == "No recorded runs"
    assert row["deployments"][0]["location"]["kind"] == "this_pc"


# --------------------------------------------------------------------------- metadata entities


@pytest.fixture
def project(svc, tmp_path):
    svc.meta_init(str(tmp_path / "proj"), "3")
    return svc._project()


def test_entities_are_listed_with_declared_owners_deployments_and_runs(svc, project):
    page = everything(svc)
    assert page["total"] == len(project.entities) == 6
    row = next(iter(by_name(page).values()))
    assert row["kind"] == "meta_entity" and row["group"]["label"] == "proj"
    assert row["owners"]["technical_owner"]["source"] == "declared"
    # The template writes "me": that's nobody, so it counts as unowned.
    assert row["owners"]["technical_owner"]["placeholder"] and row["unowned"]
    assert row["health"] == "idle"
    svc.meta_run(None, "dev")
    page = everything(svc)
    assert {r["health"] for r in page["rows"]} == {"healthy"}
    first = page["rows"][0]
    assert first["last_run"]["source"] == "meta.run_log in Dev" and first["last_run"]["where"] == "this_pc"
    detail = svc.workload_detail(first["id"])
    assert detail["runs"][0]["state"] == "ok"
    assert any(e["kind"] == "run" for e in detail["timeline"])
    assert detail["available_actions"] == [{"action": "run", "label": "Run in Dev", "env": "dev", "where": "This PC"}]


def test_downstream_and_products_are_shown_as_declared(svc, project):
    from fabric_desktop.meta.project import impact, run_order
    upstream = run_order(project)[0]
    detail = svc.workload_detail(model.meta_id("proj", upstream))
    assert [d["name"] for d in detail["dependencies"]["downstream"]] == impact(project, upstream)
    assert all(d["declared"] for d in detail["dependencies"]["downstream"])


def _inject(svc, monkeypatch, env, rows=None, error=None, deployments=None):
    """Make one environment's run log answer with these rows, or fail with this error."""
    real_runs, real_store = svc.meta_runs, svc._store

    def runs(e, limit=100):
        if e != env:
            return real_runs(e, limit)
        if error:
            raise error
        return [dict(r) for r in rows or []]

    def store(project, e, create=False):
        if e != env:
            return real_store(project, e, create)
        import polars as pl
        frame = pl.DataFrame(deployments or [], schema={"env": pl.String, "version": pl.String,
                                                       "source_env": pl.String, "at": pl.String})
        return types.SimpleNamespace(meta=lambda table: frame)

    monkeypatch.setattr(svc, "meta_runs", runs)
    monkeypatch.setattr(svc, "_store", store)


def _test_env(svc, project):
    svc._write_environments(project, {"dev": {"target": "local"},
                                      "test": {"target": "onelake", "workspace_id": "ws-test",
                                               "workspace_name": "Sales Test", "compute": "fabric"}})


def test_a_failed_run_makes_the_workload_failing_with_the_run_as_evidence(svc, project, monkeypatch):
    _test_env(svc, project)
    signed_in(svc)
    name = sorted(project.entities)[0]
    rows = [{"run_id": "r2", "entity": name, "status": "failed", "started_at": "2026-10-16T09:41:00+00:00",
             "seconds": 3, "message": "landing/shop/orders/*.csv matched no files", "batch_id": "fabric-abc"},
            {"run_id": "r1", "entity": name, "status": "ok", "started_at": "2026-10-15T06:00:00+00:00", "seconds": 9,
             "batch_id": "b1"}]
    _inject(svc, monkeypatch, "test", rows, deployments=[{"env": "test", "version": "old", "source_env": "dev",
                                                         "at": "2026-10-14T11:05:00+00:00"}])
    page = svc.workloads_list(view="attention")
    assert [r["name"] for r in page["rows"]] == [name]
    detail = svc.workload_detail(model.meta_id("proj", name))
    assert detail["health"] == "failing"
    failed = next(f for f in detail["findings"] if f["level"] == "failing")
    assert failed["evidence"]["run_id"] == "r2" and failed["evidence"]["ran_on"] == "Fabric"
    assert "matched no files" in failed["text"]
    assert any(n["action"] == "run" for n in failed["next"])
    behind = next(f for f in detail["findings"] if f["kind"] == "release")
    assert behind["level"] == "notice" and "older version" in behind["title"]
    test_dep = next(d for d in detail["deployments"] if d["env"] == "test")
    assert test_dep["location"]["kind"] == "fabric" and test_dep["version"]["behind"]


def test_a_source_that_cant_be_read_is_unknown_never_failing(svc, project, monkeypatch):
    _test_env(svc, project)
    svc.meta_run(None, "dev")
    page = everything(svc)  # signed out: Test can't be read
    assert {r["health"] for r in page["rows"]} == {"healthy"} and all(r["partial"] for r in page["rows"])
    test_obs = next(s for s in page["sources"] if "Test" in s["source"])
    assert test_obs["state"] == "signed_out"
    detail = svc.workload_detail(page["rows"][0]["id"])
    unknown = next(f for f in detail["findings"] if f["kind"] == "telemetry")
    assert unknown["level"] == "unknown" and "says nothing about the workload" in unknown["text"]

    signed_in(svc)
    _inject(svc, monkeypatch, "test", error=FabricError("GET /x failed with 403: Forbidden"))
    page = everything(svc, refresh=True)
    assert next(s for s in page["sources"] if "Test" in s["source"])["state"] == "unauthorized"
    assert "failing" not in {r["health"] for r in page["rows"]}


def test_the_last_good_read_is_shown_as_cached_then_stale(svc, project, monkeypatch):
    _test_env(svc, project)
    signed_in(svc)
    name = sorted(project.entities)[0]
    _inject(svc, monkeypatch, "test", [{"run_id": "r1", "entity": name, "status": "ok",
                                        "started_at": "2026-10-16T06:00:00+00:00", "seconds": 1}])
    page = everything(svc, refresh=True)
    assert next(s for s in page["sources"] if "Test" in s["source"])["state"] == "live"
    _inject(svc, monkeypatch, "test", error=ConnectionError("Failed to establish a new connection"))
    page = everything(svc, refresh=True)
    obs = next(s for s in page["sources"] if "Test" in s["source"])
    assert obs["state"] == "cached" and "Couldn't reach Fabric" in obs["detail"]
    row = by_name(page)[name]
    assert row["health"] == "healthy"  # what was last seen, labelled as such
    old = model.iso(model.now() - dt.timedelta(hours=5))
    cached = svc.workload_store.cache_get("meta:proj:test")
    svc.workload_store.cache_put("meta:proj:test", cached["payload"], collected_at=old)
    page = everything(svc, refresh=True)
    assert next(s for s in page["sources"] if "Test" in s["source"])["state"] == "stale"


# --------------------------------------------------------------------------- notebooks and Fabric items


class FakeFabric:
    """Workspaces ws1 (readable) and ws2 (403)."""
    items = {"ws1": [{"id": "nb1", "type": "Notebook", "displayName": "Renamed in Fabric"},
                     {"id": "nb2", "type": "Notebook", "displayName": "Other"},
                     {"id": "nb3", "type": "Notebook", "displayName": "Dup"},
                     {"id": "pl1", "type": "DataPipeline", "displayName": "pl_load_erp"},
                     {"id": "run", "type": "Notebook", "displayName": inventory.RUNNER_NOTEBOOK},
                     {"id": "lh1", "type": "Lakehouse", "displayName": "LH_Bronze"}],
             "ws3": [{"id": "nb4", "type": "Notebook", "displayName": "Dup"}]}

    def __init__(self, token=None):
        pass

    def list_workspaces(self):
        return [{"id": "ws1", "displayName": "Sales Dev"}, {"id": "ws2", "displayName": "Locked"},
                {"id": "ws3", "displayName": "Team B"}]

    def list_items(self, ws):
        if ws == "ws2":
            raise FabricError("GET /workspaces/ws2/items failed with 403: Forbidden")
        return self.items[ws]

    def list_job_instances(self, ws, item):
        return [{"id": "j9", "status": "Failed", "startTimeUtc": "2026-10-16T05:00:00Z",
                 "failureReason": {"message": "Activity Copy failed"}}]


def scan(svc, monkeypatch):
    monkeypatch.setattr(schedule_service, "FabricClient", FakeFabric)
    for ws in ("ws1", "ws2", "ws3"):
        svc.set_workspace_mark(ws, "pins", True)
    svc._wl_scan_items("tok", client=FakeFabric())


def test_fabric_items_are_scanned_per_workspace_and_a_refusal_is_partial(svc, monkeypatch):
    signed_in(svc)
    scan(svc, monkeypatch)
    page = everything(svc)
    names = {r["name"]: r for r in page["rows"]}
    assert {"pl_load_erp", "Renamed in Fabric", "Other", "Dup"} <= set(names)
    assert inventory.RUNNER_NOTEBOOK not in names and "LH_Bronze" not in names
    obs = next(s for s in page["sources"] if s["source"].startswith("Fabric items"))
    assert obs["state"] == "partial" and "Locked" in obs["detail"]
    pipeline = names["pl_load_erp"]
    assert pipeline["health"] == "unknown" and pipeline["kind_label"] == "Pipeline"


def test_a_pipeline_page_reads_its_job_history(svc, monkeypatch):
    signed_in(svc)
    scan(svc, monkeypatch)
    monkeypatch.setattr(workload_service, "FabricClient", FakeFabric)
    detail = svc.workload_detail(model.fabric_id("ws1", "pl1"))
    assert detail["runs"][0]["message"] == "Activity Copy failed" and detail["health"] == "failing"
    assert detail["deployments"][0]["observation"]["state"] == "live"


def test_a_notebook_keeps_its_fabric_item_by_recorded_id_after_a_rename(svc, monkeypatch):
    signed_in(svc)
    for name in ("Load", "Other", "Dup"):
        svc.notebooks.save(name, [{"type": "code", "source": "x = 1"}], workspace={"workspace_id": "ws1",
                                                                                 "workspace_name": "Sales Dev"})
    svc._record_notebook_item("Load", "ws1", "nb1", "Sales Dev")
    scan(svc, monkeypatch)
    rows = by_name(everything(svc))
    load = svc.workload_detail(model.notebook_id("Load"))
    assert load["link"]["how"] == "recorded" and load["link"]["item_id"] == "nb1"
    assert "Renamed in Fabric" not in rows  # shown inside the notebook, not twice
    other = svc.workload_detail(model.notebook_id("Other"))
    assert other["link"]["how"] == "name match"
    assert any(f["kind"] == "identity" for f in other["findings"])
    dup = svc.workload_detail(model.notebook_id("Dup"))
    assert dup["link"]["item_id"] == "nb3"  # its own workspace's item only
    assert model.fabric_id("ws3", "nb4") in {r["id"] for r in everything(svc)["rows"]}
    svc.rename_notebook("Load", "Load v2")
    assert svc.workload_detail(model.notebook_id("Load v2"))["link"]["item_id"] == "nb1"
    svc.workload_link("Other", "ws1", "nb2", "Sales Dev")
    assert svc.workload_detail(model.notebook_id("Other"))["link"]["how"] == "manual"


def test_publishing_records_the_item_id(svc, monkeypatch):
    signed_in(svc)

    class Pub:
        def save_notebook(self, ws, name, ipynb):
            return {"id": "nb-new"}

    monkeypatch.setattr(Service, "fabric", property(lambda self: Pub()))
    svc.notebooks.save("Fresh", [{"type": "code", "source": "x = 1"}])
    svc.publish_notebook("Fresh", [{"type": "code", "source": "x = 1"}], None, None,
                         {"workspace_id": "ws9", "workspace_name": "Nine"})
    assert svc.workload_store.links()["Fresh"]["item_id"] == "nb-new"


# --------------------------------------------------------------------------- ownership


def test_owners_assigned_on_this_pc_and_declared_ones_win(svc, project):
    signed_in(svc)
    svc.notebooks.save("Daily load", [{"type": "code", "source": "x = 1"}])
    wid = model.notebook_id("Daily load")
    out = svc.workload_assign(wid, {"technical_owner": USER, "team": "Data team", "criticality": "high",
                                    "tags": ["finance", " finance ", ""]})
    assert out["owners"]["technical_owner"] == {"value": USER, "source": "assigned", "where": "on this PC",
                                                "set_by": USER, "set_at": out["owners"]["technical_owner"]["set_at"],
                                                "placeholder": False}
    assert out["criticality"]["value"] == "high" and out["tags"] == ["finance"]
    assert [h["field"] for h in out["assignment_history"]].count("technical_owner") == 1
    assert [r["name"] for r in svc.workloads_list(view="mine")["rows"]] == ["Daily load"]
    entity = model.meta_id("proj", sorted(project.entities)[0])
    with pytest.raises(ValueError, match="entity file"):
        svc.workload_assign(entity, {"technical_owner": "someone"})
    svc.workload_assign(entity, {"business_contact": "Dana"})  # project.toml has no contacts: allowed
    with pytest.raises(ValueError):
        svc.workload_assign(wid, {"criticality": "extreme"})
    with pytest.raises(ValueError):
        svc.workload_assign(wid, {"favourite_colour": "blue"})
    svc.workload_assign(wid, {"team": ""})
    assert "team" not in svc.workload_detail(wid)["owners"]


def test_mine_never_comes_from_who_ran_it(svc, project):
    signed_in(svc)
    svc.meta_run(None, "dev")  # chris ran everything
    assert svc.workloads_list(view="mine")["total"] == 0


# --------------------------------------------------------------------------- actions


def test_retry_runs_through_the_existing_run_and_records_the_outcome(svc, project):
    signed_in(svc)
    wid = model.meta_id("proj", sorted(project.entities)[0])
    pre = svc.workload_preflight(wid, "run", "dev")
    assert pre["allowed"] and pre["env_label"] == "Dev" and pre["where"] == "This PC"
    assert USER in pre["identity"] and "writes its table" in pre["consequence"]
    out = svc.workload_act(wid, "run", "dev")
    assert out["action"]["status"] in ("succeeded", "failed") and out["action"]["identity"] == pre["identity"]
    assert out["action"]["status"] == ("succeeded" if all(r["status"] in ("ok", "skipped") for r in out["result"])
                                       else "failed")
    assert svc.workload_actions(wid)[0]["id"] == out["action"]["id"]


def test_a_failed_run_is_recorded_as_failed(svc, project, monkeypatch):
    signed_in(svc)
    wid = model.meta_id("proj", sorted(project.entities)[0])
    monkeypatch.setattr(svc, "meta_run", lambda names, env: [{"entity": names[0], "status": "failed",
                                                               "message": "boom", "run_id": "x"}])
    assert svc.workload_act(wid, "run", "dev")["action"]["status"] == "failed"
    monkeypatch.setattr(svc, "meta_run", lambda names, env: (_ for _ in ()).throw(RuntimeError("engine stopped")))
    with pytest.raises(RuntimeError):
        svc.workload_act(wid, "run", "dev")
    last = svc.workload_actions(wid)[0]
    assert last["status"] == "failed" and "engine stopped" in last["detail"]


@pytest.fixture
def no_capacity_read(monkeypatch):
    """The risk check in a Fabric run's preflight reads the workspace's capacity from Fabric; with the
    fake token that went to the real API and waited out its retries (about 150 s, or a timeout)."""
    monkeypatch.setattr(Service, "_risk_capacity_read", lambda self, *a: None)


def test_a_fabric_run_stays_submitted_until_fabric_says_how_it_ended(svc, monkeypatch, no_capacity_read):
    signed_in(svc)
    scan(svc, monkeypatch)
    wid = model.fabric_id("ws1", "pl1")
    pre = svc.workload_preflight(wid, "run_item")
    assert pre["allowed"] and "exactly as Fabric's own Run does" in pre["consequence"]
    calls = []

    def run_now(ws, item, job, name, ws_name=""):
        calls.append((ws, item, job))
        return svc._log_fabric_run({"kind": "Scheduled item run", "name": name, "workspace_id": ws,
                                    "workspace_name": ws_name, "item_id": item, "job_id": "job-1"})

    monkeypatch.setattr(svc, "run_fabric_item_now", run_now)
    out = svc.workload_act(wid, "run_item")
    assert calls == [("ws1", "pl1", "Pipeline")] and out["action"]["status"] == "submitted"
    everything(svc)
    assert svc.workload_actions(wid)[0]["status"] == "submitted"  # Fabric hasn't said yet
    runs = svc._read_fabric_runs()
    runs[-1]["status"] = "Completed"
    svc._write_fabric_runs(runs)
    everything(svc)
    assert svc.workload_actions(wid)[0]["status"] == "succeeded"


def test_unsupported_or_unsafe_actions_are_refused(svc, project, monkeypatch):
    wid = model.meta_id("proj", sorted(project.entities)[0])
    assert not svc.workload_preflight(wid, "cancel", "dev")["allowed"]
    assert not svc.workload_preflight(wid, "run", "prod")["allowed"]  # not deployed there
    signed_in(svc, token=None)
    scan_svc = svc
    monkeypatch.setattr(schedule_service, "FabricClient", FakeFabric)
    svc.set_workspace_mark("ws1", "pins", True)
    scan_svc._wl_scan_items("tok", client=FakeFabric())
    pre = svc.workload_preflight(model.fabric_id("ws1", "pl1"), "run_item")
    assert not pre["allowed"] and "Sign in" in pre["reason"]
    with pytest.raises(PermissionError):
        svc.workload_act(model.fabric_id("ws1", "pl1"), "run_item")
    assert svc.workload_actions() == []


# --------------------------------------------------------------------------- store, model, scale


def test_an_ended_action_never_changes(tmp_path):
    store = ManagementStore(tmp_path / "w.sqlite")
    a = store.start_action("notebook:x", "run_in_fabric")
    store.update_action(a["id"], "failed", detail="boom")
    assert store.update_action(a["id"], "succeeded")["status"] == "failed"


@pytest.mark.parametrize("exc,state", [
    (FabricError("GET /x failed with 403: Forbidden"), "unauthorized"),
    (PermissionError("nope"), "unauthorized"),
    (FabricError("GET /x failed with 429: Too Many Requests"), "throttled"),
    (ConnectionError("Failed to establish a new connection"), "offline"),
    (LookupError("Assign a workspace to Test first"), "not_set_up"),
    (RuntimeError("weird"), "error"),
])
def test_read_failures_are_classified(exc, state):
    assert model.classify_error(exc)[0] == state


def test_ids_round_trip():
    assert model.parse_id(model.meta_id("sales-metadata", "dim_customer")) == {
        "kind": "meta_entity", "project": "sales-metadata", "entity": "dim_customer"}
    assert model.parse_id(model.fabric_id("w", "i"))["item_id"] == "i"
    with pytest.raises(ValueError):
        model.parse_id("bogus")


def _synthetic(n):
    items = [{"workspace_id": f"ws{i % 40}", "workspace_name": f"Workspace {i % 40}", "id": f"item{i}",
              "displayName": f"pipeline_{i}", "type": "DataPipeline" if i % 3 else "Notebook"} for i in range(n)]
    runs = [{"item_id": f"item{i}", "job_id": f"j{i}", "status": "Failed" if i % 17 == 0 else "Completed",
             "started_at": "2026-10-16T05:00:00+00:00", "workspace_name": "w"} for i in range(0, n, 2)]
    assignments = {model.fabric_id(f"ws{i % 40}", f"item{i}"): {"team": {"value": f"team{i % 9}"}}
                   for i in range(0, n, 3)}
    obs = model.observation("Fabric items", "cached", model.now())
    records = inventory.fabric_workloads(items, items_obs=obs, linked=set(), fabric_schedules=[], fabric_runs=runs,
                                         assignments=assignments)
    for r in records:
        r.update(health.assess(r))
    return records


def test_ten_thousand_workloads_filter_and_page_quickly():
    t0 = time.perf_counter()
    records = _synthetic(10_000)
    build = time.perf_counter() - t0
    timings = []
    for kw in ({}, {"view": "attention"}, {"query": "pipeline_99 team"}, {"group": "owner", "offset": 5000},
               {"view": "unowned", "group": "environment"}):
        t0 = time.perf_counter()
        page = query.select(records, limit=100, **kw)
        timings.append(time.perf_counter() - t0)
        assert len(page["rows"]) <= 100
    page = query.select(records, view="attention", limit=100)
    assert page["total"] == len([i for i in range(0, 10_000, 2) if i % 17 == 0])
    assert all(r["health"] == "failing" for r in page["rows"])
    last = query.select(records, offset=9_950, limit=100)
    assert len(last["rows"]) == 50 and last["total"] == 10_000
    print(f"\nbuild+assess 10k: {build * 1000:.0f} ms; select: " + ", ".join(f"{t * 1000:.0f} ms" for t in timings))
    assert max(timings) < 1.0 and build < 5.0  # generous for shared CI runners; measured numbers are printed
