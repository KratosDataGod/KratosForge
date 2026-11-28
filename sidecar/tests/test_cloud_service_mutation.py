"""cloud_service paths that mutation testing (batch 16, docs/quality/mutation-testing.md) showed no
test checked: the Fabric runs log (its default length, its 500-run cap, a new app folder), Monitor's
refresh after Fabric answers again, the signed-in client, the model link, profiling what is read
against what is written, a schedule that would replace changes made in Fabric, a publish Fabric
returns no id for, the engine a run is counted as, the folder-name clash check, a Fabric step whose
item comes back without an id, maintenance retention, and recording the Fabric bundle. Fabric and
OneLake are fakes; nothing reaches a tenant."""

from __future__ import annotations

import json
import types

import pytest

from fabric_desktop import maintenance
from fabric_desktop.fabric_api import FabricClient
from fabric_desktop.service import Service

from test_cloud import CloudService, FakeFabric, FolderOneLake, svc  # noqa: F401 - the fixture is used by name
from test_notebook_files import FakeFabric as FilesFabric, FilesService, ipynb

GUID_WS = "11111111-1111-1111-1111-111111111111"
GUID_LH = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def app_data(tmp_path, monkeypatch):
    """App data (settings, sync pairs) goes to this test's folder, never the real one."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _log(s, n):
    for i in range(n):
        s._log_fabric_run({"kind": "Notebook run", "name": f"n{i}", "workspace_id": "w", "item_id": "i",
                           "job_id": f"j{i}", "status": "Completed"})


# ---- the runs log

def test_the_runs_log_is_made_in_a_new_nested_app_folder(tmp_path):
    s = Service(root=tmp_path / "a" / "b" / "app")
    s._log_fabric_run({"job_id": "j1"})
    assert [r["job_id"] for r in s.fabric_runs(refresh=False)] == ["j1"]


def test_runs_lists_the_newest_fifty_by_default(tmp_path):
    s = Service(root=tmp_path)
    _log(s, 51)
    runs = s.fabric_runs(refresh=False)
    assert len(runs) == 50 and runs[0]["job_id"] == "j50" and runs[-1]["job_id"] == "j1"


def test_the_runs_log_keeps_the_last_500(tmp_path):
    s = Service(root=tmp_path)
    s._write_fabric_runs([{"job_id": f"j{i}", "status": "Completed"} for i in range(501)])
    kept = s._read_fabric_runs()
    assert len(kept) == 500 and kept[0]["job_id"] == "j1" and kept[-1]["job_id"] == "j500"


def test_one_answer_from_fabric_starts_the_unreachable_count_again(tmp_path, monkeypatch):
    s = Service(root=tmp_path)
    for name in ("j0", "j1", "j2", "j3"):  # oldest first
        s._log_fabric_run({"kind": "Notebook run", "name": name, "workspace_id": "w", "item_id": "i", "job_id": name})
    asked = []

    class Flaky:
        def job_instance(self, ws, item, job):
            asked.append(job)
            if job in ("j3", "j1"):
                raise ConnectionError("offline")
            return {"status": "Completed"}
    monkeypatch.setattr(s, "_quiet_fabric", lambda: Flaky())
    runs = {r["job_id"]: r for r in s.fabric_runs()}
    # j3 fails, j2 answers (the count starts again), j1 fails: one in a row, so j0 is still asked.
    assert asked == ["j3", "j2", "j1", "j0"]
    assert not any(r.get("stale") for r in runs.values())
    assert runs["j3"]["message"] == "Status not available: offline"
    assert runs["j2"]["status"] == "Completed" and runs["j3"]["status"] == "NotStarted"


def test_a_failed_status_check_keeps_the_message_it_already_had(tmp_path, monkeypatch):
    s = Service(root=tmp_path)
    s._log_fabric_run({"job_id": "j1", "workspace_id": "w", "item_id": "i", "message": "Queued by the schedule"})

    class Down:
        def job_instance(self, ws, item, job):
            raise ConnectionError("offline")
    monkeypatch.setattr(s, "_quiet_fabric", lambda: Down())
    assert s.fabric_runs()[0]["message"] == "Queued by the schedule"


# ---- signing in and links

class TokenService(Service):
    token = "tok"

    @property
    def auth(self):
        return types.SimpleNamespace(silent_token=lambda scopes: self.token)


def test_monitor_gets_a_fabric_client_only_when_already_signed_in(tmp_path):
    s = TokenService(root=tmp_path)
    assert isinstance(s._quiet_fabric(), FabricClient)
    s.token = ""
    assert s._quiet_fabric() is None


def test_the_model_link_opens_its_details_in_fabric():
    assert Service._model_url("ws1", "m1") == \
        "https://app.fabric.microsoft.com/groups/ws1/datasets/m1/details?experience=fabric-developer"


# ---- profiling

def test_profile_reads_the_source_or_the_table_written(svc):  # noqa: F811
    read = svc.meta_profile("silver.orders")
    written = svc.meta_profile("silver.orders", source=False)
    assert read["rows"] == 6 and written["rows"] == 5  # the engine drops a row the source has
    assert "_run_id" not in {c["name"] for c in read["columns"]}
    assert "_run_id" in {c["name"] for c in written["columns"]}


def test_profiling_a_written_table_suggests_checks_only(svc):  # noqa: F811
    # Bronze keeps " Shipped" as it came, so its table would suggest a trim; cleaning applies to what
    # is read, so only checks are suggested for the output.
    kinds = {s["kind"] for s in svc.meta_profile("bronze.orders", source=False)["suggestions"]}
    assert kinds == {"check"}


def test_preview_shows_only_the_source_rows_a_rule_changes(svc):  # noqa: F811
    preview = svc.meta_profile_preview("silver.orders", [{"column": "status", "case": "upper"}])
    # Six rows are read; "SHIPPED" is already upper case, so five change. The source isn't trimmed yet.
    assert preview["changed"] == 5 and len(preview["rows"]) == 5
    assert preview["rows"][0] == {"status": " Shipped", "status (after)": " SHIPPED"}
    assert all(r["status"] != r["status (after)"] for r in preview["rows"])


# ---- publishing

@pytest.fixture
def files_svc(tmp_path):
    s = FilesService(root=tmp_path)
    s.fake = FilesFabric()
    return s


WS = {"workspace_id": "ws1", "workspace_name": "Sales WS"}


def test_a_schedule_never_replaces_changes_made_in_fabric(files_svc):
    s = files_svc
    s.notebooks.save("NB", [{"type": "code", "source": "x = 1"}], None)
    first = s._publish("NB", [{"type": "code", "source": "x = 1"}], None, workspace=WS)
    s.fake.items[first["id"]]["ipynb"]["cells"][0]["source"] = "x = 2"  # edited in Fabric
    s.fake.create_schedule = lambda *a: pytest.fail("no schedule over Fabric's changes")
    with pytest.raises(RuntimeError, match="changed in Fabric"):
        s.add_notebook_schedule("NB", [{"type": "code", "source": "x = 3"}], None, {"kind": "daily", "times": ["05:00"]},
                                workspace=WS)
    assert s.fake.items[first["id"]]["ipynb"]["cells"][0]["source"] == "x = 2"


def test_a_publish_fabric_returns_no_id_for_is_not_recorded(files_svc):
    s = files_svc
    s.fake.save_notebook = lambda ws, name, data: {}
    s.fake.list_notebooks = lambda ws: [{"id": "other", "displayName": "Someone else's"}]
    out = s._publish("NB", [{"type": "code", "source": "x = 1"}], None, workspace=WS)
    assert out["id"] is None and out["item_name"] == "NB"
    assert "NB" not in s.workload_store.links()


@pytest.mark.parametrize("source, lakehouse, engine", [
    ("from fabric_desktop import lakehouse\nx = 1", True, "python"),
    ("from fabric_desktop import lakehouse\nx = 1", False, "spark"),   # no lakehouse: published as Spark
    ("df = spark.read.table('dbo.orders')", True, "spark"),
    ("x = 1", True, "spark"),
])
def test_a_fabric_run_is_counted_as_the_engine_it_was_published_for(files_svc, source, lakehouse, engine):
    lh = {"workspace_id": "ws1", "lakehouse_id": "lh1", "name": "Sales", "workspace_name": "Sales WS"} if lakehouse else None
    run = files_svc.run_notebook_in_fabric("NB", [{"type": "code", "source": source}], lh, workspace=WS)
    assert run["engine"] == engine and run["job_id"] == "job1"


def test_a_linked_notebook_with_another_name_doesnt_block_a_publish(files_svc):
    s = files_svc
    s.notebooks.save("Ops/Weekly load", [{"type": "code", "source": "x = 1"}], None)
    s._publish("Ops/Weekly load", [{"type": "code", "source": "x = 1"}], None, workspace=WS)
    out = s._publish("Sales/Daily load", [{"type": "code", "source": "y = 1"}], None, workspace=WS)
    assert out["item_name"] == "Daily load"
    s.notebooks.save("Ops/Daily load", [{"type": "code", "source": "z = 1"}], None)
    with pytest.raises(ValueError, match="already publishes to the Fabric notebook Daily load"):
        s._publish("Ops/Daily load", [{"type": "code", "source": "z = 1"}], None, workspace=WS)


def test_a_fabric_step_whose_item_comes_back_without_an_id_is_looked_up(files_svc):
    s = files_svc
    lh = {"workspace_id": GUID_WS, "lakehouse_id": GUID_LH, "name": "Sales"}
    s.fake.add("step-1", GUID_WS, "x", ipynb([]))
    started = []
    s.fake.save_notebook = lambda ws, name, data: s.fake.items.__setitem__("step-1", {"ws": ws, "name": name,
                                                                                         "ipynb": {}}) or {}
    s.fake.run_item_job = lambda ws, item, job, execution_data=None: started.append(item) or "job7"
    cells = [{"type": "code", "source": "spark.sql('OPTIMIZE dbo.orders VORDER')", "meta": {"fabric_step": True}}]
    run = s.run_notebook_fabric_step("NB", cells, lh)
    assert started == ["step-1"] and run["item_id"] == "step-1"


# ---- table maintenance

def test_maintenance_without_a_layer_retention_keeps_the_default_days(tmp_path):
    class Store:
        lakehouse_ids = {"LH": "lh1"}

        def uri(self, *a):
            return str(tmp_path / "t")

        def options(self):
            return None

        def workspace_of(self, lh):
            return "ws1"
    s = CloudService(root=tmp_path / "app")
    s.fake = FakeFabric()
    out = s._maintain(Store(), [{"lakehouse": "LH", "schema": "dbo", "table": "t", "retention_days": None}], "test")
    assert out[0]["job_id"] == "job-t"
    assert s.fake.retention == maintenance.fabric_retention(maintenance.RETENTION_HOURS // 24)


# ---- the Direct Lake model, health and Fabric compute on the Test environment

@pytest.fixture
def promoted(svc, tmp_path, monkeypatch):  # noqa: F811
    """Dev's tables copied to a "test" environment kept in a folder, as promote would."""
    project = svc._project()
    store = FolderOneLake(tmp_path / "onelake", "test", [l.lakehouse for l in project.layers] + ["LH_Meta"])
    for e in project.entities.values():
        lh = project.layer(e.layer).lakehouse
        store.write(lh, e.schema, e.table, svc._store(project, "dev").read(lh, e.schema, e.table))
    monkeypatch.setattr(svc, "_store", lambda p, env, create=False: store if env == "test" else type(svc)._store(svc, p, env, create))
    return svc


def test_building_over_an_existing_model_asks_first_and_names_it(promoted):
    svc = promoted
    svc.meta_build_model("test")
    confirm = svc.meta_build_model("test")["confirm"]
    assert confirm["name"] == "SM_Gold" and confirm["id"] == "m1"


def test_a_model_fabric_names_without_an_id_still_asks_first(promoted, monkeypatch):
    svc = promoted

    def exists(ws, name, parts, description="", replace=True):
        raise FileExistsError(f"{name} already exists")
    monkeypatch.setattr(svc.fake, "save_semantic_model", exists)
    assert svc.meta_build_model("test")["confirm"]["id"] == ""


def test_health_lists_only_this_environments_table_maintenance(svc, tmp_path):  # noqa: F811
    for kind, env in (("Table maintenance", "dev"), ("Table maintenance", "test"), ("Notebook run", "dev")):
        svc._log_fabric_run({"kind": kind, "env": env, "name": f"{kind} {env}", "job_id": f"{kind}-{env}",
                             "status": "Completed"})
    assert [r["job_id"] for r in svc.meta_health("dev")["maintenance"]] == ["Table maintenance-dev"]


def test_health_looks_at_the_last_hundred_runs(svc):  # noqa: F811
    svc._log_fabric_run({"kind": "Table maintenance", "env": "dev", "job_id": "old", "status": "Completed"})
    for i in range(100):
        svc._log_fabric_run({"kind": "Notebook run", "env": "dev", "job_id": f"n{i}", "status": "Completed"})
    assert svc.meta_health("dev")["maintenance"] == []


def test_an_environment_with_no_target_is_in_onelake_so_it_can_run_in_fabric(svc):  # noqa: F811
    project = svc._project()
    envs = {k: {kk: vv for kk, vv in v.items() if kk != "target"} for k, v in project.environments.items()}
    envs["test"] = {"workspace_id": "ws-test"}
    svc._write_environments(project, envs)
    assert svc.meta_set_compute("test", "fabric")["environments"]["test"]["compute"] == "fabric"


# ---- a Fabric step's result

def _step_runs(s, n_after=0):
    s._log_fabric_run({"kind": "Fabric step", "request_id": "r1", "job_id": "j1", "status": "InProgress",
                       "workspace_id": "w", "item_id": "i", "lakehouse_id": "l"})
    s._log_fabric_run({"kind": "Fabric step", "request_id": "r2", "job_id": "j2", "status": "InProgress",
                       "workspace_id": "w", "item_id": "i", "lakehouse_id": "l"})
    for i in range(n_after):
        s._log_fabric_run({"kind": "Notebook run", "job_id": f"n{i}", "status": "Completed"})


def test_a_step_result_is_the_run_asked_for(tmp_path):
    s = Service(root=tmp_path)
    _step_runs(s)
    assert s.notebook_fabric_step_result("r1")["job_id"] == "j1"
    assert s.notebook_fabric_step_result("r2")["job_id"] == "j2"


def test_a_step_result_is_looked_for_in_the_last_200_runs(tmp_path):
    s = Service(root=tmp_path)
    _step_runs(s, n_after=199)  # r2 is the 200th newest, r1 the 201st
    assert s.notebook_fabric_step_result("r2")["job_id"] == "j2"
    with pytest.raises(KeyError, match="no Fabric step r1"):
        s.notebook_fabric_step_result("r1")


def test_a_table_with_one_v_ordered_file_counts_as_v_ordered(tmp_path, monkeypatch):
    from fabric_desktop import cloud_service

    class Files:
        def __init__(self, token):
            pass

        def read_bytes(self, *a):
            return b'{"printed": "ok"}'
    s = TokenService(root=tmp_path)
    monkeypatch.setattr(TokenService, "auth", property(lambda self: types.SimpleNamespace(storage_token=lambda: "tok")))
    monkeypatch.setattr(cloud_service, "FileStore", Files)
    monkeypatch.setattr(s, "_schemas_enabled", lambda ws, lh: True, raising=False)
    monkeypatch.setattr(cloud_service.maintenance, "health", lambda uri, opts: {"exists": True, "files": 1,
                                                                                "last_optimize": None})
    monkeypatch.setattr(cloud_service.maintenance, "vorder_files", lambda uri, opts: {"files": 1, "v_ordered": 1,
                                                                                       "checked": 1})
    s._log_fabric_run({"kind": "Fabric step", "request_id": "r1", "job_id": "j1", "status": "Completed",
                       "workspace_id": GUID_WS, "item_id": "i", "lakehouse_id": GUID_LH, "tables": ["dbo.orders"]})
    out = s.notebook_fabric_step_result("r1")
    assert out["output"] == {"printed": "ok"}
    assert out["tables"] == [{"table": "dbo.orders", "v_ordered": True, "files": 1, "last_optimize": None,
                              "checked": 1, "v_ordered_files": 1}]


def test_a_metadata_run_result_is_the_run_asked_for(tmp_path):
    s = Service(root=tmp_path)
    for rid in ("r1", "r2"):
        s._log_fabric_run({"kind": "Metadata run", "request_id": rid, "job_id": f"j-{rid}", "status": "InProgress",
                           "message": f"run {rid}", "workspace_id": "w", "item_id": "i"})
    assert s.meta_fabric_result("r1") == {"status": "InProgress", "message": "run r1", "results": None}
    assert s.meta_fabric_result("r2")["message"] == "run r2"


def test_publishing_the_fabric_bundle_records_it_in_a_new_nested_app_folder(svc, tmp_path, monkeypatch):  # noqa: F811
    from fabric_desktop import cloud_service
    store = FolderOneLake(tmp_path / "onelake", "test", ["LH_Silver", "LH_Gold", "LH_Bronze", "LH_Meta"])
    monkeypatch.setattr(svc, "_uses_keys", lambda project, names=None: False)
    monkeypatch.setattr(cloud_service, "bundle_files", lambda root, config: {"config.json": b"{}"})
    monkeypatch.setattr(Service, "auth", property(lambda self: types.SimpleNamespace(storage_token=lambda: "tok")))
    monkeypatch.setattr(cloud_service.FileStore, "delete", lambda self, ws, lh, path: None)
    monkeypatch.setattr(cloud_service.FileStore, "upload", lambda self, ws, lh, path, data: None)
    svc.fake.save_notebook = lambda ws, name, data: {"id": "runner-1"}
    project = svc._project()
    svc.root = tmp_path / "new" / "app"
    where = svc._fabric_bundle(project, "test", store)
    assert where["runner_id"] == "runner-1"
    assert [v["runner_id"] for v in json.loads(svc._bundles_file.read_text(encoding="utf-8")).values()] == ["runner-1"]
