"""cloud_service paths the uncapped mutation run (batch 225, docs/quality/mutation-testing.md) showed no test
checked. Fabric and OneLake are fakes; nothing reaches a tenant."""

from __future__ import annotations

import pytest

from test_notebook_files import FakeFabric as FilesFabric, FilesService

WS = {"workspace_id": "ws1", "workspace_name": "Sales WS"}


@pytest.fixture(autouse=True)
def app_data(tmp_path, monkeypatch):
    """App data (settings, sync pairs) goes to this test's folder, never the real one."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def files_svc(tmp_path):
    s = FilesService(root=tmp_path)
    s.fake = FilesFabric()
    return s


def test_a_publish_fabric_returns_an_id_for_keeps_that_id(files_svc):
    s = files_svc
    s.fake.save_notebook = lambda ws, name, data: {"id": "new-1", "displayName": name}
    s.fake.list_notebooks = lambda ws: [{"id": "old-1", "displayName": "NB"}]
    out = s._publish("NB", [{"type": "code", "source": "x = 1"}], None, workspace=WS)
    assert out["id"] == "new-1"


# ---- the Fabric step (fakes from test_fabric_step)

from test_fabric_step import LAKEHOUSE, StepService, FakeFabric as StepFabric, cells as step_cells  # noqa: E402

from fabric_desktop import fabric_step, maintenance  # noqa: E402
from fabric_desktop.service import Service  # noqa: E402


@pytest.fixture
def step_svc(tmp_path):
    s = StepService(root=tmp_path)
    s.fake = StepFabric()
    return s


def test_a_step_fabric_saves_without_an_id_is_refused_before_it_starts(step_svc):
    s = step_svc
    s.fake.save_notebook = lambda ws, name, data: {"displayName": name}  # no id, and list_notebooks finds none
    c = step_cells('spark.sql("OPTIMIZE gold.t VORDER")')
    with pytest.raises(LookupError, match=r"NB \(Fabric step\) was published but Fabric didn't return its id"):
        s.run_notebook_fabric_step("NB", c, LAKEHOUSE)
    assert s.fake.jobs == []


def test_a_failed_step_reads_no_table_health(step_svc, monkeypatch):
    s = step_svc
    run = s.run_notebook_fabric_step("NB", step_cells('spark.sql("OPTIMIZE gold.t VORDER")'), LAKEHOUSE)
    s.fake.status = "Failed"
    monkeypatch.setattr(type(s.auth), "storage_token", lambda self: "tok")

    class Files:
        def __init__(self, token):
            pass

        def read_bytes(self, ws, lh, path):
            return b""

    import fabric_desktop.cloud_service as cs
    monkeypatch.setattr(cs, "FileStore", Files)

    def health(uri, options=None, local=False):
        raise AssertionError("a failed step's tables aren't read")
    monkeypatch.setattr(maintenance, "health", health)
    out = s.notebook_fabric_step_result(run["request_id"])
    assert out["status"] == "Failed" and out["tables"] == [] and out["output"] is None


def test_the_vorder_cell_leaves_the_other_tables_alone_unless_asked(step_svc):
    out = step_svc.notebook_vorder_cell([], [], picked=["gold.u"])
    assert out["source"] == fabric_step.vorder_source(["gold.u"], False)
    assert out["source"] != fabric_step.vorder_source(["gold.u"], True)


def test_publish_check_takes_the_location_when_no_lakehouse_is_given(step_svc, monkeypatch):
    s = step_svc
    seen = {}

    def where(name, ref, workspace):
        seen["ref"] = ref
        return {"workspace_id": ref["workspace_id"]}
    monkeypatch.setattr(s, "_publish_workspace", where)

    def schemas(ws, lh):
        seen["schemas"] = (ws, lh)
        return True
    monkeypatch.setattr(s, "_schemas_enabled", schemas)
    monkeypatch.setattr(s, "_fabric_change", lambda name, ws, doc: {"changed": True, "ws": ws})
    out = s.notebook_publish_check("NB", [{"type": "code", "source": "x = 1"}], location_ref=LAKEHOUSE)
    assert seen["ref"] is LAKEHOUSE and seen["schemas"] == (LAKEHOUSE["workspace_id"], LAKEHOUSE["lakehouse_id"])
    assert out == {"changed": True, "ws": LAKEHOUSE["workspace_id"], "known": True}


# ---- Monitor's run list


def test_monitor_lists_at_most_200_runs_by_default(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    asked = []

    def runs(env, limit):
        asked.append(limit)
        return [{"started_at": f"2026-11-{1 + i // 100:02d}T{i % 100:04d}"} for i in range(250)]
    monkeypatch.setattr(s, "meta_runs", runs)
    out = s.meta_runs_all()
    assert asked == [200] and len(out["runs"]) == 200


# ---- metadata runs on Fabric capacity (fakes from test_fabric_compute and test_cloud)

import json  # noqa: E402
import types  # noqa: E402

from test_cloud import CloudService, FolderOneLake  # noqa: E402
from test_fabric_compute import ComputeFabric, FolderFiles  # noqa: E402


@pytest.fixture
def compute(tmp_path, monkeypatch):
    s = CloudService(root=tmp_path / "app")
    s.fake = ComputeFabric()
    s._auth = types.SimpleNamespace(storage_token=lambda: "tok", silent_token=lambda scopes: "tok")
    s.meta_init(str(tmp_path / "proj"), "3")
    FolderFiles.root, FolderFiles.uploads = tmp_path / "files", 0
    monkeypatch.setattr("fabric_desktop.cloud_service.FileStore", FolderFiles)
    project = s._project()
    store = FolderOneLake(tmp_path / "onelake", "dev", [l.lakehouse for l in project.layers] + ["LH_Meta", "LH_Keys"])
    monkeypatch.setattr(s, "_store", lambda p, env, create=False: store)
    monkeypatch.setattr(s, "_keys_store", lambda p, env, create=False: store)
    envs = {k: dict(v) for k, v in project.environments.items()}
    envs["dev"] = {"target": "onelake", "workspace_id": "ws-test", "compute": "fabric"}
    s._write_environments(project, envs)
    s.store = store
    return s


def test_a_bundle_without_links_or_landing_zones_has_neither_in_its_config(compute):
    project = compute._project()
    assert not project.links
    assert not any(e.spec.get("source", {}).get("landing") for e in project.entities.values())
    compute.store.landing_zones = lambda: {"sales": "zone-1"}  # zones known, but no source lands in one
    compute._fabric_bundle(project, "dev", compute.store)
    config = json.loads((FolderFiles.root / "ws-test" / "id-LH_Meta" / "Files/_fabric_desktop/current"
                         / "config.json").read_text())
    assert "links" not in config and "landing" not in config


def test_a_bundle_whose_runner_id_was_lost_is_published_again(compute):
    project = compute._project()
    compute._fabric_bundle(project, "dev", compute.store)
    first = FolderFiles.uploads
    state = json.loads(compute._bundles_file.read_text())
    state["dev:ws-test"].pop("runner_id")
    compute._bundles_file.write_text(json.dumps(state))
    assert compute._fabric_bundle(project, "dev", compute.store)["runner_id"] == "runner1"
    assert FolderFiles.uploads == 2 * first and len(compute.fake.notebooks) == 2


def test_deleting_a_schedule_answers_with_the_schedules_left(compute):
    compute._fabric_bundle(compute._project(), "dev", compute.store)  # publishes the runner
    deleted = []
    compute.fake.delete_schedule = lambda ws, item, job, sid: deleted.append((ws, item, job, sid))
    out = compute.meta_delete_schedule("dev", "s1")
    assert deleted == [("ws-test", "runner1", "RunNotebook", "s1")]
    assert out == compute.meta_schedules("dev") and out["schedules"] == []


def test_a_onelake_run_with_maintenance_off_queues_none(compute, monkeypatch):
    compute.meta_set_maintenance("dev", False)
    project = compute._project()

    def maintain(*a, **k):
        raise AssertionError("maintenance is off for Dev")
    monkeypatch.setattr(compute, "_maintain", maintain)
    results = [{"entity": "silver.orders", "status": "ok", "rows_written": 5}]
    compute._after_run(project, "dev", compute.store, results)
    assert "maintenance" not in results[0]


# ---- table health and the semantic model, on this PC


@pytest.fixture
def local_svc(tmp_path):
    s = CloudService(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    return s


def test_optimize_check_on_this_pc_has_no_capacity_to_read(local_svc):
    assert local_svc.meta_optimize_check("dev") == {"env": "dev", "local": True, "workspaces": []}


def test_the_model_plan_for_a_local_environment_never_asks_fabric(local_svc, monkeypatch):
    class NoFabric:
        def list_semantic_models(self, ws):
            raise AssertionError("an environment on this PC has no models in Fabric")
    local_svc.fake = NoFabric()
    real = local_svc._env_cfg
    monkeypatch.setattr(local_svc, "_env_cfg", lambda project, env: dict(real(project, env), workspace_id="ws-kept"))
    out = local_svc.meta_model_plan("dev")
    assert out["local"] is True and out["existing"] is None and out["workspace_id"] == "ws-kept"


# ---- the Direct Lake model over Test's gold layer


@pytest.fixture
def gold(local_svc, tmp_path, monkeypatch):
    from test_cloud import FakeFabric as ModelFabric
    s = local_svc
    s.fake = ModelFabric()
    project = s._project()
    store = FolderOneLake(tmp_path / "onelake", "test", [l.lakehouse for l in project.layers] + ["LH_Meta"])
    monkeypatch.setattr(s, "_store", lambda p, env, create=False: store if env == "test" else type(s)._store(s, p, env, create))
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    for e in project.entities.values():
        lh = project.layer(e.layer).lakehouse
        store.write(lh, e.schema, e.table, s._store(project, "dev").read(lh, e.schema, e.table))
    return s


def test_a_model_endpoint_without_an_id_is_not_ready(gold):
    gold.fake.get_lakehouse = lambda ws, lh: {"properties": {"sqlEndpointProperties": {"connectionString": "abc"}}}
    with pytest.raises(LookupError, match="SQL analytics endpoint isn't ready yet"):
        gold.meta_build_model("test")


def test_an_existing_models_names_are_read_only_when_it_is_replaced(gold):
    assert gold.meta_build_model("test")["id"] == "m1"
    assert gold.meta_build_model("test")["confirm"]["id"] == "m1"  # asks first, reads nothing yet
    assert not [c for c in gold.fake.calls if c[0] == "definition"]
    assert gold.meta_build_model("test", replace=True)["id"] == "m1"
    assert [c[0] for c in gold.fake.calls].count("definition") == 1


# ---- the capacity gate before table maintenance, and Optimize's read-only check

from types import SimpleNamespace  # noqa: E402

from fabric_desktop import cloud_service, risk  # noqa: E402
from fabric_desktop.cloud_service import CloudMixin  # noqa: E402
from fabric_desktop.meta.store import META_SCHEMAS, OneLakeStore  # noqa: E402


def gate(found, write=None):
    acks = []
    m = SimpleNamespace(_maintenance_capacity=lambda ws: found, _wl_user=lambda: "chris",
                        _risk_ack_write=write or acks.append)
    return m, acks


WARNED = {"outcome": risk.WARNING, "title": "Capacity paused", "risk_id": "r-1"}


@pytest.mark.parametrize("found", [None, {"outcome": risk.PASS, "title": "Running", "risk_id": "r-0"},
                                   {"outcome": risk.UNKNOWN, "title": "Not read", "risk_id": ""}])
def test_the_gate_lets_maintenance_start_without_a_blocking_reading(found):
    m, acks = gate(found)
    assert CloudMixin._maintenance_gate(m, "ws-dev", "dev", {}) is None and acks == []


def test_a_blocked_capacity_stops_maintenance_and_says_what_to_do():
    m, acks = gate({"outcome": risk.BLOCKED, "title": "Capacity paused", "remedy": "Resume it in Azure."})
    assert CloudMixin._maintenance_gate(m, "ws-dev", "dev", {"ws-dev": "r-1"}) == {
        "blocked": True,
        "message": "Not optimized: the capacity check is Blocked (Capacity paused). Resume it in Azure. Nothing was changed."}
    m, _ = gate({"outcome": risk.BLOCKED, "title": "Capacity paused"})
    assert CloudMixin._maintenance_gate(m, "ws-dev", "dev", {})["message"] == (
        "Not optimized: the capacity check is Blocked (Capacity paused). Nothing was changed.")
    assert acks == []


def test_a_warning_not_yet_accepted_stops_maintenance():
    m, acks = gate(WARNED)
    assert CloudMixin._maintenance_gate(m, "ws-dev", "dev", {"ws-other": "r-1"}) == {
        "warning": True, "risk_id": "r-1",
        "message": "Not optimized (capacity warning): Capacity paused. Optimize it from table health once you've "
                   "looked at the warning."}
    assert CloudMixin._maintenance_gate(m, "ws-dev", "dev", {"ws-dev": "r-old"}) == {
        "warning": True, "risk_id": "r-1",
        "message": "Not optimized: the capacity warning changed since you looked. Check it again."}
    assert acks == []


def test_an_accepted_warning_is_recorded_before_maintenance_starts():
    m, acks = gate(WARNED)
    assert CloudMixin._maintenance_gate(m, "ws-dev", "dev", {"ws-dev": "r-1"}) is None
    assert len(acks) == 1 and {k: acks[0][k] for k in ("risk_id", "workload_id", "action", "env", "result", "user",
                                                       "warnings")} == {
        "risk_id": "r-1", "workload_id": "maintain:ws-dev", "action": "meta_optimize", "env": "dev",
        "result": risk.WARNING, "user": "chris", "warnings": ["Capacity paused"]}


def test_an_acceptance_that_cant_be_recorded_starts_nothing():
    def fails(record):
        raise OSError("disk full")
    m, _ = gate(WARNED, fails)
    out = CloudMixin._maintenance_gate(m, "ws-dev", "dev", {"ws-dev": "r-1"})
    assert out["warning"] is True and out["risk_id"] == "r-1"
    assert out["message"].startswith("Not optimized, because accepting the capacity warning couldn't be recorded (")
    assert "disk full" in out["message"] and out["message"].endswith("so nothing started. Try Optimize again.")


class TwoWorkspaces(OneLakeStore):
    """OneLake as Optimize's check sees it: LH_Silver in ws-dev, LH_Gold in ws-serve; LH_Bronze isn't there."""
    def __init__(self):  # noqa: D107 - nothing to open
        pass

    lakehouse_ids = {"LH_Silver": "lh-silver", "LH_Gold": "lh-gold"}

    def workspace_of(self, lakehouse):
        return {"LH_Silver": "ws-dev", "LH_Gold": "ws-serve"}[lakehouse]


TABLES = [{"entity": "silver.orders", "lakehouse": "LH_Silver", "schema": "dbo", "table": "orders"},
          {"entity": "bronze.raw", "lakehouse": "LH_Bronze", "schema": "dbo", "table": "raw"},
          {"entity": "gold.sales", "lakehouse": "LH_Gold", "schema": "dbo", "table": "sales"}]


def check_mixin(readings, cfg):
    return SimpleNamespace(_project=lambda: None, _store=lambda project, env: TwoWorkspaces(),
                           _managed_tables=lambda project, include_meta=True: TABLES,
                           _env_cfg=lambda project, env: cfg, _maintenance_capacity=readings.get)


def test_the_optimize_check_reads_each_workspace_with_its_name_and_reading():
    readings = {"ws-dev": {"outcome": risk.WARNING, "title": "Paused", "detail": "since 9:00", "remedy": "Resume it",
                           "risk_id": "r-1"}}
    cfg = {"workspace_id": "ws-dev", "workspace_name": "Sales Dev",
           "workspaces": {"serve": {"id": "ws-serve", "name": "Sales Serve"}, "odd": "not a table"}}
    assert CloudMixin.meta_optimize_check(check_mixin(readings, cfg), "dev") == {"env": "dev", "local": False, "workspaces": [
        {"workspace_id": "ws-dev", "workspace_name": "Sales Dev", "tables": ["dbo.orders"], "result": risk.WARNING,
         "title": "Paused", "detail": "since 9:00", "remedy": "Resume it", "risk_id": "r-1"},
        {"workspace_id": "ws-serve", "workspace_name": "Sales Serve", "tables": ["dbo.sales"], "result": risk.UNKNOWN,
         "title": "", "detail": "", "remedy": "", "risk_id": ""}]}


def test_the_optimize_check_without_names_or_readings_says_so_with_empty_text():
    out = CloudMixin.meta_optimize_check(check_mixin({}, {"workspace_id": "ws-dev"}), "dev")
    assert [(w["workspace_id"], w["workspace_name"], w["result"]) for w in out["workspaces"]] == [
        ("ws-dev", "", risk.UNKNOWN), ("ws-serve", "", risk.UNKNOWN)]


def test_the_optimize_check_for_some_entities_reads_only_their_workspaces():
    m = check_mixin({}, {"workspace_id": "ws-dev", "workspace_name": "Sales Dev"})
    out = CloudMixin.meta_optimize_check(m, "dev", ["gold.sales", "bronze.raw"])
    assert [(w["workspace_id"], w["tables"]) for w in out["workspaces"]] == [("ws-serve", ["dbo.sales"])]


# ---- Optimize after a run, and the tables maintenance looks after


def test_only_tables_a_run_wrote_rows_to_are_maintained_after_it(compute, monkeypatch):
    project = compute._project()
    maintained = []
    monkeypatch.setattr(compute, "_maintain", lambda store, rows, env, risk_ids=None: maintained.extend(rows) or [])
    results = [{"entity": "silver.orders", "status": "ok", "rows_written": 0},
               {"entity": "gold.fact_sales", "status": "failed", "rows_written": 3},
               {"entity": f"meta.{next(iter(META_SCHEMAS))}", "status": "ok", "rows_written": 1},
               {"entity": "silver.customers", "status": "ok", "rows_written": 2}]
    compute._after_run(project, "dev", compute.store, results)
    assert [r["entity"] for r in maintained] == ["silver.customers"]


def test_the_meta_tables_keep_seven_days_of_history():
    project = SimpleNamespace(entities={}, layer_names=lambda: [])
    rows = CloudMixin._managed_tables(SimpleNamespace(), project)
    assert rows and {r["retention_days"] for r in rows} == {7} and {r["lakehouse"] for r in rows} == {"LH_Meta"}


# ---- the item Publish writes, found by the link this app recorded (PB-018)

from pathlib import Path  # noqa: E402

from fabric_desktop import notebook_files  # noqa: E402
from fabric_desktop.notebook import to_ipynb  # noqa: E402


class LinkFabric:
    def __init__(self, by_id=None, by_name=()):
        self.by_id, self.by_name, self.deleted, self.schedules_of = by_id or {}, list(by_name), [], []

    def get_notebook(self, ws, item_id):
        return self.by_id.get(item_id)

    def list_notebooks(self, ws):
        return self.by_name

    def list_schedules(self, ws, item_id, job):
        self.schedules_of.append(item_id)
        return []

    def delete_schedule(self, ws, item_id, job, schedule_id):
        self.deleted.append((ws, item_id, job, schedule_id))


class LinkService(CloudService):
    links: dict = {}

    @property
    def workload_store(self):
        return SimpleNamespace(links=lambda: self.links)


@pytest.fixture
def linked(tmp_path):
    s = LinkService(root=tmp_path / "app")
    s.links = {"NB": {"workspace_id": "ws1", "item_id": "it1"}}
    return s


def test_the_linked_item_is_the_one_publish_writes_whatever_its_name_now(linked):
    linked.fake = LinkFabric(by_id={"it1": {"displayName": "Renamed in Fabric"}})
    assert linked._published_item("NB", "ws1") == ({"displayName": "Renamed in Fabric", "id": "it1"}, "")
    assert linked._published_item("NB", "ws-other") == (None, "")


def test_a_deleted_linked_item_falls_back_to_the_one_with_the_name(linked):
    linked.fake = LinkFabric()
    assert linked._published_item("NB", "ws1") == (
        None, "The Fabric notebook NB was published to before was deleted, so a new one was made.")
    linked.fake = LinkFabric(by_name=[{"id": "it2", "displayName": "NB"}])
    assert linked._published_item("NB", "ws1") == ({"id": "it2", "displayName": "NB"}, "")


def test_a_name_in_fabric_stays_unless_the_notebook_was_renamed_here(linked):
    folder = Path(linked.root) / "published"
    item = {"id": "it1", "displayName": "Renamed in Fabric"}
    assert linked._fabric_item_name("Sales/NB", item) == "Renamed in Fabric"  # never published from here
    assert linked._fabric_item_name("Sales/NB", {"id": "it1"}) == "NB"  # an item without a name takes the notebook's
    nb = to_ipynb([{"type": "code", "source": "x = 1"}], None)
    notebook_files.remember_published(folder, "it1", "ws1", nb, name="NB")
    assert linked._fabric_item_name("Sales/NB", item) == "Renamed in Fabric"  # same name here as then
    notebook_files.remember_published(folder, "it1", "ws1", nb, name="Old name")
    assert linked._fabric_item_name("Sales/NB", item) == "NB"  # renamed here since


def test_publish_would_replace_another_item_with_the_name_when_the_linked_one_is_gone(linked):
    linked.fake = LinkFabric(by_name=[{"id": "it9", "displayName": "NB"}])
    assert linked._fabric_change("NB", "ws1", None) == {
        "changed": True, "item_id": "it9", "other_item": True,
        "fabric": [{"kind": "other item", "position": None, "diff": []}], "mine": []}


def test_a_linked_item_never_published_from_here_has_no_known_change(linked):
    linked.fake = LinkFabric(by_id={"it1": {"displayName": "NB"}})
    assert linked._fabric_change("NB", "ws1", None) is None


def test_schedules_are_those_of_the_linked_item_even_when_renamed_in_fabric(linked):
    linked.fake = LinkFabric(by_id={"it1": {"displayName": "Renamed in Fabric"}})
    linked.notebooks.save("NB", [{"type": "code", "source": "x = 1"}], None, None, WS)  # published to WS before
    out = linked.notebook_schedules("NB", workspace=WS)
    assert out["published"] is True and out["item_id"] == "it1" and linked.fake.schedules_of == ["it1"]
    linked.delete_notebook_schedule("NB", "s1", workspace=WS)
    assert linked.fake.deleted == [("ws1", "it1", "RunNotebook", "s1")]


def test_publish_check_without_a_known_change_says_unknown(step_svc, monkeypatch):
    s = step_svc
    monkeypatch.setattr(s, "_fabric_change", lambda name, ws, doc: None)
    assert s.notebook_publish_check("NB", [], workspace=WS) == {"changed": False, "known": False}
    assert s.notebook_publish_check("NB", []) == {"changed": False, "known": False}  # nowhere to publish


# ---- running and scheduling a published notebook


@pytest.fixture
def published(step_svc, monkeypatch):
    s = step_svc
    seen = {"jobs": [], "configs": [], "refs": []}
    monkeypatch.setattr(s, "_publish", lambda name, cells, ref, compute=None, workspace=None, **k: {
        "workspace_id": "ws1", "workspace_name": "Sales WS", "id": "it1", "url": "u"})
    monkeypatch.setattr(s, "_log_fabric_run", lambda record: record)
    s.fake.run_item_job = lambda ws, item, job, data=None: seen["jobs"].append(data) or "job-1"
    s.fake.create_schedule = lambda ws, item, job, config: seen["configs"].append(config)
    monkeypatch.setattr(s, "notebook_schedules", lambda name, ref=None, workspace=None: seen["refs"].append(ref) or {})
    s.seen = seen
    return s


def test_a_run_in_fabric_sends_the_bar_parameters_only_when_there_are_some(published, monkeypatch):
    s = published
    monkeypatch.setattr(s, "_bar_parameters", lambda name, cells: {"region": {"value": "EU", "type": "string"}})
    s.run_notebook_in_fabric("NB", [], None, workspace=WS)
    monkeypatch.setattr(s, "_bar_parameters", lambda name, cells: {})
    s.run_notebook_in_fabric("NB", [], None, workspace=WS)
    assert s.seen["jobs"] == [{"parameters": {"region": {"value": "EU", "type": "string"}}}, None]


def test_a_notebook_schedule_without_a_time_zone_is_in_utc_and_answers_for_its_location(published):
    s = published
    s.add_notebook_schedule("NB", [], None, {"kind": "daily", "times": ["06:00"]}, location_ref=LAKEHOUSE)
    assert s.seen["configs"][0]["localTimeZoneId"] == "UTC" and s.seen["refs"] == [LAKEHOUSE]


# ---- the run log: cancelling, ids, and how long things took

import re  # noqa: E402


def test_cancelling_marks_only_that_run(local_svc):
    s = local_svc
    cancelled = []
    s.fake = SimpleNamespace(cancel_job=lambda ws, item, job: cancelled.append(job))
    s._write_fabric_runs([{"job_id": "j1", "workspace_id": "ws1", "item_id": "it1", "status": "InProgress"},
                          {"job_id": "j2", "workspace_id": "ws1", "item_id": "it2", "status": "InProgress"},
                          {"job_id": "j3", "workspace_id": "ws1", "item_id": "it3", "status": "Completed"}])
    assert s.cancel_fabric_run("ws1", "it1", "j1") == {"cancelled": "j1"} and cancelled == ["j1"]
    marked = {r["job_id"]: "cancel_requested" in r for r in s._read_fabric_runs()}
    assert marked == {"j1": True, "j2": False, "j3": False}


def test_a_run_that_already_ended_names_itself_when_it_cant_be_cancelled(local_svc):
    s = local_svc
    s.fake = SimpleNamespace(cancel_job=lambda ws, item, job: None)
    s._write_fabric_runs([{"job_id": "j1", "workspace_id": "ws1", "item_id": "it1", "status": "Failed", "name": "Nightly"},
                          {"job_id": "j2", "workspace_id": "ws1", "item_id": "it2", "status": "Completed"}])
    with pytest.raises(ValueError, match=r"^Nightly already ended in Fabric \(Failed\)"):
        s.cancel_fabric_run("ws1", "it1", "j1")
    with pytest.raises(ValueError, match=r"^This run already ended in Fabric \(Completed\)"):
        s.cancel_fabric_run("ws1", "it2", "j2")


def test_a_fabric_step_request_id_is_the_time_and_six_hex_digits(step_svc):
    run = step_svc.run_notebook_fabric_step("NB", step_cells('spark.sql("OPTIMIZE gold.t VORDER")'), LAKEHOUSE)
    assert re.fullmatch(r"fds-\d{8}-\d{6}-[0-9a-f]{6}", run["request_id"])


def test_a_metadata_run_in_fabric_has_a_request_id_of_the_time_and_four_hex_digits():
    m = SimpleNamespace(_released=lambda p, env, store: p, _check_runnable=lambda p, store: None,
                        _fabric_bundle=lambda p, env, store: {"workspace_id": "ws", "runner_id": "r", "meta_lakehouse_id": "m"},
                        fabric_jobs=SimpleNamespace(run_item_job=lambda *a: "job-1"), _log_fabric_run=lambda r: r,
                        _env_cfg=lambda p, env: {})
    out = CloudMixin._run_in_fabric(m, SimpleNamespace(entities={"a": 1}), "test", None, None, None, False)
    assert re.fullmatch(r"fd-\d{8}-\d{6}-[0-9a-f]{4}", out[0]["request_id"])


def test_results_are_looked_up_among_the_200_newest_runs(step_svc, monkeypatch):
    asked = []
    monkeypatch.setattr(step_svc, "fabric_runs", lambda limit=50, refresh=True, job_ids=None: asked.append(limit) or [])
    with pytest.raises(KeyError):
        step_svc.notebook_fabric_step_result("fds-1")
    with pytest.raises(LookupError, match="no record of the Fabric run fd-1"):
        step_svc.meta_fabric_result("fd-1")
    assert asked == [200, 200]


def test_seconds_between_are_rounded_to_milliseconds():
    assert cloud_service._seconds("2026-11-09T10:00:00Z", "2026-11-09T10:00:01.23456Z") == 1.235


def test_a_profile_says_how_long_it_took_in_hundredths(local_svc, monkeypatch):
    s = local_svc
    clock = iter([100.0, 101.23456])
    monkeypatch.setattr(cloud_service.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(s, "_profile_input", lambda project, env, name, source: None)
    monkeypatch.setattr(cloud_service.profiling, "profile", lambda df, keys, rules: {"suggestions": []})
    name = next(iter(s._project().entities))
    assert s.meta_profile(name)["seconds"] == 1.23


# ---- metadata schedules in Fabric, and which stores are made when missing


def test_metadata_schedules_say_whether_the_runner_is_published(compute):
    assert {k: compute.meta_schedules("dev")[k] for k in ("published", "item_id")} == {"published": False, "item_id": ""}
    compute._fabric_bundle(compute._project(), "dev", compute.store)
    assert {k: compute.meta_schedules("dev")[k] for k in ("published", "item_id")} == {"published": True, "item_id": "runner1"}


def test_a_metadata_schedule_without_a_time_zone_is_in_utc_and_makes_only_devs_store(compute, monkeypatch):
    made = []
    store = compute.store
    monkeypatch.setattr(compute, "_store", lambda p, env, create=False: made.append(("store", env, create)) or store)
    monkeypatch.setattr(compute, "_keys_store", lambda p, env, create=False: made.append(("keys", env, create)) or store)
    compute.meta_run()  # Dev's tables exist, so the project is released
    made.clear()
    compute.meta_add_schedule("dev", {"kind": "daily", "times": ["05:00"]})
    assert compute.fake.schedules[0]["configuration"]["localTimeZoneId"] == "UTC"
    assert ("store", "dev", True) in made and ("keys", "dev", True) in made


def test_a_model_from_a_save_without_created_or_id_is_found_by_name(gold):
    gold.fake.models = [{"id": "m0", "displayName": "SM_Other"}, {"id": "m1", "displayName": "SM_Gold"}]
    gold.fake.save_semantic_model = lambda ws, name, parts, description="", replace=True: {"id": "m2"}
    built = gold.meta_build_model("test")
    assert built["id"] == "m2" and built["endpoint_refreshed"] is True  # the id the save returned
    gold.fake.save_semantic_model = lambda ws, name, parts, description="", replace=True: {}
    built = gold.meta_build_model("test")
    assert built["id"] == "m1" and built["created"] is False
    assert built["url"] == gold._model_url(built["workspace_id"], "m1") and built["url"]
    gold.fake.models = []
    built = gold.meta_build_model("test")
    assert built["id"] == "" and built["url"] == ""


def test_a_model_whose_definition_cant_be_read_isnt_replaced(gold):
    assert gold.meta_build_model("test")["id"] == "m1"

    def unreadable(ws, item_id, fmt=None):
        raise OSError("timed out")
    gold.fake.get_item_definition = unreadable
    with pytest.raises(RuntimeError, match=r"^Couldn't read SM_Gold's definition \(OSError: timed out\)"):
        gold.meta_build_model("test", replace=True)


def test_a_relationship_to_a_table_not_written_yet_isnt_listed_as_left_out(gold, tmp_path):
    import shutil
    shutil.rmtree(tmp_path / "onelake" / "LH_Gold" / "Tables" / "dbo" / "dim_customer")
    built = gold.meta_build_model("test")
    assert built["skipped"] == ["gold.dim_customer"] and built["tables"] == ["fact_sales"]
    assert built["relationships_left_out"] == []


def test_the_model_plan_takes_where_an_environment_keeps_its_tables_from_its_name(local_svc, monkeypatch):
    monkeypatch.setattr(local_svc, "_env_cfg", lambda project, env: {})
    assert local_svc.meta_model_plan("dev")["local"] is True
    plan = local_svc.meta_model_plan("test")
    assert plan["local"] is False
    assert {t["name"]: t["kind"] for t in plan["tables"]} == {"dim_customer": "dimension", "fact_sales": "fact"}


def test_the_model_plan_finds_the_model_by_name_and_reuses_its_last_definition(local_svc, monkeypatch):
    s = local_svc

    def no_read(ws, item_id, fmt=None):
        raise AssertionError("the plan reuses the definition read last")
    s.fake = SimpleNamespace(list_semantic_models=lambda ws: [{"id": "m9", "displayName": "SM_Other"}],
                             get_item_definition=no_read)
    monkeypatch.setattr(s, "_env_cfg", lambda project, env: {"workspace_id": "ws-t"})
    assert s.meta_model_plan("test", "gold")["existing"] is None
    s.fake.list_semantic_models = lambda ws: [{"id": "m1", "displayName": "SM_Gold"}]
    s.__dict__["_model_definitions"] = {("ws-t", "m1"): {}}
    assert s.meta_model_plan("test", "gold")["existing"]["id"] == "m1"


def test_two_notebooks_that_would_share_an_item_name_are_refused_naming_the_workspace():
    m = SimpleNamespace(workload_store=SimpleNamespace(links=lambda: {"Ops/Daily load": {"workspace_id": "ws1"}}),
                        notebooks=SimpleNamespace(_taken=lambda name: True))
    with pytest.raises(ValueError, match="^Ops/Daily load already publishes to the Fabric notebook Daily load in Sales WS,"):
        CloudMixin._fabric_name_free(m, "Sales/Daily load", WS)
    with pytest.raises(ValueError, match="in that workspace, and Fabric names it without its folder"):
        CloudMixin._fabric_name_free(m, "Sales/Daily load", {"workspace_id": "ws1"})


# ---- _maintain: a warned workspace without risk_ids, and the lease on this PC


def test_maintenance_with_no_accepted_warnings_says_why_a_warned_workspace_waits():
    m = SimpleNamespace(_maintenance_capacity=lambda ws: WARNED if ws == "ws-dev" else None)
    m._maintenance_gate = lambda *a: CloudMixin._maintenance_gate(m, *a)
    out = CloudMixin._maintain(m, TwoWorkspaces(), [dict(TABLES[0], retention_days=7)], "dev")
    assert [(r["status"], r["message"][:33]) for r in out] == [("Failed", "Not optimized (capacity warning):")]


def test_maintenance_on_this_pc_waits_up_to_two_minutes_for_the_tables_lease(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from fabric_desktop.meta.store import LocalStore
    waits = []

    class Leased(LocalStore):
        @contextmanager
        def compaction_lease(self, lakehouse, schema, table, wait=0):
            waits.append(wait)
            yield

    monkeypatch.setattr(cloud_service.maintenance, "compact_local", lambda uri, days: {"files": 1})
    out = CloudMixin._maintain(SimpleNamespace(), Leased(tmp_path / "dev", "dev"), [dict(TABLES[0], retention_days=7)], "dev")
    assert waits == [120] and out == [{"table": "dbo.orders", "status": "Completed", "files": 1}]


# ---- Monitor's status refresh: a 30-second budget, and stopping after two unreachable answers in a row


class Polled:
    def __init__(self, errors):
        self.errors, self.asked = errors, []

    def job_instance(self, ws, item, job):
        self.asked.append(job)
        if self.errors.get(job):
            raise self.errors[job]
        return {"status": "InProgress"}


def open_runs(*jobs):
    return [{"job_id": j, "workspace_id": "ws1", "item_id": "it1", "status": "InProgress"} for j in reversed(jobs)]


def test_a_run_fabric_no_longer_knows_resets_the_unreachable_count(local_svc):
    from fabric_desktop.fabric_api import FabricError
    busy = FabricError("busy", status=503)
    fabric = Polled({"j1": busy, "j2": FabricError("gone", status=404), "j3": busy})
    runs = open_runs("j1", "j2", "j3", "j4")  # asked newest first: j1 first
    assert local_svc._refresh_fabric_runs(fabric, runs) == set() and fabric.asked == ["j1", "j2", "j3", "j4"]
    fabric = Polled({"j1": busy, "j2": busy})
    runs = open_runs("j1", "j2", "j3")
    assert local_svc._refresh_fabric_runs(fabric, runs) == {id(runs[0])} and fabric.asked == ["j1", "j2"]


def test_no_status_is_asked_after_the_30_second_budget(local_svc, monkeypatch):
    clock = iter([0.0, 30.0, 30.5])
    monkeypatch.setattr(cloud_service.time, "monotonic", lambda: next(clock))
    fabric = Polled({})
    runs = open_runs("j1", "j2")
    assert local_svc._refresh_fabric_runs(fabric, runs) == {id(runs[0])} and fabric.asked == ["j1"]  # at 30 s, still asked


def test_table_health_lists_the_20_newest_maintenance_jobs_of_its_environment(local_svc):
    s = local_svc
    s._write_fabric_runs([{"job_id": f"j{i}", "kind": "Table maintenance", "env": "dev", "status": "Completed"}
                          for i in range(25)] + [{"job_id": "t1", "kind": "Table maintenance", "env": "test",
                                                  "status": "Completed"}])
    jobs = s.meta_health("dev")["maintenance"]
    assert [j["job_id"] for j in jobs] == [f"j{i}" for i in range(24, 4, -1)]


# ---- Profile's preview of cleaning rules, and Monitor with a broken project


def preview_input(local_svc, monkeypatch, n, dupes=False):
    import polars as pl
    keys = list(local_svc._project().entities["silver.orders"].keys)
    ids = [1 if dupes else i for i in range(n)]
    df = pl.DataFrame({**{k: ids for k in keys}, "status": [f"shipped {i}" for i in range(n)],
                       "loaded": list(range(n))})
    monkeypatch.setattr(local_svc, "_profile_input", lambda project, env, name, source: df)


def test_the_preview_shows_at_most_20_changed_rows_by_default(local_svc, monkeypatch):
    preview_input(local_svc, monkeypatch, 25)
    out = local_svc.meta_profile_preview("silver.orders", [{"column": "status", "case": "upper"}])
    assert out["changed"] == 25 and len(out["rows"]) == 20
    assert out["rows"][19] == {"status": "shipped 19", "status (after)": "SHIPPED 19"}


def test_rules_that_drop_rows_have_no_side_by_side_preview(local_svc, monkeypatch):
    preview_input(local_svc, monkeypatch, 3, dupes=True)
    out = local_svc.meta_profile_preview("silver.orders", [{"column": "status", "case": "upper"},
                                                            {"dedupe_keep": "newest:loaded"}])
    assert out == {"columns": ["status"], "rows": [], "changed": 5}


def test_monitor_says_why_a_project_that_cant_be_read_has_no_runs(local_svc, monkeypatch):
    def broken():
        raise ValueError("project.toml line 3: expected '='")
    monkeypatch.setattr(local_svc, "_project", broken)
    assert local_svc.meta_runs_all() == {"runs": [], "unavailable": {},
                                         "project_error": "ValueError: project.toml line 3: expected '='"}


def test_publish_to_a_location_only_goes_to_its_workspace(files_svc):
    out = files_svc._publish("NB", [{"type": "code", "source": "x = 1"}], None, location_ref=LAKEHOUSE)
    assert out["workspace_id"] == LAKEHOUSE["workspace_id"]


def test_a_source_that_lands_files_without_known_zones_gets_no_landing_map(compute):
    project = compute._project()
    entity = next(e for e in project.entities.values() if "source" in e.spec)
    entity.spec["source"]["landing"] = "sales"  # its own LH_Landing, as before zones
    assert compute.store.landing_zones is None
    compute._fabric_bundle(project, "dev", compute.store)
    config = json.loads((FolderFiles.root / "ws-test" / "id-LH_Meta" / "Files/_fabric_desktop/current"
                         / "config.json").read_text())
    assert "landing" not in config
