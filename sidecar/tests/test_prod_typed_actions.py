"""WR-250: promote, rollback and restore into Prod wait for Prod's workspace name typed, as Prod writes do.

The product owner chose it on 2026-11-19 ("Yes, require the name"). The engine checks ``confirm_name`` after the
role and duty gates and before anything changes, so a caller that skips the page's confirm changes nothing. The
name is a guard parameter, so the audit line is written before the change runs. Test and Dev ask for nothing.
"""

import shutil

import pytest

from fabric_desktop import estate_service
from fabric_desktop.meta.project import prod_name, prod_typed
from fabric_desktop.service import Service

NAME = "Sales Prod"


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "repo" / "crm"), "3")
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    s.meta_set_environment("test", target="local")
    s.meta_set_environment("prod", workspace_id="ws-prod", workspace_name=NAME, target="local")
    local = s._project().local_data
    for env in ("test", "prod"):
        shutil.copytree(local / "dev" / "LH_Bronze" / "Files", local / env / "LH_Bronze" / "Files")
    return s


def on_test(svc, **run):
    version = svc.meta_promote("dev", "test")["version"]
    results = svc.meta_run(env="test", **run)
    assert all(r["status"] == "ok" for r in results), results
    return version


def prod_releases(svc):
    return [r["version"] for r in svc.meta_releases("prod")["releases"]]


@pytest.mark.parametrize("typed", ["", "Prod", "sales prod", "Sales Prod ", "ws-prod", "Sales Test"])
def test_promote_to_prod_without_its_workspaces_exact_name_promotes_nothing(svc, typed):
    on_test(svc)
    with pytest.raises(PermissionError, match=f"{NAME} is the Prod workspace, so promoting to it runs only after you "
                                              "type its name to confirm. Nothing was promoted."):
        svc.meta_promote("test", "prod", confirm_name=typed)
    assert prod_releases(svc) == []


def test_promote_to_prod_with_its_name_lands_and_test_needs_none(svc):
    version = on_test(svc)  # promote to Test took no name
    assert svc.meta_promote("test", "prod", confirm_name=NAME)["version"] == version
    assert prod_releases(svc) == [version]


def test_a_gate_that_fails_is_said_before_the_name_is_asked(svc):
    from fabric_desktop.meta.project import GateError
    svc.meta_promote("dev", "test")  # Test hasn't run this version, so the gate stops it
    with pytest.raises(GateError):
        svc.meta_promote("test", "prod")


def test_rolling_prod_back_waits_for_the_name(svc):
    v1 = on_test(svc)
    svc.meta_promote("test", "prod", confirm_name=NAME)
    text = svc.meta_entity("silver.orders")["text"]
    svc.meta_save_entity("silver.orders", text.replace('expr = "amount - coalesce(discount, 0)"', 'expr = "amount"', 1))
    assert all(r["status"] == "ok" for r in svc.meta_run(["silver.orders"], backfill="full"))
    v2 = on_test(svc, backfill="full")
    svc.meta_promote("test", "prod", confirm_name=NAME)
    listed = svc.meta_releases("prod")
    assert listed["confirm_name"] == NAME and "confirm_name" not in svc.meta_releases("test")
    for typed in ("", "Prod", v1):
        with pytest.raises(PermissionError, match="Sales Prod is the Prod workspace, so rolling it back runs only after you type its name"):
            svc.meta_rollback("prod", v1, confirm_name=typed)
    assert prod_releases(svc)[0] == v2
    assert svc.meta_rollback("prod", v1, confirm_name=NAME)["version"] == v1
    svc.meta_rollback("test", v1)  # Test asks nothing


def test_restoring_a_prod_table_waits_for_the_name_before_anything_is_read(svc, monkeypatch):
    touched = []
    monkeypatch.setattr(Service, "_entity_uri", lambda self, *a: touched.append(a) or (_ for _ in ()).throw(
        LookupError("past the name check")))
    monkeypatch.setattr(estate_service.restore_mod, "restore", lambda *a, **k: touched.append(a))
    with pytest.raises(PermissionError, match="Sales Prod is the Prod workspace, so restoring silver.orders there runs "
                                              "only after you type its name to confirm. Nothing was restored."):
        svc.meta_restore("silver.orders", "prod", 1, reason="bad load")
    assert touched == []
    with pytest.raises(LookupError, match="past the name check"):
        svc.meta_restore("silver.orders", "prod", 1, confirm_name=NAME)
    with pytest.raises(LookupError, match="past the name check"):
        svc.meta_restore("silver.orders", "test", 1)  # Test asks nothing


def test_the_name_falls_back_to_the_workspace_id_then_to_prod():
    class P:
        def __init__(self, prod):
            self.environments = {"prod": prod}
    assert prod_name(P({"workspace_id": "ws-1", "workspace_name": "Sales Prod"})) == "Sales Prod"
    assert prod_name(P({"workspace_id": "ws-1"})) == "ws-1"
    assert prod_name(P({})) == "Prod"
    prod_typed(P({}), "test", "", "promoting to it", "Nothing was promoted.")  # only Prod asks
    with pytest.raises(PermissionError):
        prod_typed(P({}), "prod", "", "promoting to it", "Nothing was promoted.")


def test_confirm_name_is_a_guard_parameter_so_the_audit_line_comes_first():
    from fabric_desktop import audit
    assert "confirm_name" in audit.GUARD_PARAMS


def test_the_pages_send_the_typed_name():
    from pathlib import Path
    ui = Path(__file__).resolve().parents[2] / "app" / "ui"
    meta, dataops, estate = ((ui / f).read_text(encoding="utf-8") for f in ("meta.js", "dataops.js", "estate.js"))
    assert "typeToConfirm: prodName" in meta and "confirm_name: prodName" in meta
    assert "res.confirm_name" in dataops and "confirm_name: needs" in dataops
    assert "hist.confirm_name" in estate and "confirm_name: needs" in estate


# ---- WR-251: a workspace that a project opened on this PC named Prod asks the same, whatever this project calls it

def remember_as_prod(svc, workspace_id):
    svc.prod_workspaces_file.parent.mkdir(parents=True, exist_ok=True)
    svc.prod_workspaces_file.write_text(f'{{"prod": ["{workspace_id}"]}}', encoding="utf-8")


def test_a_test_workspace_another_project_named_prod_waits_for_its_name(svc, monkeypatch):
    svc.meta_set_environment("test", workspace_id="WS-SHARED", workspace_name="Sales Shared", target="local")
    remember_as_prod(svc, "ws-shared")  # the ids are compared without case, as for notebook cells
    assert svc.meta_promote_check("dev", "test")["confirm_name"] == "Sales Shared"
    with pytest.raises(PermissionError, match="Sales Shared is a workspace that a project opened on this PC named as "
                                              "Prod, so promoting to it runs only after you type its name to "
                                              "confirm. Nothing was promoted."):
        svc.meta_promote("dev", "test")
    assert svc.meta_releases("test")["releases"] == []
    version = svc.meta_promote("dev", "test", confirm_name="Sales Shared")["version"]
    assert svc.meta_releases("test")["confirm_name"] == "Sales Shared"
    with pytest.raises(PermissionError, match="so rolling it back runs only after"):
        svc.meta_rollback("test", version)
    touched = []
    monkeypatch.setattr(Service, "_entity_uri", lambda self, *a: touched.append(a) or (_ for _ in ()).throw(
        LookupError("past the name check")))
    with pytest.raises(PermissionError, match="so restoring silver.orders there runs only after"):
        svc.meta_restore("silver.orders", "test", 1)
    assert touched == []
    with pytest.raises(LookupError, match="past the name check"):
        svc.meta_restore("silver.orders", "test", 1, confirm_name="Sales Shared")


def test_a_workspace_no_project_named_prod_asks_nothing(svc):
    svc.meta_set_environment("test", workspace_id="ws-test", workspace_name="Sales Test", target="local")
    remember_as_prod(svc, "ws-elsewhere")
    assert "confirm_name" not in svc.meta_promote_check("dev", "test")
    assert svc.meta_promote("dev", "test")["version"]
