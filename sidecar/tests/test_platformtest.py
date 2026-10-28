"""The central platform flows of the smoke test (PB-052 part 1), with Fabric faked and the platform
on a local SQLite file (LocalPlatform runs the same core.py as UDF_Platform)."""

import json

import pytest

from fabric_desktop import platformtest
from fabric_desktop.central import core
from fabric_desktop.central import setup as setup_mod
from fabric_desktop.central.client import LocalPlatform

WS = "11111111-1111-1111-1111-111111111111"
ME = "ana@contoso.com"


class Fabric:
    def __init__(self, items=(), description="fabric-desktop-live-test", name="KF tenant test", fail_delete=()):
        self.items = [dict(i) for i in items]
        self.workspace = {"id": WS, "displayName": name, "description": description}
        self.fail_delete = set(fail_delete)
        self.deleted = []

    def get_workspace(self, ws):
        return self.workspace

    def list_items(self, ws):
        return self.items

    def delete_item(self, ws, item_id):
        self._delete(item_id)

    def delete_lakehouse(self, ws, item_id):
        name = next((i["displayName"] for i in self.items if i["id"] == item_id), None)
        self._delete(item_id)  # its SQL endpoint goes with it, as in Fabric
        self.items = [i for i in self.items if not (i.get("type") == "SQLEndpoint" and i["displayName"] == name)]

    def _delete(self, item_id):
        if item_id in self.fail_delete:
            raise RuntimeError("409 Conflict")
        self.deleted.append(item_id)
        self.items = [i for i in self.items if i["id"] != item_id]


class Setup:
    """Stands in for central/setup.py: makes each item once, then keeps it; its tables step fails until
    the UDF's connection is added, as in Fabric. ``recreate`` makes a broken setup that creates again."""

    def __init__(self, platform, fabric, connected=True, recreate=False):
        self.platform, self.fabric, self.connected, self.recreate = platform, fabric, connected, recreate
        self.calls = 0

    def __call__(self, fabric, token, env, name, capacity, existing):
        self.calls += 1
        cfg, steps = dict(existing), [{"step": "Platform workspace", "status": "done", "detail": "Kept KF tenant test"}]
        for item, key in platformtest.ITEMS.items():
            made = self.recreate or not cfg.get(key)
            if made:
                cfg[key] = f"{key}-{self.calls}"
                self.fabric.items.append({"id": cfg[key], "displayName": item, "type": platformtest.ITEM_TYPES[item]})
            steps.append({"step": item, "status": "done", "detail": ("Created " if made else "Kept ") + item})
        if self.connected:
            out = self.platform.call("ensure_schema")
            steps.append({"step": "Platform tables", "status": "done",
                          "detail": f"Created {len(out['created'])} tables" if out["created"] else "Tables were already there"})
        else:
            steps.append({"step": "Platform tables", "status": "failed", "detail": "No connection"})
        return {"env": env, "config": cfg, "steps": steps, "ready": all(s["status"] == "done" for s in steps)}


def harness(tmp_path, fabric=None, connected=True, **kw):
    fabric = fabric or Fabric()
    platform = LocalPlatform(tmp_path / "platform.db", user=lambda: ME)
    setup = kw.pop("setup", None) or Setup(platform, fabric, connected)
    test = platformtest.PlatformTest(WS, fabric, lambda: "tok", tmp_path / "out", client=lambda ws, fid: platform,
                                     setup=setup, user=lambda: ME, run_id="run1", **kw)
    return test, fabric, platform, setup


def by_id(result):
    return {c["id"]: c for c in result["checks"]}


def test_every_flow_runs_from_empty_and_everything_made_is_deleted(tmp_path):
    test, fabric, platform, setup = harness(tmp_path)
    result = test.run()
    checks = by_id(result)
    assert result["outcome"] == "passed", result["checks"]
    assert [checks[f"platform.{i}"]["status"] for i in ("finish_empty", "repair", "product", "keys", "exception")] == ["passed"] * 5
    assert checks["platform.restrict"]["status"] == "skipped" and "by hand" in checks["platform.restrict"]["detail"]
    assert "Kept" in checks["platform.repair"]["detail"] and setup.calls == 2
    assert "A looks up as" in checks["platform.keys"]["detail"]
    assert "asked, approved, active, then ended" in checks["platform.exception"]["detail"]
    assert platform.call("list_policy_exceptions", policy="fdlt-run1", active=True) == []
    # Every item is deleted by id, what depends on SQL_Platform first.
    assert fabric.deleted == ["varlib_id-1", "functions_id-1", "sql_id-1", "landing_id-1", "meta_id-1"]
    assert result["left_behind"] == [] and fabric.items == []
    ledger = json.loads((tmp_path / "out" / "platform-ledger.json").read_text())
    assert [e["name"] for e in ledger["items"]] == list(platformtest.ITEMS) and ledger["workspace_id"] == WS


@pytest.mark.parametrize("fabric, says", [
    (Fabric(description="Team workspace"), "doesn't contain fabric-desktop-live-test"),
    (Fabric(name="Sales Prod"), "looks like production"),
    (Fabric(items=[{"id": "real-meta", "displayName": "LH_Meta", "type": "Lakehouse"}]), "already holds LH_Meta"),
    (Fabric(items=[{"id": "nb", "displayName": "UDF_Platform", "type": "Notebook"}]),
     "a Notebook named UDF_Platform, where the platform's is a UserDataFunction"),
])
def test_a_workspace_it_may_not_change_is_refused_before_anything_is_made(tmp_path, fabric, says):
    test, fabric, _, setup = harness(tmp_path, fabric)
    result = test.run()
    assert result["outcome"] == "refused" and any(says in r for r in result["refused"])
    assert setup.calls == 0 and result["checks"] == [] and fabric.deleted == []


def test_a_policy_platform_workspace_is_refused(tmp_path):
    test, fabric, _, setup = harness(tmp_path, protected_ids={WS.upper()})
    assert any("policy file" in r for r in test.run()["refused"]) and setup.calls == 0


def test_upgrade_takes_over_a_previous_builds_platform_brings_its_schema_up_and_deletes_it(tmp_path):
    old = [{"id": f"old-{key}", "displayName": name, "type": platformtest.ITEM_TYPES[name]}
           for name, key in platformtest.ITEMS.items()]
    old.append({"id": "old-meta-endpoint", "displayName": "LH_Meta", "type": "SQLEndpoint"})  # the lakehouse's own
    test, fabric, platform, setup = harness(tmp_path, Fabric(items=old), upgrade=True)
    with platform._db() as db:  # what an older build left: an earlier schema version
        db.run(f"UPDATE {db.t('config', 'setting')} SET value = '3' WHERE name = 'schema_version'")
        db.commit()
    result = test.run()
    checks = by_id(result)
    assert checks["platform.finish_empty"]["status"] == "skipped"
    assert checks["platform.upgrade"]["status"] == "passed", checks["platform.upgrade"]
    assert f"schema version {core.SCHEMA_VERSION}" in checks["platform.upgrade"]["detail"]
    assert sorted(fabric.deleted) == sorted(i["id"] for i in old if i["type"] != "SQLEndpoint") and fabric.items == []


def test_upgrade_without_a_previous_platform_is_refused(tmp_path):
    test, _, _, setup = harness(tmp_path, upgrade=True)
    result = test.run()
    assert result["outcome"] == "refused" and "needs the platform a previous build made" in result["refused"][0]
    assert setup.calls == 0


def test_a_missing_udf_connection_waits_for_a_person_then_finishes(tmp_path):
    asked = []
    test, fabric, platform, setup = harness(tmp_path, connected=False)
    def pause(text):
        asked.append(text)
        setup.connected = True
    test.pause = pause
    result = test.run()
    assert result["outcome"] == "passed", result["checks"]
    assert len(asked) == 1 and "Manage connections" in asked[0] and '"platform"' in asked[0]


def test_without_a_person_a_missing_connection_fails_and_the_rest_is_skipped_and_cleaned_up(tmp_path):
    test, fabric, _, _ = harness(tmp_path, connected=False)
    result = test.run()
    checks = by_id(result)
    assert checks["platform.finish_empty"]["status"] == "failed" and "No connection" in checks["platform.finish_empty"]["detail"]
    assert checks["platform.product"]["status"] == "skipped" and "isn't ready" in checks["platform.product"]["detail"]
    assert fabric.items == [], "what it made before the failure is deleted"


def test_a_repair_that_makes_items_again_fails_flow_12(tmp_path):
    fabric = Fabric()
    platform = LocalPlatform(tmp_path / "p.db", user=lambda: ME)
    test, fabric, _, _ = harness(tmp_path, fabric, setup=Setup(platform, fabric, recreate=True))
    result = test.run()
    assert by_id(result)["platform.repair"]["status"] == "failed"
    assert "should keep what's there" in by_id(result)["platform.repair"]["detail"]
    assert fabric.items == [], "both rounds' items are in the ledger and deleted"


def test_a_schema_older_than_this_build_fails(tmp_path, monkeypatch):
    test, *_ = harness(tmp_path)
    monkeypatch.setattr(core, "SCHEMA_VERSION", core.SCHEMA_VERSION + 1)
    monkeypatch.setattr(platformtest.core, "SCHEMA_VERSION", core.SCHEMA_VERSION)
    test.client_for = lambda ws, fid: type("Old", (), {"call": staticmethod(lambda op, **k: {"schema_version": 1, "created": []})})()
    result = test.run()
    assert by_id(result)["platform.finish_empty"]["status"] == "failed"
    assert "schema is version 1" in by_id(result)["platform.finish_empty"]["detail"]


def test_an_item_that_cant_be_deleted_is_left_behind_and_the_rest_still_go(tmp_path):
    test, fabric, _, _ = harness(tmp_path, Fabric(fail_delete={"sql_id-1"}))
    result = test.run()
    assert [e["id"] for e in result["left_behind"]] == ["sql_id-1"] and "409" in result["left_behind"][0]["why"]
    assert fabric.deleted == ["varlib_id-1", "functions_id-1", "landing_id-1", "meta_id-1"]


def test_keep_leaves_the_platform_and_says_so(tmp_path):
    test, fabric, _, _ = harness(tmp_path, keep=True)
    result = test.run()
    assert fabric.deleted == [] and len(result["left_behind"]) == 5 and result["kept"]


def test_flow_16_runs_when_a_central_lakehouse_is_set_up_here(tmp_path):
    test, *_ = harness(tmp_path, restrict=lambda: "Restricted fdopt_ to role readers (1 change)")
    assert by_id(test.run())["platform.restrict"] == {"flow": 16, "id": "platform.restrict", "title": platformtest.FLOW_TITLES[16],
                                                      "status": "passed", "detail": "Restricted fdopt_ to role readers (1 change)"}


def test_the_plan_names_what_is_made_and_how_it_goes(tmp_path):
    test, *_ = harness(tmp_path)
    plan = "\n".join(test.plan())
    for name in platformtest.ITEMS:
        assert name in plan
    assert "fdlt_run1" in plan and "platform-ledger.json" in plan and "Delete every platform item" in plan
    assert all(name in setup_mod.__dict__.values() for name in platformtest.ITEMS)


def test_upgrade_matches_name_and_type_and_refuses_a_clash(tmp_path):
    old = [{"id": f"old-{key}", "displayName": name, "type": platformtest.ITEM_TYPES[name]}
           for name, key in platformtest.ITEMS.items()]
    found, clashes = platformtest._platform_items(old + [{"id": "ep", "displayName": "LH_Meta", "type": "SQLEndpoint"},
                                                         {"id": "ep2", "displayName": "SQL_Platform", "type": "SQLEndpoint"},
                                                         {"id": "sm", "displayName": "LH_Meta", "type": "SemanticModel"}])
    assert {n: i["id"] for n, i in found.items()} == {n: f"old-{k}" for n, k in platformtest.ITEMS.items()} and clashes == []
    probe = old + [{"id": "ep", "displayName": "LH_Meta", "type": "SQLEndpoint"},
                   {"id": "nb", "displayName": "UDF_Platform", "type": "Notebook"}]
    test, fabric, _, setup = harness(tmp_path, Fabric(items=probe), upgrade=True)
    result = test.run()
    assert result["outcome"] == "refused" and any("a Notebook named UDF_Platform" in r for r in result["refused"])
    assert setup.calls == 0 and fabric.deleted == [] and result["ledger"] == []
    _, clashes = platformtest._platform_items(old + [{"id": "dup", "displayName": "VL_Platform", "type": "VariableLibrary"}])
    assert clashes == ["two VariableLibrary items named VL_Platform"]
