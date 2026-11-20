"""The metadata service's gates and after-promote steps, and the edges of entities, environments and source
systems. Fakes for Fabric and the catalog; a real project on disk where the files matter."""

from types import SimpleNamespace

import polars as pl
import pytest

from fabric_desktop import meta_service as ms
from fabric_desktop.meta.project import GateError
from fabric_desktop.service import Service


class Cat:
    def __init__(self, **answers):
        self.answers = answers

    def call(self, op, **kw):
        answer = self.answers[op]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def bare(tmp_path):
    return Service(root=tmp_path / "app")


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


# ---------------------------------------------------------------- small paths


def test_a_capacity_with_nothing_to_say_is_no_finding(bare, monkeypatch):
    monkeypatch.setattr(bare, "_risk_capacity", lambda ws, a, b, c: {"observed_at": "now"})
    monkeypatch.setattr(ms.risk, "check_capacity", lambda finding, now: None)
    assert bare._maintenance_capacity("w1") is None


def test_an_environment_saved_without_a_workspace_or_target_keeps_its_target(svc):
    before = svc._project().environments.get("test", {}).get("target")
    svc.meta_set_environment("test")
    assert svc._project().environments.get("test", {}).get("target") == before


def test_an_entity_outside_the_layers_has_no_preview(svc, monkeypatch):
    project = svc._project()
    name = next(iter(project.entities))
    monkeypatch.setattr(svc, "_project", lambda: project)
    monkeypatch.setattr(svc, "_store", lambda p, env: SimpleNamespace(
        meta_where=lambda table, cond: pl.DataFrame({"entity": [name], "status": ["ok"], "steps": ["[]"]})))
    real = project.layer_names
    monkeypatch.setattr(project, "layer_names", lambda: [n for n in real() if n != project.entities[name].layer])
    out = svc.meta_entity(name)
    assert out["preview"] is None
    assert out["runs"] == [{"entity": name, "status": "ok"}]


def test_a_new_entity_keeps_the_owner_it_was_given(svc, monkeypatch):
    monkeypatch.setattr(Service, "auth", property(lambda self: pytest.fail("asked who is signed in")))
    svc.meta_new_entity("clean", "silver.returns", owner="ana")
    assert svc._project().entities["silver.returns"].spec.get("owner") == "ana"


def test_a_cascade_of_one_entity_backfills_it(bare, monkeypatch):
    project = SimpleNamespace()
    ran = []

    class Engine:
        def __init__(self, *a, **kw):
            pass

        def backfill(self, name):
            ran.append(("backfill", name))
            return [SimpleNamespace(to_dict=lambda: {"entity": name})]

        def run_many(self, names, backfill=None):
            ran.append(("run_many", names))
            return []
    monkeypatch.setattr(ms, "Engine", Engine)
    monkeypatch.setattr(bare, "_store", lambda p, env, create=False: "store")
    monkeypatch.setattr(bare, "_released", lambda p, env, store: p)
    monkeypatch.setattr(bare, "_uses_keys", lambda p, names: False)
    monkeypatch.setattr(bare, "_link_table", lambda p, env: None)
    monkeypatch.setattr(bare, "_table_lock", lambda p, env, store: None)
    monkeypatch.setattr(bare, "_after_run", lambda p, env, store, out: None)
    assert bare._run_here(project, "dev", ["silver.a"], cascade=True) == [{"entity": "silver.a"}]
    assert bare._run_here(project, "dev", ["silver.a", "silver.b"], cascade=True) == []
    assert ran == [("backfill", "silver.a"), ("run_many", ["silver.a", "silver.b"])]


# ---------------------------------------------------------------- promote


def _gates(svc, monkeypatch, project, **override):
    monkeypatch.setattr(svc, "_project", lambda: project)
    monkeypatch.setattr(svc, "_store", lambda p, env, create=False: f"store-{env}")
    monkeypatch.setattr(ms, "promote_checks", override.get("checks", lambda p, s, t, d: []))
    monkeypatch.setattr(svc, "_test_gate", lambda p: ([], []))
    monkeypatch.setattr(svc, "_product_gate", lambda p: [])
    monkeypatch.setattr(svc, "_breaking_gate", lambda p, t: ([], ["breaking note"]))
    monkeypatch.setattr(svc, "_deprecation_gate", lambda p: ([], []))
    monkeypatch.setattr(svc, "_window_gate", lambda p, t: ["outside the window"])
    monkeypatch.setattr(ms.release, "status", lambda root: {"clean": True})
    monkeypatch.setattr(svc, "_release_gate", lambda p, t, git: ([], ["release note"]))
    monkeypatch.setattr(svc, "_duties_gate", lambda p, s, t, src=None, separation=True: ([], []))
    monkeypatch.setattr(svc, "governance_promote_gate",
                        lambda t: {"errors": [], "excepted": [], "notes": []})


def test_a_source_that_cant_be_read_is_a_promote_check_error(bare, monkeypatch):
    project = SimpleNamespace(key_tables={}, source_systems=[], environments={}, version="3", root="r")

    def unreadable(p, s, t, d):
        raise LookupError("Test has no tables yet")
    _gates(bare, monkeypatch, project, checks=unreadable)
    out = bare.meta_promote_check("dev", "test")
    assert out["errors"] == ["Test has no tables yet"]


def test_after_a_promote_each_follow_up_step_reports_on_its_own(bare, monkeypatch):
    project = SimpleNamespace(key_tables={}, source_systems=["sap"], environments={"test": {"workspace_id": "w"}},
                              products={"customers": object()}, version="3", root="r")
    _gates(bare, monkeypatch, project)
    monkeypatch.setattr(bare, "_keys_store", lambda p, env, create=False: None)
    monkeypatch.setattr(ms, "promote", lambda p, src, target, today, release: {"release": {}})
    monkeypatch.setattr(ms.release, "record", lambda *a: "record")
    monkeypatch.setattr(bare, "_signed_in_object_id", lambda: "")
    monkeypatch.setattr(bare, "_compute", lambda p, env: "fabric")
    monkeypatch.setattr(bare, "_publish_varlib", lambda p, env: None)
    monkeypatch.setattr(bare, "mesh_publish", lambda env: {"published": ["customers"], "problems": ["no owner"]})
    monkeypatch.setattr(bare, "_fabric_bundle", lambda p, env, store: None)
    out = bare.meta_promote("dev", "test")
    assert "keys" not in out
    assert out["variable_library"] == "updated"
    assert out["breaking"] == ["breaking note"] and out["change_window"] == ["outside the window"]
    assert out["release"] == {"notes": ["release note"]}
    assert out["products"] == ["customers"] and out["product_problems"] == ["no owner"]
    assert out["fabric"] == "synced"

    def fails(*a):
        raise RuntimeError("offline")
    monkeypatch.setattr(bare, "_publish_varlib", fails)
    monkeypatch.setattr(bare, "mesh_publish", fails)
    monkeypatch.setattr(bare, "_fabric_bundle", fails)
    out = bare.meta_promote("dev", "test")
    assert out["variable_library"] == "not updated: offline"
    assert out["products"] == "not published: offline" and "product_problems" not in out
    assert out["fabric"] == "not synced: offline"
    # Products published cleanly, tables run on this PC: nothing more to say.
    monkeypatch.setattr(bare, "mesh_publish", lambda env: {"published": ["customers"]})
    monkeypatch.setattr(bare, "_compute", lambda p, env: "local")
    out = bare.meta_promote("dev", "test")
    assert out["products"] == ["customers"] and "product_problems" not in out and "fabric" not in out


def test_a_rollback_is_stopped_by_the_role_check(bare, monkeypatch):
    monkeypatch.setattr(bare, "_project", lambda: SimpleNamespace())
    monkeypatch.setattr(bare, "_duties_gate", lambda p, s, t, separation=True: (["not a contributor"], []))
    with pytest.raises(GateError, match="not a contributor"):
        bare.meta_rollback("prod", "2")


def test_a_rollback_in_fabric_syncs_the_release_it_went_back_to(bare, monkeypatch):
    # A Prod rollback asks for the Prod workspace's name to be typed (WR-250).
    project = SimpleNamespace(environments={"prod": {"workspace_name": "Sales Prod"}})
    monkeypatch.setattr(bare, "_project", lambda: project)
    monkeypatch.setattr(bare, "_duties_gate", lambda p, s, t, separation=True: ([], []))
    monkeypatch.setattr(bare, "_store", lambda p, env: "store")
    monkeypatch.setattr(ms, "rollback", lambda p, store, version, user, oid: {"project": "released", "version": version})
    monkeypatch.setattr(ms.duties, "current_user", lambda: "me")
    monkeypatch.setattr(bare, "_signed_in_object_id", lambda: "")
    monkeypatch.setattr(bare, "_compute", lambda p, env: "fabric")
    synced = []
    monkeypatch.setattr(bare, "_fabric_bundle", lambda p, env, store: synced.append(p))
    assert bare.meta_rollback("prod", "2", confirm_name="Sales Prod") == {"version": "2", "fabric": "synced"}
    assert synced == ["released"]

    def fails(*a):
        raise RuntimeError("offline")
    monkeypatch.setattr(bare, "_fabric_bundle", fails)
    assert bare.meta_rollback("prod", "2", confirm_name="Sales Prod")["fabric"] == "not synced: offline"


def test_dev_has_no_duties_and_an_unreadable_source_has_no_deployments(bare, monkeypatch):
    project = SimpleNamespace(environments={"test": {"separate_promoter": True, "target": "local"}}, version="3")
    assert bare._duties_gate(project, "dev", "dev") == ([], [])
    seen = []
    monkeypatch.setattr(ms.duties, "separation_problem", lambda *a: seen.append(a) or None)
    monkeypatch.setattr(ms.duties, "current_user", lambda: "me")
    monkeypatch.setattr(bare, "_signed_in_object_id", lambda: "oid")

    def unreadable(table):
        raise OSError("gone")
    assert bare._duties_gate(project, "dev", "test", src=SimpleNamespace(meta=unreadable)) == ([], [])
    assert seen[0][3] is None  # no one recorded as the last promoter


def test_gates_that_cant_reach_the_catalog_are_notes(bare, monkeypatch):
    project = SimpleNamespace(links={"a": object()}, products={"p": SimpleNamespace(status="published")})
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_products=RuntimeError("offline"),
                                                      change_window=RuntimeError("offline")))
    assert bare._deprecation_gate(project) == (
        [], ["couldn't check whether the products this project links to are deprecated (offline)"])
    assert bare._window_gate(project, "prod") == ["couldn't check the platform's change window (offline)"]
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(change_window={"window": {"days": ["Mon"]}},
                                                      list_products=RuntimeError("offline")))
    assert bare._window_gate(project, "prod") == [
        "couldn't check who reads what's changing for the change window (offline)"]


def test_the_product_gate_judges_on_git_alone_without_the_catalog(bare, monkeypatch):
    project = SimpleNamespace(products={"p": object()})
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(enrichment=RuntimeError("offline")))
    monkeypatch.setattr(ms.mesh, "project_name", lambda p: "crm")
    monkeypatch.setattr(ms.mesh, "publish_problems", lambda p, saved: [f"saved={saved}"])
    assert bare._product_gate(project) == ["saved={}"]


# ---------------------------------------------------------------- source systems


def test_a_refused_first_source_system_leaves_no_file(svc, monkeypatch):
    project = svc._project()
    path = ms.keys_mod.keys_dir(project.root) / ms.keys_mod.SOURCE_SYSTEMS_FILE
    if path.exists():
        path.unlink()
    monkeypatch.setattr(svc, "_read_platform_sources", lambda: [])
    monkeypatch.setattr(ms.keys_mod, "validate_keys", lambda p: ["source system 9: bad"])
    with pytest.raises(GateError, match="source system 9: bad"):
        svc.meta_save_source_system({"id": 9, "code": "SAP", "name": "SAP"})
    assert not path.exists()
