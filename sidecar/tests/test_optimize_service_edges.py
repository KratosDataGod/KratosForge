"""Optimize's app calls, the paths the main tests don't reach: a lakehouse that can't be read, a
conflict, results that can't be sent yet, the policy turned off after connecting, and the central
lakehouse's role change refusing or failing."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fabric_desktop import optimize_service
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.optimize import setup
from fabric_desktop.optimize.shared import Shared
from fabric_desktop.optimize_service import OptimizeMixin
from fabric_desktop.service import Service
from test_optimize_service import svc  # noqa: F401 - the fixture is used by name

SECRET = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlLWlzLWhlcmU"


def _connected(svc):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    return svc.optimize_status()["active"]["lakehouse_id"]


def test_the_lakehouse_is_opened_with_its_own_schemas_and_the_sign_in_token(svc, monkeypatch):  # noqa: F811
    made = []
    monkeypatch.setattr(optimize_service, "OneLakeLakehouse", lambda *a: made.append(a) or "lake")
    monkeypatch.setattr(Service, "_schemas_enabled", lambda self, ws, lh: lh == "with-schemas")
    token = lambda: "t"  # noqa: E731
    assert OptimizeMixin._optimize_lake(svc, "w1", "with-schemas") == "lake"
    OptimizeMixin._optimize_lake(svc, "w1", "flat", schemas=False, token=token)
    (ws, lh, schemas, used, cache), second = made
    assert (ws, lh, schemas, used, cache) == ("w1", "with-schemas", True, svc.auth.storage_token,
                                              svc.root / "optimize" / "cache")
    assert second[2] is False and second[3] is token


def test_discover_says_which_lakehouses_cant_be_read_and_why(svc, monkeypatch):  # noqa: F811
    svc.fake.lakehouses["w1"].append({"id": "lh-broken", "displayName": "broken"})
    real = Service._optimize_lake

    def lake(self, ws, lh, schemas=None, token=None):
        if lh == "lh-sales":
            raise RuntimeError("403 Forbidden")
        if lh == "lh-broken":
            raise RuntimeError("the folder listing came back garbled")
        return real(self, ws, lh, schemas, token)
    monkeypatch.setattr(Service, "_optimize_lake", lake)
    found = {f["lakehouse_name"]: f for f in svc.optimize_discover("w1")}
    assert found["Sales"]["error"] == "No access" and found["Sales"]["optimize"] is False
    assert found["broken"]["error"] == "Couldn't be read"


def test_connect_refuses_a_lakehouse_whose_tables_arent_optimizes(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(setup, "inspect", lambda lake, *a: {"state": setup.CONFLICT, "message": "Not Optimize's tables."})
    with pytest.raises(ValueError, match="Not Optimize's tables"):
        svc.optimize_connect("w1", "lh-sales", initialize=True)
    assert svc.optimize_status()["connections"] == []


def test_nothing_is_sent_without_a_lakehouse_this_person_can_write(svc):  # noqa: F811
    assert svc._optimize_flush() == {"sent": 0, "dropped": 0, "waiting": 0}
    lakehouse = _connected(svc)
    svc.optimize_outbox.add(lakehouse, "analysis", {"run": {"run_id": "r1"}}, "down")
    svc.optimize_registry.add(dict(svc.central_lakehouse(), access="read"))
    assert svc._optimize_flush() == {"sent": 0, "dropped": 0, "waiting": 0}
    assert svc.optimize_outbox.status(lakehouse)["pending"] == 1  # kept for when write access comes


@pytest.mark.parametrize("exc", [ConnectionError("network down"), PermissionError("403 Forbidden")])
def test_a_waiting_item_that_still_cant_go_stops_the_rest(svc, monkeypatch, exc):  # noqa: F811
    lakehouse = _connected(svc)
    for n in (1, 2):
        svc.optimize_outbox.add(lakehouse, "analysis", {"run": {"run_id": f"r{n}"}}, "down")
    tried = []

    def record(self, run, *a):
        tried.append(run["run_id"])
        raise exc
    monkeypatch.setattr(Shared, "record_analysis", record)
    out = svc.optimize_switch(lakehouse)["sync"]
    assert out == {"sent": 0, "dropped": 0, "waiting": 2, "error": str(exc)}
    assert tried == ["r1"]  # the second isn't tried while the lakehouse is out of reach
    assert str(exc) in svc.optimize_status()["outbox"]["last_error"]


def test_connecting_stands_when_sending_what_waits_fails(svc, monkeypatch):  # noqa: F811
    lakehouse = _connected(svc)

    def broken(token=None):
        raise RuntimeError(f"outbox unreadable {SECRET}")
    monkeypatch.setattr(svc, "_optimize_flush", broken)
    out = svc.optimize_switch(lakehouse)
    assert out["active"]["lakehouse_id"] == lakehouse
    assert out["sync"]["error"].startswith("outbox unreadable") and SECRET not in out["sync"]["error"]


def test_a_write_needs_a_connected_lakehouse(svc):  # noqa: F811
    with pytest.raises(ValueError, match="Connect this PC"):
        svc._optimize_write("analysis", {"run": {"run_id": "r1"}})
    assert svc.optimize_outbox.status("")["pending"] == 0


def test_the_policy_turned_off_after_connecting_hides_the_central_lakehouse(svc, tmp_path):  # noqa: F811
    _connected(svc)
    assert svc.central_lakehouse()
    (tmp_path / "policy.json").write_text(json.dumps({"allow_optimize_lakehouse": False}))
    assert svc.central_lakehouse() is None
    assert svc.optimize_status()["active"]  # the connection is kept for when the policy allows it again


def test_central_family_refuses_tables_it_cant_set_up_even_with_approval(svc, monkeypatch):  # noqa: F811
    _connected(svc)
    monkeypatch.setattr(setup, "inspect", lambda lake, fam=None: {"state": setup.NEWER, "message": "Update KratosForge."})
    initialized = []
    monkeypatch.setattr(setup, "initialize", lambda *a, **k: initialized.append(a))
    with pytest.raises(PermissionError, match="^Update KratosForge.$"):
        svc.central_family(SimpleNamespace(prefix="fdtst_"), approve=True)
    assert initialized == []


FAMILY = SimpleNamespace(prefix="fdtst_")


@pytest.fixture
def restrict(svc, monkeypatch):  # noqa: F811
    """central_restrict with the family ready and OneLake security faked: ``plans`` are what each
    read of the roles finds, ``api.puts`` what was written."""
    _connected(svc)
    lake = SimpleNamespace(schema_names=lambda: ["access", "dbo"])
    monkeypatch.setattr(svc, "central_family", lambda fam, approve=False: (lake, svc.optimize_installation))
    monkeypatch.setattr(svc.auth, "signed_in_tenant", lambda: "tenant", raising=False)
    plans: list[list[str]] = []

    def plan(current, family, role, members, tenant, schemas):
        assert (family, tenant, schemas) == (FAMILY, "tenant", ["access", "dbo"])
        return {"changes": plans.pop(0), "default_reader_paths": ["/Tables/dbo"],
                "family_path": "/Tables/access", "roles": current}
    monkeypatch.setattr(optimize_service.roles, "restrict_plan", plan)
    api = SimpleNamespace(puts=[], list_data_access_roles_tagged=lambda ws, lh: ([], '"v1"'))
    api.put_data_access_roles = lambda ws, lh, r, etag=None: api.puts.append(etag)
    monkeypatch.setattr(Service, "_scoped", lambda self, scopes: api, raising=False)
    return SimpleNamespace(svc=svc, plans=plans, api=api)


def test_restrict_says_how_to_turn_on_onelake_security_when_its_roles_cant_be_read(restrict):
    def refused(ws, lh):
        raise RuntimeError(f"404 DataAccessSecurityNotEnabled {SECRET}")
    restrict.api.list_data_access_roles_tagged = refused
    with pytest.raises(PermissionError, match="Turn on OneLake security") as err:
        restrict.svc.central_restrict(FAMILY, "Readers", ["obj-1"])
    assert "DataAccessSecurityNotEnabled" in str(err.value) and SECRET not in str(err.value)
    assert restrict.api.puts == []


def test_restrict_changes_nothing_when_someone_already_made_the_change(restrict):
    restrict.plans += [["Hide /Tables/access"], []]  # the person looked, then an admin made it first
    out = restrict.svc.central_restrict(FAMILY, "Readers", ["obj-1"], approve=True, shown=["Hide /Tables/access"])
    assert out["changed"] is False and out["changes"] == []
    assert restrict.api.puts == []


def test_restrict_passes_on_a_failed_write_that_isnt_a_changed_role(restrict):
    restrict.plans += [["Hide /Tables/access"]] * 2

    def put(ws, lh, r, etag=None):
        raise FabricError("PUT failed with 400", "BadRequest", 400)
    restrict.api.put_data_access_roles = put
    with pytest.raises(FabricError, match="400"):
        restrict.svc.central_restrict(FAMILY, "Readers", ["obj-1"], approve=True, shown=["Hide /Tables/access"])


def test_sharing_runs_stops_when_the_lakehouse_refuses_the_read(svc, monkeypatch):  # noqa: F811
    _connected(svc)
    monkeypatch.setattr(Shared, "local_execution_ids", lambda self: (_ for _ in ()).throw(PermissionError("403 Forbidden")))
    with pytest.raises(PermissionError, match="403"):
        svc.optimize_share_local_runs()


def test_a_run_log_that_cant_be_read_leaves_only_that_environment_out(svc, tmp_path, monkeypatch):  # noqa: F811
    svc.meta_init(str(tmp_path / "repo" / "crm"), "3")
    assert [r["status"] for r in svc.meta_run()] == ["ok"] * 6
    me = svc.optimize_installation.id
    assert len(svc._optimize_metadata_runs(me)) == 6

    def store(project, env):
        raise OSError(f"run log for {env} is locked")
    monkeypatch.setattr(svc, "_store", store)
    assert svc._optimize_metadata_runs(me) == []
