"""A project's Fabric side on this PC (cloud_service.py), more of the paths the main tests don't
reach: a publish check that can't tell, a Fabric step whose output or tables can't be read,
schedules of a notebook that isn't published, maintenance that fails for one table, the run bundle
when Fabric or this PC's records misbehave, and profiles of what hasn't been written. Fabric and
OneLake are stand-ins; nothing reaches a tenant."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import cloud_service, maintenance, notebook_files
from fabric_desktop.errors import NotSetUp
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.meta.project import GateError
from fabric_desktop.service import Service
from tests.test_cloud import CloudService, FolderOneLake

WS = "ws-1"


@pytest.fixture
def svc(tmp_path):
    s = CloudService(root=tmp_path / "app")
    s.fake = SimpleNamespace(calls=[])
    s.meta_init(str(tmp_path / "proj"), "3")
    return s


def links(monkeypatch, svc, mapping):
    monkeypatch.setattr(type(svc), "workload_store", property(lambda self: SimpleNamespace(links=lambda: mapping)),
                        raising=False)


def test_seconds_with_an_end_without_a_zone_count_as_utc():
    assert cloud_service._seconds("2026-11-09T10:00:00Z", "2026-11-09T10:00:07") == 7.0


# ---- has the Fabric item changed since this app published it


def test_a_publish_check_without_a_workspace_doesnt_know(svc, monkeypatch):
    def nowhere(name, ref, workspace=None):
        raise ValueError("Pick a workspace")

    monkeypatch.setattr(svc, "_publish_workspace", nowhere)
    assert svc.notebook_publish_check("nb", []) == {"changed": False, "known": False}


def test_a_linked_item_that_cant_be_looked_up_stops_the_publish(svc, monkeypatch):
    links(monkeypatch, svc, {"nb": {"workspace_id": WS, "item_id": "item-1"}})

    def refused(name, ws):
        raise FabricError("throttled", status=429)

    monkeypatch.setattr(svc, "_published_item", refused)
    with pytest.raises(RuntimeError, match="Couldn't check whether nb changed in Fabric"):
        svc._fabric_change("nb", WS, {"cells": []})


def test_an_item_this_pc_kept_no_copy_of_or_deleted_in_fabric_isnt_known(svc, monkeypatch):
    links(monkeypatch, svc, {"nb": {"workspace_id": WS, "item_id": "item-1"}})
    item = ({"id": "item-1"},)
    assert svc._fabric_change("nb", WS, {"cells": []}, item) is None  # no copy of what was published
    notebook_files.remember_published(svc.root / "published", "item-1", WS, {"cells": []}, name="nb")

    def gone(ws, item_id, fmt):
        raise FabricError("not found", status=404)

    svc.fake.get_item_definition = gone
    assert svc._fabric_change("nb", WS, {"cells": []}, item) is None  # deleted: publish makes it again
    svc.fake.get_item_definition = lambda ws, item_id, fmt: (_ for _ in ()).throw(FabricError("busy", status=503))
    with pytest.raises(RuntimeError, match="so it wasn't published"):
        svc._fabric_change("nb", WS, {"cells": []}, item)


# ---- a Fabric step's result


def test_an_unknown_fabric_step_isnt_found(svc, monkeypatch):
    monkeypatch.setattr(svc, "fabric_runs", lambda limit=50, refresh=True, job_ids=None: [])
    with pytest.raises(KeyError, match="no Fabric step fds-1"):
        svc.notebook_fabric_step_result("fds-1")


def test_a_finished_step_whose_output_and_tables_cant_be_read_still_shows(svc, monkeypatch):
    run = {"request_id": "fds-1", "status": "Completed", "workspace_id": WS, "lakehouse_id": "lh-1",
           "tables": ["dbo.sales"], "item_id": "i", "job_id": "j"}
    monkeypatch.setattr(svc, "fabric_runs", lambda limit=50, refresh=True, job_ids=None: [run])
    monkeypatch.setattr(Service, "auth", property(lambda self: SimpleNamespace(storage_token=lambda: "tok")))
    monkeypatch.setattr(svc, "_schemas_enabled", lambda ws, lh: True)

    def unreadable(self, ws, lh, path):
        raise OSError("OneLake unreachable")

    monkeypatch.setattr(cloud_service.FileStore, "read_bytes", unreadable)

    def no_health(uri, options):
        raise RuntimeError("delta log unreadable")

    monkeypatch.setattr(maintenance, "health", no_health)
    out = svc.notebook_fabric_step_result("fds-1")
    assert out["status"] == "Completed" and out["output"] is None
    assert out["tables"] == [{"table": "dbo.sales", "v_ordered": None, "message": "RuntimeError: delta log unreadable"}]


# ---- notebooks' schedules


def test_without_a_workload_store_no_other_notebook_holds_the_name():
    assert cloud_service.CloudMixin._fabric_name_free(SimpleNamespace(), "Sales/Daily", {"workspace_id": WS}) is None


def test_a_notebook_that_isnt_published_has_no_schedules_to_list_or_remove(svc, monkeypatch):
    monkeypatch.setattr(svc, "_published_item", lambda name, ws: (None, ""))
    svc.fake.list_notebooks = lambda ws: []
    ref = {"workspace_id": WS, "workspace_name": "Sales", "lakehouse_id": "lh"}
    assert svc.notebook_schedules("nb", ref) == {"published": False, "schedules": [], "workspace_id": WS,
                                                 "workspace_name": "Sales"}
    with pytest.raises(LookupError, match="nb isn't published in Sales, so it has no schedules there"):
        svc.delete_notebook_schedule("nb", "s1", ref)


def test_removing_a_published_notebook_s_schedule(svc, monkeypatch):
    removed = []
    svc.fake.list_notebooks = lambda ws: [{"id": "item-1", "displayName": "nb"}]
    svc.fake.delete_schedule = lambda ws, item, job, sid: removed.append((ws, item, job, sid))
    svc.fake.list_schedules = lambda ws, item, job: []
    monkeypatch.setattr(svc, "_published_item", lambda name, ws: (None, ""))
    out = svc.delete_notebook_schedule("nb", "s1", {"workspace_id": WS, "lakehouse_id": "lh"})
    assert removed == [(WS, "item-1", cloud_service.NOTEBOOK_JOB, "s1")] and out["published"] is True


def test_removing_a_metadata_schedule_from_the_runner(svc, monkeypatch):
    removed = []
    svc.meta_set_environment(env="test", workspace_id=WS, workspace_name="Sales Test")
    svc.fake.list_notebooks = lambda ws: [{"id": "runner-1", "displayName": cloud_service.RUNNER}]
    svc.fake.delete_schedule = lambda ws, item, job, sid: removed.append((ws, item, job, sid))
    svc.meta_delete_schedule("test", "s9")
    assert removed == [(WS, "runner-1", "RunNotebook", "s9")]


# ---- table health and maintenance


def test_a_table_whose_lakehouse_isnt_in_the_environment_shows_missing(svc, monkeypatch):
    def no_lakehouse(uri, options, local=False):
        raise LookupError("LH_Gold isn't in this environment yet")

    monkeypatch.setattr(maintenance, "health", no_lakehouse)
    tables = svc.meta_health("dev")["tables"]
    assert tables and all(t["status"] == "missing" and t["exists"] is False for t in tables)
    assert tables[0]["message"] == "LH_Gold isn't in this environment yet"


def test_onelake_maintenance_goes_on_when_retention_or_one_table_fails(svc, tmp_path, monkeypatch):
    project = svc._project()
    store = FolderOneLake(tmp_path / "onelake", "test", ["LH_Silver", "LH_Gold"])  # no LH_Bronze: left out
    monkeypatch.setattr(svc, "_store", lambda p, env, create=False: store)

    def no_retention(uri, days, options):
        raise PermissionError("can't write the Delta log")

    monkeypatch.setattr(maintenance, "set_retention", no_retention)
    warned = []
    monkeypatch.setattr(cloud_service._log, "warning", lambda msg, *a: warned.append(msg % a))

    def maintain(ws, lh, table, schema, vacuum_retention=None):
        if table == "fact_sales":
            raise FabricError("capacity paused", status=430)
        return f"job-{table}"

    svc.fake.run_table_maintenance = maintain
    svc.fake.job_instance = lambda ws, item, job: {"status": "InProgress"}
    out = svc.meta_optimize("test")
    names = {o["table"]: o for o in out}
    assert names["dbo.fact_sales"]["status"] == "Failed" and "capacity paused" in names["dbo.fact_sales"]["message"]
    assert all(o["status"] != "Failed" for t, o in names.items() if t != "dbo.fact_sales")
    lakehouses = {t["lakehouse"] for t in svc._managed_tables(project)}
    assert "LH_Bronze" in lakehouses and len(out) < len(svc._managed_tables(project))
    assert warned and warned[0].startswith("Couldn't set Delta retention before table maintenance")


# ---- running the project in Fabric


def test_a_local_environment_cant_have_a_run_bundle(svc):
    project = svc._project()
    with pytest.raises(ValueError, match="keeps its tables on this PC"):
        svc._fabric_bundle(project, "dev", svc._store(project, "dev"))


def test_without_lh_meta_there_is_nowhere_for_the_bundle(svc, tmp_path):
    store = FolderOneLake(tmp_path / "onelake", "test", ["LH_Gold"])
    with pytest.raises(NotSetUp, match="LH_Meta doesn't exist in the test workspace yet"):
        svc._fabric_bundle(svc._project(), "test", store)


@pytest.mark.parametrize("records", ["[]", OSError("locked")])
def test_a_runner_fabric_returns_without_an_id_is_not_recorded(svc, tmp_path, monkeypatch, records):
    store = FolderOneLake(tmp_path / "onelake", "test", ["LH_Silver", "LH_Gold", "LH_Bronze", "LH_Meta"])
    if isinstance(records, str):
        svc._bundles_file.parent.mkdir(parents=True, exist_ok=True)
        svc._bundles_file.write_text(records, encoding="utf-8")  # not a dict: started again
    else:
        def locked(path, default):
            raise records

        monkeypatch.setattr(cloud_service.jsonfile, "read_json", locked)
    monkeypatch.setattr(Service, "auth", property(lambda self: SimpleNamespace(storage_token=lambda: "tok")))
    uploads = []
    monkeypatch.setattr(cloud_service.FileStore, "delete", lambda self, ws, lh, path: None)
    monkeypatch.setattr(cloud_service.FileStore, "upload", lambda self, ws, lh, path, data: uploads.append(path))
    monkeypatch.setattr(svc, "_uses_keys", lambda project, names=None: False)
    svc.fake.save_notebook = lambda ws, name, data: {}
    svc.fake.list_notebooks = lambda ws: []
    with pytest.raises(LookupError, match="was published but Fabric didn't return its id"):
        svc._fabric_bundle(svc._project(), "test", store)
    assert uploads  # the bundle went up; only the runner's id is missing
    assert not svc._bundles_file.exists() or svc._bundles_file.read_text(encoding="utf-8") == "[]"


def test_a_project_that_doesnt_pass_its_checks_isnt_sent_to_fabric(svc, monkeypatch):
    monkeypatch.setattr(cloud_service, "validate", lambda project, today: ["gold.fact_sales: no keys"])
    with pytest.raises(GateError):
        svc._check_runnable(svc._project(), object())


# ---- profiles


def test_the_output_of_an_entity_never_written_cant_be_profiled(svc):
    with pytest.raises(LookupError, match="silver.orders has not been loaded in dev yet"):
        svc.meta_profile("silver.orders", source=False)


def test_a_preview_whose_rules_name_no_column_changes_nothing(svc):
    assert [r["status"] for r in svc.meta_run()] == ["ok"] * 6
    out = svc.meta_profile_preview("silver.orders", [{"column": "no_such_column", "case": "upper"}])
    assert out["columns"] == [] and out["rows"] == []


class PlatformKeys:
    """Keys kept in the platform (keys_mod.is_service): Fabric can reach them only through its functions."""

    def __init__(self, functions_id=""):
        self.client = SimpleNamespace(workspace_id="ws-plat", functions_id=functions_id)

    def issue(self, *a):
        raise AssertionError("not called")


@pytest.mark.parametrize("functions_id", ["", "fn-1"])
def test_keys_in_the_platform_go_to_fabric_only_through_its_functions(svc, tmp_path, monkeypatch, functions_id):
    store = FolderOneLake(tmp_path / "onelake", "test", ["LH_Silver", "LH_Gold", "LH_Bronze", "LH_Meta"])
    monkeypatch.setattr(svc, "_uses_keys", lambda project, names=None: True)
    monkeypatch.setattr(svc, "_keys_store", lambda project, env, create=False: PlatformKeys(functions_id))
    sent = []
    monkeypatch.setattr(cloud_service, "bundle_files", lambda root, config: sent.append(config) or {"config.json": b"{}"})
    monkeypatch.setattr(Service, "auth", property(lambda self: SimpleNamespace(storage_token=lambda: "tok")))
    monkeypatch.setattr(cloud_service.FileStore, "delete", lambda self, ws, lh, path: None)
    monkeypatch.setattr(cloud_service.FileStore, "upload", lambda self, ws, lh, path, data: None)
    svc.fake.save_notebook = lambda ws, name, data: {"id": "runner-1"}
    if not functions_id:
        with pytest.raises(ValueError, match="keys are in the platform on this PC, which Fabric can't reach"):
            svc._fabric_bundle(svc._project(), "test", store)
        return
    where = svc._fabric_bundle(svc._project(), "test", store)
    assert where["runner_id"] == "runner-1"
    assert sent[0]["platform_keys"] == {"workspace_id": "ws-plat", "functions_id": "fn-1"}


@pytest.fixture
def onelake_gold(svc, tmp_path, monkeypatch):
    from tests.test_cloud import FakeFabric
    svc.fake = FakeFabric()
    project = svc._project()
    store = FolderOneLake(tmp_path / "onelake", "test", [l.lakehouse for l in project.layers] + ["LH_Meta"])
    monkeypatch.setattr(svc, "_store", lambda p, env, create=False: store if env == "test" else type(svc)._store(svc, p, env, create))
    return svc, project, store


def test_a_model_over_a_layer_with_nothing_written_says_to_run_first(onelake_gold):
    svc, _, _ = onelake_gold
    with pytest.raises(LookupError, match="No gold tables have been written in test yet"):
        svc.meta_build_model("test")


def test_a_model_waits_for_the_sql_endpoint_and_builds_when_its_refresh_fails(onelake_gold, monkeypatch):
    svc, project, store = onelake_gold
    assert [r["status"] for r in svc.meta_run()] == ["ok"] * 6
    for e in project.entities.values():
        lh = project.layer(e.layer).lakehouse
        store.write(lh, e.schema, e.table, svc._store(project, "dev").read(lh, e.schema, e.table))
    real = svc.fake.get_lakehouse
    svc.fake.get_lakehouse = lambda ws, lh: {"properties": {}}
    with pytest.raises(LookupError, match="SQL analytics endpoint isn't ready yet"):
        svc.meta_build_model("test")
    svc.fake.get_lakehouse = real

    def refresh_fails(ws, sql_id):
        raise FabricError("endpoint busy", status=503)

    svc.fake.refresh_sql_endpoint = refresh_fails
    built = svc.meta_build_model("test")
    assert built["id"] == "m1" and built["endpoint_refreshed"] is False
