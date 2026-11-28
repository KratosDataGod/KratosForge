"""controlplane/provisioning at its edges, from the mutation run (batch 466). Every step's ``creates``
flag, the blocked reasons an existing lakehouse gives, what each apply step says, where apply stops,
and the upgrade plan and apply on their own (FabricProvisioning with fakes, no Fabric)."""

from __future__ import annotations

import polars as pl
import pytest

from fabric_desktop.controlplane import manifest as mf
from fabric_desktop.controlplane import providers as pv
from fabric_desktop.controlplane import provisioning as prv
from fabric_desktop.controlplane import store as st
from fabric_desktop.optimize import schema, setup
from test_controlplane import LH, OID, WS, ProvFabric, provisioning

FAM = schema.Family("fdtst_", "Test", [schema.Migration(1, "Create", {
    "event": {"what": pl.String}, "schema_version": schema.TABLES_V1["schema_version"]})])
NEW = {"name": "Ours", "capacity_id": "cap"}


def creates(plan):
    return [(s.action, s.creates) for s in plan.steps]


# ---- plan: every step says whether it creates something


def test_every_new_step_creates_and_every_refusal_or_find_does_not(tmp_path):
    fab = ProvFabric(workspaces=[{"id": WS, "displayName": "Data"}])
    prov, _ = provisioning(tmp_path, fab)
    assert creates(prov.plan(NEW)) == [("create", True), ("create", True), ("initialize", True),
                                       ("write_manifest", True)]
    assert creates(prov.plan({"capacity_id": "cap"}))[0] == ("refuse", False)  # no name
    assert creates(prov.plan({"name": "x", "workspace_id": "nope"}))[0] == ("refuse", False)
    found = prov.plan({"name": "x", "workspace_id": WS})
    assert creates(found)[0] == ("find", False) and found.steps[0].target == "Data"
    no_ws = prov.plan({"name": "x", "capacity_id": "c", "workspace_name": "N", "lakehouse_id": LH})
    assert creates(no_ws)[1] == ("refuse", False)


def test_a_name_or_owner_the_manifest_would_refuse_blocks_the_plan(tmp_path):
    prov, _ = provisioning(tmp_path, ProvFabric())
    plan = prov.plan(NEW | {"owner_object_id": "not-a-guid"})
    assert plan.steps[-1].action == "write_manifest" and plan.steps[-1].blocked
    assert plan.blocked == [plan.steps[-1].blocked]
    assert prov.plan(NEW | {"owner_object_id": OID}).blocked == []


def test_an_existing_lakehouse_steps_create_nothing(tmp_path):
    fab = ProvFabric(workspaces=[{"id": WS, "displayName": "Data"}])
    prov, _ = provisioning(tmp_path, fab)
    empty = prov.plan({"name": "x", "workspace_id": WS, "lakehouse_id": LH})
    assert creates(empty)[:2] == [("find", False), ("find", False)]
    assert empty.steps[2] == pv.PlanStep("initialize", "Optimize tables (fdopt_)", True, prv.TABLES_PERMISSION)
    setup.initialize(prov._open_lake(WS, LH, None), prov.installation, approve=True)
    ready = prov.plan({"name": "x", "workspace_id": WS, "lakehouse_id": LH})
    assert creates(ready)[:3] == [("find", False), ("find", False), ("find", False)]
    assert prov.apply(ready, confirmed=True)["ok"]
    held = prov.plan({"name": "x", "workspace_id": WS, "lakehouse_id": LH})
    assert creates(held)[1] == ("refuse", False)


def test_a_damaged_or_unreadable_lakehouse_is_a_blocked_step_with_its_reason(tmp_path):
    fab = ProvFabric(workspaces=[{"id": WS, "displayName": "Data"}])
    prov, _ = provisioning(tmp_path, fab)
    target = tmp_path / "lakes" / LH / st.FOLDER / f"{st.NAME}.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"{not json")
    damaged = prov.plan({"name": "x", "workspace_id": WS, "lakehouse_id": LH})
    assert creates(damaged)[1] == ("refuse", False)
    assert damaged.blocked[0].startswith("Its KratosForge manifest is damaged: ")
    target.unlink()

    def broken(ws, lh, schemas):
        raise RuntimeError("socket closed")

    prov._open_lake = broken
    unread = prov.plan({"name": "x", "workspace_id": WS, "lakehouse_id": LH})
    assert creates(unread)[1] == ("refuse", False) and "couldn't be read" in unread.blocked[0]
    assert "socket closed" in unread.blocked[0]

    class Denied(Exception):
        class response:  # noqa: N801 - shaped like requests' error
            status_code = 403

    def denied(ws, lh, schemas):
        raise Denied("no")

    prov._open_lake = denied
    assert prov.plan({"name": "x", "workspace_id": WS, "lakehouse_id": LH}).blocked == ["You can't read this lakehouse."]


# ---- apply


def test_a_blocked_plan_is_refused_with_its_own_reason_even_if_it_since_cleared(tmp_path):
    fab = ProvFabric(workspaces=[{"id": WS, "displayName": "KratosForge"}])
    prov, _ = provisioning(tmp_path, fab)
    shown = prov.plan(NEW)
    assert "already exists" in shown.blocked[0]
    fab.workspaces.clear()  # the name is free now, but the plan the person saw was blocked
    with pytest.raises(PermissionError, match="already exists"):
        prov.apply(shown, confirmed=True)
    assert fab.created == []


def test_each_step_says_what_it_did_and_a_new_lakehouse_is_opened_with_schemas(tmp_path):
    fab = ProvFabric()
    prov, _ = provisioning(tmp_path, fab)
    seen = []
    real = prov._open_lake
    prov._open_lake = lambda ws, lh, schemas: seen.append(schemas) or real(ws, lh, schemas)
    out = prov.apply(prov.plan(NEW), confirmed=True)
    assert out["ok"] is True and seen == [True]
    by = {s["step"]: s for s in out["steps"]}
    assert by["Workspace"] == {"step": "Workspace", "status": "done", "detail": "Created KratosForge"}
    assert by["Lakehouse"] == {"step": "Lakehouse", "status": "done", "detail": "Created KratosForgeLakehouse"}
    assert by["Manifest"] == {"step": "Manifest", "status": "done", "detail": "Wrote the manifest"}
    assert by["Optimize tables"]["status"] == "done" and by["Optimize tables"]["detail"]


def test_an_existing_lakehouse_is_opened_to_look_for_schemas(tmp_path):
    fab = ProvFabric(workspaces=[{"id": WS, "displayName": "Data"}])
    prov, _ = provisioning(tmp_path, fab)
    seen = []
    real = prov._open_lake
    prov._open_lake = lambda ws, lh, schemas: seen.append(schemas) or real(ws, lh, schemas)
    out = prov.apply(prov.plan({"name": "x", "workspace_id": WS, "lakehouse_id": LH}), confirmed=True)
    assert out["ok"] and seen[-1] is None


def test_a_failed_workspace_stops_before_the_lakehouse(tmp_path):
    fab = ProvFabric()
    prov, _ = provisioning(tmp_path, fab)

    def fails(*a, **k):
        raise RuntimeError("no capacity")

    fab.create_workspace = fails
    lakehouses = []
    fab.create_lakehouse = lambda *a, **k: lakehouses.append(a) or {"id": LH}
    out = prov.apply(prov.plan(NEW), confirmed=True)
    assert out["ok"] is False and [s["step"] for s in out["steps"]] == ["Workspace"] and lakehouses == []


def test_a_failed_family_stops_before_the_manifest(tmp_path, monkeypatch):
    fab = ProvFabric()
    prov, lakes = provisioning(tmp_path, fab)

    def fails(*a, **k):
        raise RuntimeError("tables locked")

    monkeypatch.setattr(setup, "initialize", fails)
    out = prov.apply(prov.plan(NEW), confirmed=True)
    assert out["ok"] is False and out["manifest"] is None
    assert [s["step"] for s in out["steps"]] == ["Workspace", "Lakehouse", "Optimize tables"]
    assert prov._manifest_store(out["workspace_id"], out["lakehouse_id"]).read() is None


def test_a_family_without_a_version_is_recorded_at_its_latest(tmp_path, monkeypatch):
    prov, _ = provisioning(tmp_path, ProvFabric())
    monkeypatch.setattr(setup, "initialize", lambda *a, **k: {"schema_version": None, "message": "Set up."})
    out = prov.apply(prov.plan(NEW), confirmed=True)
    assert out["ok"] is True and out["manifest"]["schema"]["families"] == {"fdopt_": schema.OPTIMIZE.latest()}
    assert {"step": "Optimize tables", "status": "done", "detail": "Set up."} in out["steps"]


def test_a_manifest_that_fails_is_not_ok(tmp_path):
    fab = ProvFabric(workspaces=[{"id": WS, "displayName": "Data"}])
    prov, _ = provisioning(tmp_path, fab)
    plan = prov.plan({"name": "x", "workspace_id": WS, "lakehouse_id": LH})

    class Refusing:
        def read(self):
            return None

        def create(self, manifest):
            raise st.ManifestExists("taken")

    prov._manifest_store = lambda ws, lh: Refusing()
    out = prov.apply(plan, confirmed=True)
    assert out["ok"] is False and out["steps"][-1]["status"] == "failed" and out["manifest"] is None


# ---- upgrade


def environment(tmp_path, families=None):
    """An environment made with Optimize's tables only, and a provisioning that also knows FAM."""
    fab = ProvFabric()
    made, _ = provisioning(tmp_path, fab)
    out = made.apply(made.plan(NEW), confirmed=True)
    assert out["ok"]
    prov, _ = provisioning(tmp_path, fab, families or [schema.OPTIMIZE, FAM])
    return prov, out["manifest"]


def test_an_upgrade_plan_steps_create_nothing_and_say_what_it_means(tmp_path):
    prov, m = environment(tmp_path)
    plan = prov.plan_upgrade(m)
    assert plan.steps == (
        pv.PlanStep("initialize", "Test tables (fdtst_)", True, prv.TABLES_PERMISSION, note=prv.UPGRADE_NOTE),
        pv.PlanStep("write_manifest", "Files/_kratosforge/environment.json", False, prv.TABLES_PERMISSION,
                    note=prv.MANIFEST_NOTE))
    assert plan.request == {"environment_id": m["environment_id"], "workspace_id": m["analytical_store"]["workspace_id"],
                            "lakehouse_id": m["analytical_store"]["lakehouse_id"]}
    done = provisioning(tmp_path, ProvFabric())[0].plan_upgrade(m)
    assert done.steps[0] == pv.PlanStep("refuse", "environment", False, blocked=prv.UPGRADE_DONE)


def test_an_upgrade_plan_for_a_newer_manifest_or_unreadable_lakehouse_is_blocked(tmp_path):
    prov, m = environment(tmp_path)
    newer = dict(m, manifest_version=mf.MANIFEST_VERSION + 1)
    plan = prov.plan_upgrade(newer)
    assert creates(plan) == [("refuse", False)] and "newer KratosForge" in plan.blocked[0]

    def broken(ws, lh, schemas):
        raise RuntimeError("socket closed")

    prov._open_lake = broken
    unread = prov.plan_upgrade(m)
    assert creates(unread) == [("refuse", False)] and "socket closed" in unread.blocked[0]


def test_an_upgrade_opens_a_store_without_a_schemas_flag_with_schemas(tmp_path):
    prov, m = environment(tmp_path)
    seen = []
    real = prov._open_lake
    prov._open_lake = lambda ws, lh, schemas: seen.append(schemas) or real(ws, lh, schemas)
    bare = dict(m, analytical_store={k: v for k, v in m["analytical_store"].items() if k != "schemas"})
    prov.plan_upgrade(bare)
    assert seen and all(s is True for s in seen)


def test_an_upgrade_plan_refuses_a_newer_family(tmp_path):
    prov, m = environment(tmp_path)
    lake = prov._open_lake(m["analytical_store"]["workspace_id"], m["analytical_store"]["lakehouse_id"], True)
    setup.initialize(lake, prov.installation, approve=True, family=FAM)
    setup.write(lake, prov.installation, "schema_version",
                [{"record_id": "v99", "version": 99, "description": "later", "applied_at": "2026-11-19T20:00:00Z"}],
                family=FAM)
    plan = prov.plan_upgrade(m)
    assert creates(plan)[0] == ("refuse", False) and "newer" in plan.blocked[0].lower()


def test_apply_upgrade_needs_confirmation_and_the_same_environment(tmp_path):
    prov, m = environment(tmp_path)
    plan = prov.plan_upgrade(m)
    with pytest.raises(pv.NotConfirmed):
        prov.apply_upgrade(plan, confirmed=False)
    other = pv.ProvisionPlan(plan.provider, plan.steps, dict(plan.request, environment_id="kfenv_" + "b" * 32))
    with pytest.raises(PermissionError, match="Something changed"):
        prov.apply_upgrade(other, confirmed=True)
    gone = pv.ProvisionPlan(plan.provider, plan.steps, dict(plan.request, lakehouse_id="99999999-9999-4999-8999-999999999999"))
    with pytest.raises(PermissionError, match="Something changed"):
        prov.apply_upgrade(gone, confirmed=True)
    edited = pv.ProvisionPlan(plan.provider, plan.steps[1:], plan.request)
    with pytest.raises(PermissionError, match="Something changed"):
        prov.apply_upgrade(edited, confirmed=True)
    assert "fdtst_" not in prov._manifest_store(m["analytical_store"]["workspace_id"],
                                                m["analytical_store"]["lakehouse_id"]).read().manifest["schema"]["families"]


def test_apply_upgrade_refuses_a_plan_that_is_now_blocked(tmp_path):
    prov, m = environment(tmp_path)
    plan = prov.plan_upgrade(m)
    lake = prov._open_lake(m["analytical_store"]["workspace_id"], m["analytical_store"]["lakehouse_id"], True)
    setup.initialize(lake, prov.installation, approve=True, family=FAM)
    setup.write(lake, prov.installation, "schema_version",
                [{"record_id": "v99", "version": 99, "description": "later", "applied_at": "2026-11-19T20:00:00Z"}],
                family=FAM)  # a newer app took the family meanwhile: the fresh plan is blocked
    with pytest.raises(PermissionError, match="(?i)newer"):
        prov.apply_upgrade(plan, confirmed=True)


def test_apply_upgrade_keeps_families_it_left_alone_and_the_rest_of_the_schema(tmp_path):
    prov, m = environment(tmp_path, families=[FAM])  # Optimize's family isn't in this provisioning's list
    ws, lh = m["analytical_store"]["workspace_id"], m["analytical_store"]["lakehouse_id"]
    cur = prov._manifest_store(ws, lh).read()
    extra = mf.updated(cur.manifest, {"schema": dict(cur.manifest["schema"], operational=5)})
    prov._manifest_store(ws, lh).replace(cur, extra)
    seen = []
    real = prov._open_lake
    prov._open_lake = lambda w, l, schemas: seen.append(schemas) or real(w, l, schemas)
    out = prov.apply_upgrade(prov.plan_upgrade(extra), confirmed=True)
    assert out["ok"] is True and seen and all(s is True for s in seen)
    assert out["manifest"]["schema"] == {"families": {"fdopt_": m["schema"]["families"]["fdopt_"], "fdtst_": 1},
                                         "operational": 5}
    assert [s["step"] for s in out["steps"]] == ["Test tables", "Manifest"]
    assert out["steps"][-1]["detail"] == "Recorded the new table versions"


def test_apply_upgrade_records_a_family_without_a_version_at_its_latest(tmp_path, monkeypatch):
    prov, m = environment(tmp_path)
    plan = prov.plan_upgrade(m)
    monkeypatch.setattr(setup, "initialize", lambda *a, **k: {"schema_version": None, "message": "Set up."})
    monkeypatch.setattr(setup, "inspect", lambda lake, fam=setup.CORE: {"schema_version": None} if fam is FAM
                        else {"schema_version": schema.OPTIMIZE.latest()})
    monkeypatch.setattr(prov, "plan_upgrade", lambda manifest: plan)
    out = prov.apply_upgrade(plan, confirmed=True)
    assert out["ok"] is True and out["manifest"]["schema"]["families"]["fdtst_"] == FAM.latest()


def test_a_failed_family_upgrade_stops_before_the_manifest(tmp_path, monkeypatch):
    prov, m = environment(tmp_path)
    plan = prov.plan_upgrade(m)

    def fails(*a, **k):
        raise RuntimeError("tables locked")

    monkeypatch.setattr(setup, "initialize", fails)
    out = prov.apply_upgrade(plan, confirmed=True)
    assert out["ok"] is False and out["manifest"] is None
    assert out["steps"] == [{"step": "Test tables", "status": "failed", "detail": out["steps"][0]["detail"]}]
    assert "tables locked" in out["steps"][0]["detail"]
    ws, lh = m["analytical_store"]["workspace_id"], m["analytical_store"]["lakehouse_id"]
    assert prov._manifest_store(ws, lh).read().manifest == m


def test_an_upgrade_plan_for_a_lakehouse_you_cant_read_says_so(tmp_path):
    prov, m = environment(tmp_path)

    class Denied(Exception):
        class response:  # noqa: N801 - shaped like requests' error
            status_code = 403

    def denied(ws, lh, schemas):
        raise Denied("no")

    prov._open_lake = denied
    assert prov.plan_upgrade(m).blocked == ["You can't read this lakehouse."]


def test_a_lake_that_doesnt_say_whether_it_has_schemas_is_recorded_with_them(tmp_path, monkeypatch):
    prov, _ = provisioning(tmp_path, ProvFabric())
    monkeypatch.setattr(setup, "initialize", lambda *a, **k: {"schema_version": 2, "message": "Set up."})
    prov._open_lake = lambda ws, lh, schemas: object()  # no ``schemas`` attribute
    out = prov.apply(prov.plan(NEW), confirmed=True)
    assert out["ok"] is True and out["manifest"]["analytical_store"]["schemas"] is True


def test_an_upgrade_of_a_manifest_without_a_schemas_flag_opens_the_lake_with_schemas(tmp_path):
    prov, m = environment(tmp_path)
    ws, lh = m["analytical_store"]["workspace_id"], m["analytical_store"]["lakehouse_id"]
    cur = prov._manifest_store(ws, lh).read()
    bare = dict(cur.manifest, analytical_store={k: v for k, v in m["analytical_store"].items() if k != "schemas"})
    prov._manifest_store(ws, lh).replace(cur, bare)  # another writer left the flag out: it's optional
    plan = prov.plan_upgrade(bare)
    seen = []
    real = prov._open_lake
    prov._open_lake = lambda w, l, schemas: seen.append(schemas) or real(w, l, schemas)
    assert prov.apply_upgrade(plan, confirmed=True)["ok"] is True
    assert seen and all(s is True for s in seen)


def test_a_newer_family_in_an_existing_lakehouse_is_a_refusal_that_creates_nothing(tmp_path):
    fab = ProvFabric(workspaces=[{"id": WS, "displayName": "Data"}])
    prov, _ = provisioning(tmp_path, fab)
    lake = prov._open_lake(WS, LH, None)
    setup.initialize(lake, prov.installation, approve=True)
    setup.write(lake, prov.installation, "schema_version",
                [{"record_id": "v99", "version": 99, "description": "later", "applied_at": "2026-11-19T20:00:00Z"}])
    plan = prov.plan({"name": "x", "workspace_id": WS, "lakehouse_id": LH})
    assert creates(plan)[2] == ("refuse", False) and plan.steps[2].target == "Optimize tables (fdopt_)"
