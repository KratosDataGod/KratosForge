"""The ten-flow tenant smoke test (smoketest.py), held to exact values: each step's flow, id and detail on a full
run, the plan with and without the platform and warehouse flows, an empty token, a name check that lets a bad name
through, what the live, platform and warehouse parts add, smoke.json's layout and the summary's lines. Sign-in,
Fabric and the live, platform and warehouse runs are the main tests' fakes."""

from __future__ import annotations

import json
import re

from fabric_desktop import fabric_api, smoketest
from tests.test_smoketest import WS, Credential, Live, Platform, clean_bundle, smoke


def _steps(result):
    return {s["id"]: s for s in result["steps"]}


def test_a_full_run_records_each_step_under_its_flow_with_what_it_found(tmp_path):
    result = smoke(tmp_path).run()
    assert [(s["flow"], s["id"]) for s in result["steps"]] == [
        (1, "engine"), (2, "auth.client"), (2, "auth.fabric"), (2, "auth.onelake"), (2, "auth.sql"), (2, "auth.kusto"),
        (3, "catalog.workspaces"), (3, "catalog.items"), (4, "lakehouse.bad_name"), (4, "live.fabric.create_lakehouse"),
        (5, "live.delta.merge"), (6, "live.notebook.publish"), (6, "live.notebook.run"), (9, "live.cleanup.lakehouse"),
        (9, "live.outcome"), (10, "support.bundle")]
    steps = _steps(result)
    assert re.fullmatch(r"KratosForge engine \S+; DuckDB \S+, Polars \S+, delta-rs \S+, PyArrow \S+\.",
                        steps["engine"]["detail"])
    assert {k: steps[k]["detail"] for k in ("auth.client", "auth.fabric", "auth.onelake", "catalog.workspaces",
                                            "catalog.items", "support.bundle")} == {
        "auth.client": "No App ID set: signs in with Microsoft's client.",
        "auth.fabric": "Token received (not recorded).", "auth.onelake": "Token received (not recorded).",
        "catalog.workspaces": "2 workspaces listed, the test workspace among them.",
        "catalog.items": "3 items (Lakehouse, Notebook).",
        "support.bundle": "Checked 36 characters: no token, user name, tenant id or workspace id."}
    assert steps["lakehouse.bad_name"]["detail"].startswith("Refused: Fabric doesn't accept the lakehouse name '1 bad name'")
    assert not steps["auth.sql"]["required"] and not steps["auth.kusto"]["required"]
    assert result["run_in_fabric"] is False and result["warehouse_check"] is None
    text = (tmp_path / "out" / "smoke1" / "smoke.json").read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2)


def test_a_run_without_its_own_id_gets_twelve_hex_characters(tmp_path):
    test = smoketest.SmokeTest(WS, Credential(), None, tmp_path)
    assert re.fullmatch(r"[0-9a-f]{12}", test.run_id) and test.dir == tmp_path / test.run_id


def test_the_plan_names_the_platform_and_warehouse_flows_only_when_asked_for(tmp_path):
    test = smoke(tmp_path)
    plain = test.plan()
    assert not any(line.startswith(("Flows 11-16", "Flow 17")) for line in plain)
    test.platform_test, test.warehouse_check = Platform(), object()
    both = test.plan()
    assert sum(line.startswith("Flows 11-16 (--platform)") for line in both) == 1
    assert sum(line.startswith("Flow 17 (--warehouse)") for line in both) == 1
    assert len(both) == len(plain) + 2


def test_an_empty_token_fails_its_step(tmp_path):
    class Empty(Credential):
        def storage_token(self):
            return ""
    steps = _steps(smoke(tmp_path, credential=Empty()).run())
    assert (steps["auth.onelake"]["status"], steps["auth.onelake"]["detail"]) == (
        "failed", "RuntimeError: Sign-in returned an empty token")


def test_a_name_check_that_lets_a_bad_name_through_fails_flow_4(tmp_path, monkeypatch):
    monkeypatch.setattr(fabric_api, "lakehouse_name_problem", lambda name: "")
    step = _steps(smoke(tmp_path).run())["lakehouse.bad_name"]
    assert step["status"] == "failed" and step["detail"].startswith("RuntimeError: The name check accepted '1 bad name'")


def test_the_live_outcome_counts_what_it_left_behind(tmp_path):
    live = Live(left_behind=[{"kind": "lakehouse"}, {"kind": "notebook"}])
    step = _steps(smoke(tmp_path, live=live).run())["live.outcome"]
    assert (step["status"], step["detail"]) == ("failed", "Live test live1: passed, 2 left behind")


def test_the_platform_outcome_is_flow_11(tmp_path):
    test = smoke(tmp_path)
    test.platform_test = Platform()
    step = _steps(test.run())["platform.outcome"]
    assert (step["flow"], step["detail"]) == (11, "Every platform item it made was deleted.")


class _Warehouse:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def __call__(self, out):
        return self

    def run(self):
        if self.error:
            raise self.error
        return self.result


def _warehouse_run(tmp_path, check):
    test = smoke(tmp_path)
    test.warehouse_check = check
    return test.run()


def test_warehouse_findings_are_counted_and_recorded_without_failing_flow_17(tmp_path):
    result = _warehouse_run(tmp_path, _Warehouse({"run_id": "w1", "outcome": "findings", "refused": [], "cleanup": "Dropped.",
                                                  "counts": {"docs are wrong": 1, "local differs": 2, "passed": 4}}))
    steps = [s for s in result["steps"] if s["flow"] == 17]
    assert [(s["id"], s["status"], s["detail"], s["required"]) for s in steps] == [
        ("warehouse.findings", "failed", "1 docs are wrong, 2 local differs, 4 passed", False),
        ("warehouse.check", "passed", "Dropped.", True)]
    assert result["warehouse_check"] == {"run_id": "w1", "outcome": "findings", "refused": [],
                                         "counts": {"docs are wrong": 1, "local differs": 2, "passed": 4},
                                         "cleanup": "Dropped."}


def test_warehouse_findings_of_none_pass(tmp_path):
    result = _warehouse_run(tmp_path, _Warehouse({"run_id": "w1", "outcome": "passed", "refused": [], "cleanup": "",
                                                  "counts": {"local matches the warehouse": 3}}))
    assert _steps(result)["warehouse.findings"]["status"] == "passed"


def test_a_refused_or_crashed_warehouse_check_fails_its_step(tmp_path):
    refused = _warehouse_run(tmp_path / "a", _Warehouse({"run_id": "w1", "outcome": "refused", "refused": ["no", "never"],
                                                          "cleanup": "", "counts": {}}))
    assert _steps(refused)["warehouse.check"]["detail"] == "RuntimeError: no; never"
    crashed = _warehouse_run(tmp_path / "b", _Warehouse(error=RuntimeError("lost the connection")))
    assert _steps(crashed)["warehouse.check"]["detail"] == "RuntimeError: lost the connection"
    assert crashed["warehouse_check"] == {"run_id": None, "outcome": "failed", "refused": [], "counts": {}, "cleanup": None}


def _summary(**kw):
    return smoketest.summary({"run_id": "s1", "outcome": "passed", "version": "1.0", "started_at": "S",
                              "workspace": {"id": "ws-id", "name": ""}, "run_in_fabric": True, "flows": [],
                              "steps": [], "live_test": None, "platform_test": None} | kw)


def test_the_summary_names_the_workspace_by_id_without_a_name_and_says_run_in_fabric():
    assert ("Outcome of the automated parts: **passed**. Engine 1.0, workspace ws-id, started S, with Run in Fabric."
            in _summary())
    assert "workspace Live, started S, without Run in Fabric." in _summary(workspace={"id": "x", "name": "Live"},
                                                                         run_in_fabric=False)


def test_the_summary_marks_steps_that_dont_fail_their_flow():
    text = _summary(steps=[{"flow": 2, "title": "Kusto", "status": "failed", "detail": "a|b", "required": False},
                           {"flow": 2, "title": "Fabric", "status": "passed", "detail": "ok", "required": True}])
    assert "| 2 | Kusto | **Failed** | a\\|b (recorded, doesn't fail the flow) |\n| 2 | Fabric | Passed | ok |\n" in text


def test_the_summary_says_whether_platform_items_were_kept_or_left_behind():
    kept = _summary(platform_test={"left_behind": [{"id": "x"}], "kept": True})
    left = _summary(platform_test={"left_behind": [{"id": "x"}], "kept": False})
    assert "**Kept** by the platform flows" in kept and "**Left behind** by the platform flows" in left


def test_a_default_smoke_test_doesnt_run_in_fabric(tmp_path):
    test = smoketest.SmokeTest(WS, Credential(), None, tmp_path, support_bundle=clean_bundle)
    assert test.run_in_fabric is False


def test_one_wrong_doc_and_one_local_difference_are_two_findings(tmp_path):
    result = _warehouse_run(tmp_path, _Warehouse({"run_id": "w1", "outcome": "findings", "refused": [], "cleanup": "",
                                                  "counts": {"docs are wrong": 1, "local differs": 1}}))
    assert _steps(result)["warehouse.findings"]["status"] == "failed"
