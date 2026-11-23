"""The central platform flows of the smoke test (platformtest.py), held to exact values: which items clash, the run
id, the plan with --platform-keep, the flow and id of every check on each kind of run, the folder and ledger it
writes, the product's detail, the exception's week and the active list it reads, a delete's reason cut to 200,
and when setup is waiting on the connection. Fabric is faked; the platform is a local SQLite file."""

from __future__ import annotations

import datetime as dt
import json
import re

from fabric_desktop import platformtest
from fabric_desktop.central import setup as setup_mod
from tests.test_platformtest import Fabric, harness

RUN = [(11, "platform.finish_empty"), (12, "platform.repair"), (13, "platform.product"), (14, "platform.keys"),
       (15, "platform.exception"), (16, "platform.restrict")]


def test_a_platform_items_own_sql_endpoint_isnt_a_clash_but_one_named_like_another_item_is():
    _, clashes = platformtest._platform_items([{"id": "e", "displayName": setup_mod.META, "type": "SQLEndpoint"}])
    assert clashes == []
    _, clashes = platformtest._platform_items([{"id": "e", "displayName": setup_mod.FUNCTIONS, "type": "SQLEndpoint"}])
    assert clashes == [f"a SQLEndpoint named {setup_mod.FUNCTIONS}, where the platform's is a UserDataFunction"]


def test_a_run_without_its_own_id_gets_twelve_hex_characters(tmp_path):
    test = platformtest.PlatformTest("ws", Fabric(), lambda: "t", tmp_path, client=lambda ws, fid: None)
    assert re.fullmatch(r"[0-9a-f]{12}", test.run_id) and test.name == f"fdlt_{test.run_id}"


def test_the_plan_says_when_items_are_kept(tmp_path):
    test, *_ = harness(tmp_path, keep=True)
    assert test.plan()[-1] == ("Delete every platform item it made or took over: not deleted, --platform-keep; "
                               f"the ledger is {tmp_path / 'out' / 'platform-ledger.json'}.")
    test, *_ = harness(tmp_path)
    assert test.plan()[-1] == f"Delete every platform item it made or took over; the ledger is {tmp_path / 'out' / 'platform-ledger.json'}."


def test_a_full_run_records_each_flow_and_what_it_did(tmp_path):
    test, fabric, platform, _ = harness(tmp_path)
    asked = []
    real = platform.call

    def call(op, **kw):
        asked.append((op, kw))
        return real(op, **kw)
    platform.call = call
    result = test.run()
    assert [(c["flow"], c["id"]) for c in result["checks"]] == RUN
    assert test.config["workspace_name"] == "KF tenant test"
    checks = {c["id"]: c for c in result["checks"]}
    assert checks["platform.product"]["detail"] == "Registered fdlt_run1/fdlt_run1_product (version 1); it's listed."
    until = (dt.date.today() + dt.timedelta(days=7)).isoformat()
    assert checks["platform.exception"]["detail"].endswith(f" to fdlt-run1 until {until}: asked, approved, active, then ended.")
    lists = [kw for op, kw in asked if op == "list_policy_exceptions"]
    assert lists == [{"policy": "fdlt-run1", "active": True}] * 2
    ledger = (tmp_path / "out" / "platform-ledger.json").read_text(encoding="utf-8")
    assert ledger == json.dumps(json.loads(ledger), indent=2)


def test_an_upgrade_run_skips_flow_11_and_names_flow_12_upgrade(tmp_path):
    old = [{"id": f"old-{key}", "displayName": name, "type": platformtest.ITEM_TYPES[name]}
           for name, key in platformtest.ITEMS.items()]
    test, *_ = harness(tmp_path, Fabric(items=old, name=""), upgrade=True)
    result = test.run()
    assert [(c["flow"], c["id"], c["status"]) for c in result["checks"]][:2] == [
        (11, "platform.finish_empty", "skipped"), (12, "platform.upgrade", "passed")]
    assert [c["title"] for c in result["checks"][:2]] == [platformtest.FLOW_TITLES[11], platformtest.FLOW_TITLES[12]]
    assert test.config["workspace_name"] == ""


def test_a_platform_that_isnt_ready_skips_flows_13_to_15(tmp_path):
    test, *_ = harness(tmp_path, connected=False)
    result = test.run()
    assert [(c["flow"], c["id"], c["status"]) for c in result["checks"]] == [
        (11, "platform.finish_empty", "failed"), (12, "platform.repair", "failed"), (13, "platform.product", "skipped"),
        (14, "platform.keys", "skipped"), (15, "platform.exception", "skipped"), (16, "platform.restrict", "skipped")]
    assert {c["detail"] for c in result["checks"][2:5]} == {"Not run: the platform isn't ready (flow 12)."}


def test_runs_write_into_a_new_folder_and_again_into_the_same_one(tmp_path):
    for _ in range(2):
        out = tmp_path / "a" / "b"
        test = platformtest.PlatformTest("ws", Fabric(description="team"), lambda: "t", out, client=lambda ws, fid: None,
                                         run_id="run1")
        assert test.run()["outcome"] == "refused" and out.is_dir()


def test_a_delete_that_fails_keeps_its_reason_cut_to_200(tmp_path):
    class Stuck(Fabric):
        def _delete(self, item_id):
            raise RuntimeError("x" * 300)
    test, *_ = harness(tmp_path, Stuck())
    result = test.run()
    assert {len(e["why"]) for e in result["left_behind"]} == {200}


def test_setup_waits_on_the_connection_only_when_its_tables_step_failed():
    assert platformtest._needs_connection({"steps": [{"step": "Platform tables", "status": "failed"}]}) is True
    assert platformtest._needs_connection({"steps": [{"step": "Platform tables", "status": "done"},
                                                     {"step": "LH_Meta", "status": "failed"}]}) is False
