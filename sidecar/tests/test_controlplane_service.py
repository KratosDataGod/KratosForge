"""The KratosForge Environment's app calls (CP-009): previews bound to tokens, confirm and typed-name
gates, the policy switch, and connect, switch, disconnect and check. Nothing here reaches Fabric."""

from __future__ import annotations

import inspect

import pytest

from fabric_desktop import app_settings, audit, server
from fabric_desktop import controlplane_service as cpsvc
from fabric_desktop.controlplane import manifest as mf
from fabric_desktop.controlplane import providers as pv
from fabric_desktop.controlplane import store as st
from fabric_desktop.optimize.lakehouse import LocalLakehouse
from fabric_desktop.service import Service

WS = "11111111-1111-4111-8111-111111111111"
CENTRAL = "44444444-4444-4444-8444-444444444444"
TWO = "55555555-5555-4555-8555-555555555555"


class FakeFabric:
    def __init__(self):
        self.workspaces = [{"id": WS, "displayName": "Platform"}]
        self.lakehouses: dict[str, list[dict]] = {WS: []}
        self.created: list[tuple] = []

    def list_workspaces(self):
        return list(self.workspaces)

    def list_lakehouses(self, ws):
        return list(self.lakehouses.get(ws, []))

    def find_lakehouse(self, ws, name):
        return next((lh for lh in self.list_lakehouses(ws) if lh["displayName"] == name), None)

    def create_workspace(self, name, capacity_id="", description=""):
        ws = {"id": f"{len(self.created) + 2:08d}-2222-4222-8222-222222222222", "displayName": name}
        self.workspaces.append(ws)
        self.lakehouses[ws["id"]] = []
        self.created.append(("workspace", name))
        return ws

    def create_lakehouse(self, ws, name, description=""):
        lh = {"id": f"{len(self.created) + 2:08d}-3333-4333-8333-333333333333", "displayName": name}
        self.lakehouses.setdefault(ws, []).append(lh)
        self.created.append(("lakehouse", name))
        return lh


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    s = Service(root=tmp_path / "app")
    fabric = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fabric))
    lakes = tmp_path / "onelake"
    monkeypatch.setattr(Service, "_optimize_lake",
                        lambda self, ws, lh, schemas=None, token=None: LocalLakehouse(lakes / ws / lh, schemas is not False))
    monkeypatch.setattr(Service, "_cp_store", lambda self, ws, lh: st.local_store(lakes / ws / lh))
    prod: set[str] = set()
    monkeypatch.setattr(Service, "_prod_marked", lambda self, ws: ws in prod)
    s.fake, s.lakes, s.prod = fabric, lakes, prod
    return s


def _policy(tmp_path, **values):
    import json
    (tmp_path / "policy.json").write_text(json.dumps(values), encoding="utf-8")


def test_calls_are_registered_audited_and_gated():
    assert cpsvc.METHODS <= server.METHODS
    for read in ("cp_status", "cp_discover", "cp_plan", "cp_adopt_preview", "cp_check", "cp_cost"):
        assert not audit.audited(read), read
    assert not audit.audited("cp_upgrade_plan")
    for change in ("cp_create", "cp_adopt", "cp_upgrade", "cp_connect", "cp_switch", "cp_disconnect"):
        assert audit.audited(change), change
    # The go-ahead writes its audit line first, or the call doesn't run.
    assert audit.fails_closed("cp_create", {"token": "t", "confirmed": True})
    assert audit.fails_closed("cp_adopt", {"token": "t", "confirmed": True})
    assert audit.fails_closed("cp_upgrade", {"token": "t", "confirmed": True})
    # The caller names a preview; it can't send a plan, a request or a manifest.
    for method in ("cp_create", "cp_adopt", "cp_upgrade"):
        assert list(inspect.signature(getattr(Service, method)).parameters) == ["self", "token", "confirmed", "confirm_name"]


def test_plan_then_create_by_token_once(svc):
    plan = svc.cp_plan({"name": "Ours", "capacity_id": "cap"})
    assert not plan["blocked"] and plan["token"] and svc.fake.created == []
    assert [s["action"] for s in plan["steps"]][-1] == "write_manifest" and plan["typed_name"] == ""
    with pytest.raises(pv.NotConfirmed):
        svc.cp_create(plan["token"])
    out = svc.cp_create(plan["token"], confirmed=True)  # not used up by the refusal without confirm
    assert out["ok"] and [c[0] for c in svc.fake.created] == ["workspace", "lakehouse"]
    assert svc.cp_status()["active"]["environment_id"] == out["environment"]["environment_id"]
    with pytest.raises(PermissionError, match="expired or was already used"):
        svc.cp_create(plan["token"], confirmed=True)
    assert len(svc.fake.created) == 2


def test_a_blocked_plan_has_no_token_and_an_unknown_token_runs_nothing(svc):
    blocked = svc.cp_plan({"name": "Ours", "workspace_name": "Platform", "capacity_id": "cap"})
    assert blocked["blocked"] and blocked["token"] == ""
    with pytest.raises(PermissionError, match="expired"):
        svc.cp_create("made-up", confirmed=True)
    with pytest.raises(PermissionError, match="expired"):
        svc.cp_adopt(svc.cp_plan({"name": "Ours", "capacity_id": "cap"})["token"], confirmed=True)  # wrong kind
    assert svc.fake.created == []


def test_what_changed_since_the_preview_stops_create(svc):
    plan = svc.cp_plan({"name": "Ours", "capacity_id": "cap"})
    svc.fake.workspaces.append({"id": "w-new", "displayName": "kratosforge"})
    with pytest.raises(PermissionError, match="already exists"):
        svc.cp_create(plan["token"], confirmed=True)
    svc.fake.workspaces.pop()
    plan = svc.cp_plan({"name": "Ours", "workspace_id": WS})
    svc.fake.lakehouses[WS].append({"id": "lh-x", "displayName": "KratosForgeLakehouse"})
    with pytest.raises(PermissionError, match="already has a lakehouse"):
        svc.cp_create(plan["token"], confirmed=True)
    assert svc.fake.created == []


def test_a_plan_whose_steps_changed_without_being_blocked_is_refused(svc):
    from fabric_desktop.optimize import setup
    plan = svc.cp_plan({"name": "Ours", "workspace_id": WS, "lakehouse_id": CENTRAL})
    assert "initialize" in [s["action"] for s in plan["steps"]] and plan["token"]
    # Another PC set the lakehouse up meanwhile: the plan now only finds the tables. Not blocked, but not what was shown.
    setup.initialize(LocalLakehouse(svc.lakes / WS / CENTRAL), svc.optimize_installation, approve=True)
    with pytest.raises(PermissionError, match="changed since this was shown"):
        svc.cp_create(plan["token"], confirmed=True)
    assert st.local_store(svc.lakes / WS / CENTRAL).read() is None


def test_a_plan_request_takes_only_its_fields_as_short_text(svc):
    for request, why in [({"name": "x", "extra": 1}, "Unknown request fields: extra"),
                         ({"name": "x" * (cpsvc.MAX_FIELD + 1)}, "at most"),
                         ({"name": ["x"]}, "must be text")]:
        with pytest.raises(ValueError, match=why):
            svc.cp_plan(request)
    assert svc._cp_previews() == {} and svc.fake.created == []


def test_a_preview_left_too_long_expires(svc, monkeypatch):
    plan = svc.cp_plan({"name": "Ours", "capacity_id": "cap"})
    later = cpsvc.time.monotonic() + cpsvc.PREVIEW_SECONDS + 1
    monkeypatch.setattr(cpsvc.time, "monotonic", lambda: later)
    with pytest.raises(PermissionError, match="expired"):
        svc.cp_create(plan["token"], confirmed=True)


def test_only_a_few_previews_are_kept(svc):
    tokens = [svc.cp_plan({"name": f"n{i}", "capacity_id": "cap"})["token"] for i in range(cpsvc.MAX_PREVIEWS + 1)]
    assert len(svc._cp_previews()) == cpsvc.MAX_PREVIEWS
    with pytest.raises(PermissionError, match="expired"):
        svc.cp_create(tokens[0], confirmed=True)


def test_a_prod_workspace_needs_its_name_typed(svc):
    svc.prod.add(WS)
    plan = svc.cp_plan({"name": "Ours", "workspace_id": WS})
    assert plan["typed_name"] == "Platform"
    for typed in ("", "Platfrom", "platform", " Platform", "Platform "):
        with pytest.raises(PermissionError, match="type its name exactly"):
            svc.cp_create(plan["token"], confirmed=True, confirm_name=typed)
    out = svc.cp_create(plan["token"], confirmed=True, confirm_name="Platform")
    assert out["ok"] and svc.fake.created == [("lakehouse", "KratosForgeLakehouse")]


def test_the_policy_turns_it_off_but_a_pc_can_still_let_go(svc, tmp_path):
    out = svc.cp_create(svc.cp_plan({"name": "Ours", "capacity_id": "cap"})["token"], confirmed=True)
    env = out["environment"]["environment_id"]
    _policy(tmp_path, allow_central_environment=False)
    assert svc.cp_status()["allowed"] is False
    calls = [lambda: svc.cp_discover(), lambda: svc.cp_plan({"name": "x"}), lambda: svc.cp_create("t", confirmed=True),
             lambda: svc.cp_adopt_preview("x"), lambda: svc.cp_adopt("t", confirmed=True),
             lambda: svc.cp_connect(WS, "lh"), lambda: svc.cp_switch(env), lambda: svc.cp_check(),
             lambda: svc.cp_cost(), lambda: svc.cp_upgrade_plan(), lambda: svc.cp_upgrade("t", confirmed=True)]
    for call in calls:
        with pytest.raises(PermissionError, match="allow_central_environment"):
            call()
    assert svc.cp_disconnect(env)["forgotten"] and svc.cp_status()["active"] is None


def test_discover_lists_workspaces_then_environments(svc):
    assert svc.cp_discover()["namespaces"] == [{"id": WS, "display_name": "Platform"}]
    out = svc.cp_create(svc.cp_plan({"name": "Ours", "workspace_id": WS})["token"], confirmed=True)
    found = svc.cp_discover(WS)
    assert not found["truncated"]
    (env,) = found["found"]
    assert env["state"] == "environment" and env["lakehouse_id"] == out["lakehouse_id"]
    assert env["environment"]["environment_id"] == out["environment"]["environment_id"]


def test_connect_trusts_only_a_manifest_that_names_its_own_lakehouse(svc):
    out = svc.cp_create(svc.cp_plan({"name": "Ours", "workspace_id": WS})["token"], confirmed=True)
    lh = out["lakehouse_id"]
    svc.cp_disconnect(out["environment"]["environment_id"])
    assert svc.cp_connect(WS, lh)["environment"]["lakehouse_id"] == lh
    with pytest.raises(ValueError, match="doesn't hold"):
        svc.cp_connect(WS, "lh-empty")
    st.local_store(svc.lakes / WS / "lh-copy").create(out["manifest"])  # the same manifest copied elsewhere
    with pytest.raises(ValueError, match="names another lakehouse"):
        svc.cp_connect(WS, "lh-copy")


def test_switch_and_disconnect_change_only_this_pc(svc):
    a = svc.cp_create(svc.cp_plan({"name": "A", "workspace_id": WS, "lakehouse_name": "A"})["token"], confirmed=True)
    b = svc.cp_create(svc.cp_plan({"name": "B", "workspace_id": WS, "lakehouse_name": "B"})["token"], confirmed=True)
    ida, idb = a["environment"]["environment_id"], b["environment"]["environment_id"]
    assert svc.cp_status()["active"]["environment_id"] == idb
    assert svc.cp_switch(ida)["active"]["environment_id"] == ida
    with pytest.raises(ValueError, match="isn't connected"):
        svc.cp_switch("kfenv_" + "0" * 32)
    assert svc.cp_disconnect(ida) == {"forgotten": True, "active": None}
    assert st.local_store(svc.lakes / WS / a["lakehouse_id"]).read() is not None  # the environment is untouched


def _central(svc):
    svc.fake.lakehouses[WS].append({"id": CENTRAL, "displayName": "FabricDesktop_Central"})
    svc.optimize_connect(WS, CENTRAL, "Platform", "FabricDesktop_Central", initialize=True)


def test_adopt_writes_only_the_previewed_manifest(svc):
    _central(svc)
    before = sorted(LocalLakehouse(svc.lakes / WS / CENTRAL).table_names())
    preview = svc.cp_adopt_preview("Ours")
    assert preview["environment"]["lakehouse_id"] == CENTRAL and preview["token"]
    assert st.local_store(svc.lakes / WS / CENTRAL).read() is None  # a preview writes nothing
    with pytest.raises(pv.NotConfirmed):
        svc.cp_adopt(preview["token"])
    out = svc.cp_adopt(preview["token"], confirmed=True)
    assert out["manifest"] == preview["manifest"] and svc.cp_status()["active"]["lakehouse_id"] == CENTRAL
    assert sorted(LocalLakehouse(svc.lakes / WS / CENTRAL).table_names()) == before
    with pytest.raises(ValueError, match="Connect to it instead"):
        svc.cp_adopt_preview("Again")


def test_adopt_refuses_when_the_lakehouse_changed_or_was_adopted_meanwhile(svc):
    from fabric_desktop.optimize import setup
    _central(svc)
    preview = svc.cp_adopt_preview("Ours")
    lake = LocalLakehouse(svc.lakes / WS / CENTRAL)
    setup.write(lake, svc.optimize_installation, "schema_version",
                [{"record_id": "v99", "version": 99, "description": "later", "applied_at": "2026-11-20T00:00:00Z"}])
    with pytest.raises(PermissionError, match="changed since"):
        svc.cp_adopt(preview["token"], confirmed=True)

    other = svc.cp_adopt_preview  # a fresh lakehouse, adopted by another PC between preview and confirm
    svc.fake.lakehouses[WS].append({"id": TWO, "displayName": "Two"})
    svc.optimize_connect(WS, TWO, "Platform", "Two", initialize=True)
    preview = other("Ours")
    st.local_store(svc.lakes / WS / TWO).create(mf.new_manifest(
        name="Theirs", provider="fabric", namespace={"workspace_id": WS},
        analytical_store={"workspace_id": WS, "lakehouse_id": TWO}))
    with pytest.raises(ValueError, match="meanwhile"):
        svc.cp_adopt(preview["token"], confirmed=True)


def test_check_says_how_the_environment_is(svc, monkeypatch):
    assert svc.cp_check()["status"] == "" and svc.cp_check()["environment"] is None
    out = svc.cp_create(svc.cp_plan({"name": "Ours", "workspace_id": WS})["token"], confirmed=True)
    got = svc.cp_check()
    assert got["status"] == "Connected" and got["compatibility"]["state"] == "ready"

    def store_failing(exc):
        class Failing:
            def read(self):
                raise exc
        return lambda self, ws, lh: Failing()

    monkeypatch.setattr(Service, "_cp_store", store_failing(ConnectionError("network down")))
    assert svc.cp_check()["status"] == "Working offline"
    monkeypatch.setattr(Service, "_cp_store", store_failing(RuntimeError("403 Forbidden")))
    assert svc.cp_check()["status"] == "Limited permissions"
    monkeypatch.setattr(Service, "_cp_store", store_failing(mf.ManifestError("not JSON")))
    assert svc.cp_check()["status"] == "Connection failed"
    monkeypatch.setattr(Service, "_cp_store", lambda self, ws, lh: st.local_store(svc.lakes / "nowhere"))
    gone = svc.cp_check()
    assert gone["status"] == "Connection failed" and "isn't in its lakehouse" in gone["detail"]
    assert out["environment"]["environment_id"] == gone["environment"]["environment_id"]


def test_cost_reads_the_environments_measured_sync_overhead_and_leaves_the_rest_unknown(svc):
    import datetime as dt
    from fabric_desktop import measurement as ms
    from fabric_desktop.optimize.shared import Shared
    with pytest.raises(ValueError, match="Connect this PC"):
        svc.cp_cost()
    out = svc.cp_create(svc.cp_plan({"name": "Ours", "workspace_id": WS})["token"], confirmed=True)
    lake = LocalLakehouse(svc.lakes / WS / out["lakehouse_id"])
    at = dt.datetime.now(dt.timezone.utc).isoformat()
    ids = {"workload_id": "optimize.sync.analysis", "run_id": "r1", "source": "Optimize sync on this PC",
           "collected_at": at, "period_start": at, "period_end": at}
    Shared(lake, svc.optimize_installation).record_measurements([
        ms.make(ms.MEASURED, "optimize_sync_seconds", ms.SECONDS, 2.5, **ids),
        ms.make(ms.MEASURED, "optimize_sync_rows", ms.COUNT, 40, **ids)])
    got = svc.cp_cost(days=7)
    assert got["sync_overhead"]["optimize_sync_seconds"]["value"] == 2.5 and got["syncs"] == 1
    assert got["sync_overhead"]["optimize_sync_seconds"]["label"] == "Measured"
    assert got["this_pc"]["optimize_sync_seconds"]["value"] == 2.5 and got["pcs"] == 1
    assert got["fabric_cu"]["classification"] == "unknown" and got["fabric_cu"]["value"] is None
    assert got["cost"]["value"] is None and got["net"]["value"] is None
    with pytest.raises(ValueError, match="between 1 and 366, not 0"):
        svc.cp_cost(days=0)
    with pytest.raises(ValueError) as long:
        svc.cp_cost(days="9" * 500)
    assert len(str(long.value)) < 70


def test_cost_refuses_tables_a_newer_app_wrote(svc):
    from fabric_desktop.optimize import setup
    out = svc.cp_create(svc.cp_plan({"name": "Ours", "workspace_id": WS})["token"], confirmed=True)
    setup.write(LocalLakehouse(svc.lakes / WS / out["lakehouse_id"]), svc.optimize_installation, "schema_version",
                [{"record_id": "v99", "version": 99, "description": "later", "applied_at": "2026-11-20T00:00:00Z"}])
    with pytest.raises(PermissionError, match="(?i)newer"):
        svc.cp_cost()


def _adopted(svc):
    """An environment adopted from a central lakehouse holding only Optimize's tables: the other
    families' tables aren't there yet, so it needs an upgrade."""
    _central(svc)
    svc.cp_adopt(svc.cp_adopt_preview("Ours")["token"], confirmed=True)
    return LocalLakehouse(svc.lakes / WS / CENTRAL)


def test_upgrade_shows_a_plan_then_runs_it_by_token_once(svc):
    from fabric_desktop.optimize import schema
    with pytest.raises(ValueError, match="Connect this PC"):
        svc.cp_upgrade_plan()
    lake = _adopted(svc)
    others = [f for f in schema.FAMILIES.values() if f.prefix != "fdopt_"]
    assert others, "the test needs a second table family"
    assert svc.cp_check()["status"] == "Upgrade required"
    before = sorted(lake.table_names())
    plan = svc.cp_upgrade_plan()
    assert plan["token"] and not plan["blocked"] and plan["typed_name"] == ""
    assert [s["action"] for s in plan["steps"]] == ["initialize"] * len(others) + ["write_manifest"]
    assert sorted(lake.table_names()) == before  # a plan changes nothing
    with pytest.raises(pv.NotConfirmed):
        svc.cp_upgrade(plan["token"])
    out = svc.cp_upgrade(plan["token"], confirmed=True)
    assert out["ok"] and [s["status"] for s in out["steps"]] == ["done"] * (len(others) + 1)
    assert set(out["environment"]["families"]) == set(schema.FAMILIES)
    assert st.local_store(svc.lakes / WS / CENTRAL).read().manifest["schema"]["families"] == out["environment"]["families"]
    assert svc.cp_check()["status"] == "Connected"
    with pytest.raises(PermissionError, match="expired or was already used"):
        svc.cp_upgrade(plan["token"], confirmed=True)
    done = svc.cp_upgrade_plan()
    assert done["token"] == "" and "Nothing to upgrade" in done["blocked"][0]


def test_upgrade_refuses_what_changed_since_the_plan_and_needs_a_prod_name(svc):
    from fabric_desktop.optimize import schema, setup
    lake = _adopted(svc)
    plan = svc.cp_upgrade_plan()
    other = next(f for f in schema.FAMILIES.values() if f.prefix != "fdopt_")
    setup.initialize(lake, svc.optimize_installation, approve=True, family=other)  # another PC, meanwhile
    with pytest.raises(PermissionError, match="changed since"):
        svc.cp_upgrade(plan["token"], confirmed=True)
    svc.prod.add(WS)
    plan = svc.cp_upgrade_plan()
    assert plan["typed_name"] == "Platform"
    for typed in ("", "platform", "Platform "):
        with pytest.raises(PermissionError, match="type its name exactly"):
            svc.cp_upgrade(plan["token"], confirmed=True, confirm_name=typed)
    assert svc.cp_upgrade(plan["token"], confirmed=True, confirm_name="Platform")["ok"]


def test_upgrade_never_overwrites_a_manifest_another_pc_changed(svc, monkeypatch):
    _adopted(svc)
    plan = svc.cp_upgrade_plan()
    real = st.ManifestStore.replace

    def raced(self, current, manifest):
        raise st.ManifestChanged("Another installation changed this environment's manifest since it was read.")
    monkeypatch.setattr(st.ManifestStore, "replace", raced)
    out = svc.cp_upgrade(plan["token"], confirmed=True)
    assert not out["ok"] and out["steps"][-1]["step"] == "Manifest" and out["steps"][-1]["status"] == "failed"
    monkeypatch.setattr(st.ManifestStore, "replace", real)
    # The tables were upgraded but the manifest wasn't: the next plan only catches the manifest up.
    again = svc.cp_upgrade_plan()
    assert again["token"] and [s["action"] for s in again["steps"]] == ["write_manifest"]
    done = svc.cp_upgrade(again["token"], confirmed=True)
    assert done["ok"] and [s["step"] for s in done["steps"]] == ["Manifest"]
    assert "Nothing to upgrade" in svc.cp_upgrade_plan()["blocked"][0]


PLATFORM_WS = "66666666-6666-4666-8666-666666666666"
FUNCTIONS = "77777777-7777-4777-8777-777777777777"
SQL_DB = "88888888-8888-4888-8888-888888888888"
KEYS_WS = "99999999-9999-4999-8999-999999999999"


def _platform(monkeypatch, prod, keys=None):
    monkeypatch.setattr(Service, "_platform_config", lambda self: {"dev": {}, "test": {}, "prod": dict(prod)})
    monkeypatch.setattr(Service, "_keys_config", lambda self: dict(keys or {}))


def test_adopt_links_the_prod_platform_database_as_the_operational_store(svc, monkeypatch):
    # CP-ADR-005 (decided 2026-11-20): one KratosForge workspace; an existing Prod Platform's database is linked, by ids only.
    _central(svc)
    _platform(monkeypatch, {"workspace_id": PLATFORM_WS, "functions_id": FUNCTIONS, "sql_id": SQL_DB, "display_name": "x"},
              {"workspace_id": KEYS_WS, "functions_id": FUNCTIONS, "shared": True})
    preview = svc.cp_adopt_preview("Ours")
    op = preview["manifest"]["operational_store"]
    assert op["workspace_id"] == PLATFORM_WS and op["functions_id"] == FUNCTIONS and op["database_id"] == SQL_DB
    assert preview["manifest"]["deployments"] == {"prod": {"platform_workspace_id": PLATFORM_WS}, "keys": {"workspace_id": KEYS_WS}}
    assert preview["environment"]["has_operational_store"]
    out = svc.cp_adopt(preview["token"], confirmed=True)
    assert out["manifest"]["operational_store"]["functions_id"] == FUNCTIONS
    assert st.local_store(svc.lakes / WS / CENTRAL).read().manifest == out["manifest"]


def test_adopt_without_a_fabric_platform_stays_lakehouse_only(svc, monkeypatch):
    _central(svc)
    _platform(monkeypatch, {"workspace_id": PLATFORM_WS}, {"workspace_id": KEYS_WS, "functions_id": FUNCTIONS})  # no functions; keys not shared
    preview = svc.cp_adopt_preview("Ours")
    assert preview["manifest"]["operational_store"] is None and preview["manifest"]["deployments"] == {}


def test_adopt_refuses_when_the_platform_changed_since_the_preview(svc, monkeypatch):
    _central(svc)
    _platform(monkeypatch, {"workspace_id": PLATFORM_WS, "functions_id": FUNCTIONS})
    preview = svc.cp_adopt_preview("Ours")
    _platform(monkeypatch, {"workspace_id": PLATFORM_WS, "functions_id": SQL_DB})  # repointed meanwhile
    with pytest.raises(PermissionError, match="changed since"):
        svc.cp_adopt(preview["token"], confirmed=True)
    assert st.local_store(svc.lakes / WS / CENTRAL).read() is None
