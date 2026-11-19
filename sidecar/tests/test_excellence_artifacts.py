"""Notebook and pipeline controls (FEA-301, FEA-302): each has a pass, a fail and a not-evaluated case;
the evidence keeps rule matches and activity names, never code; and the optimization engine's
``inspect`` hook changes nothing when it isn't given."""

from __future__ import annotations

import base64
import json

import pytest

from fabric_desktop import server
from fabric_desktop.excellence import adapters, assess, catalog, catalog_artifacts, inspect_items
from fabric_desktop.opportunities import assess as opp_assess, fixture
from fabric_desktop.service import Service

AT = "2026-11-18T02:00:00+00:00"
WSID = "11111111-2222-3333-4444-555555555555"
OTHER_WS = "99999999-2222-3333-4444-555555555555"
FAKE_SECRET = "Zx9qLm2Vb7Tr4Kp1"  # pragma: allowlist secret (fake password: the tests check it never leaves the notebook)
SECRET_CELL = f'conn = "Server=tcp:x.database.windows.net;Password={FAKE_SECRET};"\n'

NOTEBOOK_CASES = [
    ("FEA-SEC-005", SECRET_CELL, 'key = notebookutils.credentials.getSecret("https://v.vault.azure.net/", "k")\n'),
    ("FEA-DEVOPS-001", f'df = spark.read.load("abfss://{WSID}@onelake.dfs.fabric.microsoft.com/x/Tables/t")\n',
     'df = spark.read.load("Tables/t")\n'),
    ("FEA-REL-001", "try:\n    x = 1\nexcept Exception:\n    pass\n",
     "try:\n    x = 1\nexcept ValueError:\n    raise\n"),
    ("FEA-REL-002", "%pip install requests\n", "%pip install requests==2.31.0\n"),
    ("FEA-DE-003", 'spark.sql("VACUUM t RETAIN 1 HOURS")\n', 'spark.sql("VACUUM t RETAIN 168 HOURS")\n'),
]
NOTEBOOK_CONTROLS = [c for c, _, _ in NOTEBOOK_CASES]
POLICY_CONTROLS = ["FEA-REL-004", "FEA-REL-005", "FEA-REL-006"]
PIPELINE_CONTROLS = ["FEA-REL-003", "FEA-DEVOPS-002", *POLICY_CONTROLS]


def notebook(item: str, cells: list[str] | None, definition: str = "read") -> dict:
    w = {"item_id": item, "type": "Notebook", "name": item, "workspace_id": WSID, "definition": definition,
         "problem": None if definition == "read" else "403", "runs": {"read": True, "scheduled": 3}}
    if cells is not None:
        w["cells"] = cells
    return w


GOOD_POLICY = {"retry": 2, "retryIntervalInSeconds": 60, "timeout": "0.01:00:00"}


def activity(name: str, inactive: bool = False, workspace: object = None, kind: str = "TridentNotebook",
             policy: object = GOOD_POLICY, depends_on: object = ()) -> dict:
    tp = {"notebookId": "aaaaaaaa-2222-3333-4444-555555555555"}
    if workspace is not None:
        tp["workspaceId"] = workspace
    a = {"name": name, "type": kind, "inactive": inactive, "type_properties": tp, "path": "$",
         "depends_on": [{"activity": d, "conditions": [c]} for d, c in depends_on]}
    if policy is not None:
        a["policy"] = dict(policy)
    return a


def pipeline(item: str, acts: list[dict] | None, definition: str = "read") -> dict:
    w = {"item_id": item, "type": "DataPipeline", "name": item, "workspace_id": WSID, "definition": definition,
         "problem": None if definition == "read" else "403", "runs": {"read": True, "scheduled": 3}}
    if acts is not None:
        w["activities"] = acts
    return w


def finish(workloads: list[dict]) -> dict:
    """What opportunities.assess does with the hook: inspect, then drop the code."""
    for w in workloads:
        w["inspected"] = inspect_items.inspect(w)
        w.pop("cells", None)
        w.pop("activities", None)
    return {"workspaces": [{"id": WSID}], "workloads": workloads, "opportunities": []}


def run(workloads: list[dict], extra_items: list[dict] | None = None) -> dict:
    opt = finish(workloads)
    items = [{"id": w["item_id"], "type": w["type"], "displayName": w["name"]} for w in workloads] + (extra_items or [])
    records = (adapters.workspace({"id": WSID, "displayName": "Sales"}, items, collected_at=AT)
               + adapters.optimization(opt, collected_at=AT) + adapters.inspection(opt, collected_at=AT))
    return assess.assess(records, catalog.registry(), assessed_at=AT, scope={"workspaces": [{"id": WSID}]})


def outcome_of(snap: dict, cid: str, subject_id: str) -> dict:
    got = [o for o in snap["outcomes"] if o["control_id"] == cid and o["subject"]["id"] == subject_id]
    assert len(got) == 1, got
    return got[0]


# ---- the controls are registered and complete


def test_the_artifact_controls_are_registered_and_complete():
    r = catalog.registry()
    ids = {c.id for c in catalog_artifacts.CONTROLS}
    assert ids == set(NOTEBOOK_CONTROLS) | set(PIPELINE_CONTROLS)
    assert len(ids) == 10 and ids <= set(r.versions())
    for c in catalog_artifacts.CONTROLS:
        assert c.references and all(ref["url"].startswith("https://") for ref in c.references)
        assert c.evaluate.__doc__ and c.severity.capitalize() in c.evaluate.__doc__  # severity justified
        assert c.evidence_kinds == ("item.inspection",)


# ---- notebooks (FEA-301)


@pytest.mark.parametrize("cid, bad, good", NOTEBOOK_CASES, ids=NOTEBOOK_CONTROLS)
def test_notebook_controls_pass_fail_and_not_evaluated(cid, bad, good):
    snap = run([notebook("bad", ["x = 1\n", bad]), notebook("good", [good]),
                notebook("unread", None, definition="not_read"), notebook("gone", None)])
    failed = outcome_of(snap, cid, "bad")
    assert failed["outcome"] == "fail" and failed["basis"] == "verified_configuration"
    assert "cell 2, line" in failed["text"] and failed["detail"]["where"][0]["cell"] == 2
    assert outcome_of(snap, cid, "good")["outcome"] == "pass"
    assert outcome_of(snap, cid, "unread")["outcome"] == "not_evaluated"  # definition not read
    assert outcome_of(snap, cid, "gone")["outcome"] == "not_evaluated"  # read, but no cells to inspect
    assert cid in {f["rule"].split("@")[0] for f in snap["findings"]}


def test_a_secret_is_kept_as_where_it_is_never_what_it_is():
    w = notebook("nb", ["print(1)\n", SECRET_CELL])
    got = inspect_items.inspect(dict(w))
    assert got["rules"]["NB001"] == {"severity": "Error", "count": 1, "where": [{"cell": 2, "line": 1}]}
    snap = run([w])
    text = json.dumps(snap)
    assert FAKE_SECRET not in text and "Server=tcp" not in text and "conn =" not in text
    assert outcome_of(snap, "FEA-SEC-005", "nb")["outcome"] == "fail"


def test_notebook_inspection_keeps_rule_ids_and_places_only():
    got = inspect_items.inspect(notebook("nb", ["%pip install a b c\n" * 30]))
    assert set(got) == {"read", "kind", "cells", "scheduled", "rules", "unchecked"}
    nb009 = got["rules"]["NB009"]
    assert nb009["count"] == 30 and len(nb009["where"]) == inspect_items.MAX_PLACES
    assert set(nb009["where"][0]) == {"cell", "line"}


def test_an_inspection_that_breaks_is_not_evaluated_never_a_pass(monkeypatch):
    def boom(*a, **k):
        raise RecursionError("deep")
    monkeypatch.setattr(inspect_items.notebook_practices, "check", boom)
    snap = run([notebook("nb", ["x = 1\n"])])
    for cid in NOTEBOOK_CONTROLS:
        assert outcome_of(snap, cid, "nb")["outcome"] == "not_evaluated"


# ---- pipelines (FEA-302)


def test_deactivated_activity_control():
    snap = run([pipeline("bad", [activity("Load", inactive=True), activity("Next")]),
                pipeline("good", [activity("Load")]), pipeline("unread", None, definition="not_read"),
                pipeline("odd", [{"name": "Strange", "type": "Copy", "type_properties": {}}])])
    o = outcome_of(snap, "FEA-REL-003", "bad")
    assert o["outcome"] == "fail" and "Load" in o["text"] and "Next" not in o["text"]
    assert outcome_of(snap, "FEA-REL-003", "good")["outcome"] == "pass"
    assert outcome_of(snap, "FEA-REL-003", "unread")["outcome"] == "not_evaluated"
    odd = outcome_of(snap, "FEA-REL-003", "odd")  # the reader didn't say whether it's active
    assert odd["outcome"] == "not_evaluated" and "Strange" in odd["text"]


@pytest.mark.parametrize("workspace, outcome", [
    (OTHER_WS, "fail"),
    ({"value": "@pipeline().libraryVariables.ws", "type": "Expression"}, "pass"),
    ("@pipeline().parameters.ws", "pass"),
    (None, "pass"),
])
def test_fixed_workspace_control(workspace, outcome):
    snap = run([pipeline("p", [activity("Run nb", workspace=workspace)])])
    o = outcome_of(snap, "FEA-DEVOPS-002", "p")
    assert o["outcome"] == outcome
    assert OTHER_WS not in json.dumps(snap["outcomes"]) and OTHER_WS not in json.dumps(snap["evidence"])


def test_fixed_workspace_is_found_nested_and_missing_properties_are_not_evaluated():
    nested = activity("Copy", kind="Copy")
    nested["type_properties"] = {"sink": {"datasetSettings": {"linkedService": {"properties": {
        "typeProperties": {"workspaceId": OTHER_WS, "artifactId": "x"}}}}}}
    bare = {"name": "Bare", "type": "Copy", "inactive": False, "path": "$"}
    snap = run([pipeline("nested", [nested]), pipeline("bare", [bare]), pipeline("gone", None)])
    assert outcome_of(snap, "FEA-DEVOPS-002", "nested")["outcome"] == "fail"
    assert outcome_of(snap, "FEA-DEVOPS-002", "bare")["outcome"] == "not_evaluated"
    assert outcome_of(snap, "FEA-REL-003", "bare")["outcome"] == "pass"  # its state was read
    assert outcome_of(snap, "FEA-DEVOPS-002", "gone")["outcome"] == "not_evaluated"


def test_pipeline_inspection_keeps_names_and_counts_only():
    got = inspect_items.inspect(pipeline("p", [activity("A", inactive=True, workspace=OTHER_WS)]))
    assert got == {"read": True, "kind": "pipeline", "activities": 1, "inactive": ["A"], "inactive_count": 1,
                   "fixed_workspace": ["A"], "fixed_workspace_count": 1, "state_unknown": [], "properties_unknown": [],
                   "calls_out": 1, "no_retry": [], "no_retry_count": 0, "no_timeout": [], "no_timeout_count": 0,
                   "policy_unknown": [], "failure_path": False, "depends_unknown": []}


# ---- pipeline retry, timeout and failure path (FEA-302)


def _policy_outcomes(acts: list[dict]) -> dict[str, dict]:
    snap = run([pipeline("p", acts)])
    return {cid: outcome_of(snap, cid, "p") for cid in POLICY_CONTROLS}


ALERT = ("Copy", "Failed")


def test_policy_controls_pass_when_retry_timeout_and_a_failure_path_are_set():
    got = _policy_outcomes([activity("Copy", kind="Copy"),
                            activity("Alert", kind="Office365Outlook", policy={}, depends_on=[ALERT])])
    assert {c: o["outcome"] for c, o in got.items()} == dict.fromkeys(POLICY_CONTROLS, "pass")
    assert got["FEA-REL-004"]["text"].startswith("All 1 of its")  # Outlook isn't counted: a retry can send twice


@pytest.mark.parametrize("policy, retry, timeout", [
    ({}, "fail", "fail"),  # nothing set: Fabric's defaults, no retry and 12 hours
    ({"retry": 0, "timeout": "0.12:00:00"}, "fail", "fail"),  # the defaults written out
    ({"retry": 3, "timeout": "1.00:00:00"}, "pass", "fail"),  # longer than the default
    ({"retry": 3, "timeout": "12:00:00"}, "pass", "fail"),
    ({"retry": "2", "timeout": "11:59:59"}, "pass", "pass"),
    ({"retry": "@pipeline().parameters.r", "timeout": {"value": "@variables('t')", "type": "Expression"}},
     "pass", "pass"),  # set by an expression: its value is only known at run time
    ({"retry": 1, "timeout": "0.00:00:00"}, "pass", "fail"),
])
def test_retry_and_timeout_controls(policy, retry, timeout):
    got = _policy_outcomes([activity("Load", kind="Copy", policy=policy), activity("Wait", kind="Wait", policy={})])
    assert got["FEA-REL-004"]["outcome"] == retry and got["FEA-REL-005"]["outcome"] == timeout
    for cid, outcome in (("FEA-REL-004", retry), ("FEA-REL-005", timeout)):
        o = got[cid]
        if outcome == "fail":
            assert "Load" in o["text"] and "Wait" not in o["text"] and o["detail"] == {"activities": ["Load"], "count": 1}
            assert o["basis"] == "verified_configuration"


def test_failure_path_control():
    def fp(acts):
        return _policy_outcomes(acts)["FEA-REL-006"]
    assert fp([activity("Copy", kind="Copy"), activity("Log", kind="Script", depends_on=[("Copy", "Succeeded")])
               ])["outcome"] == "fail"
    assert fp([activity("Copy", kind="Copy"), activity("Tidy", kind="Script", depends_on=[("Copy", "Completed")])
               ])["outcome"] == "pass"
    assert fp([activity("Copy", kind="Copy"), activity("Mail", kind="Teams", depends_on=[("Copy", "failed")])
               ])["outcome"] == "pass"  # conditions are matched without regard to case


def test_policy_controls_are_not_applicable_without_an_activity_that_calls_out():
    got = _policy_outcomes([activity("Wait", kind="Wait", policy=None), activity("Child", kind="InvokePipeline")])
    assert {c: o["outcome"] for c, o in got.items()} == dict.fromkeys(POLICY_CONTROLS, "not_applicable")


def test_a_missing_policy_or_depends_on_key_is_not_evaluated_never_a_pass():
    no_policy = activity("Old", kind="Copy", policy=None)  # an older engine kept no policy
    got = _policy_outcomes([no_policy, activity("Mail", kind="Teams", depends_on=[("Old", "Failed")])])
    assert got["FEA-REL-004"]["outcome"] == got["FEA-REL-005"]["outcome"] == "not_evaluated"
    assert "Old" in got["FEA-REL-004"]["text"] and got["FEA-REL-006"]["outcome"] == "pass"
    unreadable = _policy_outcomes([activity("Odd", kind="Copy", policy={"retry": 2, "timeout": "soon"})])
    assert unreadable["FEA-REL-004"]["outcome"] == unreadable["FEA-REL-005"]["outcome"] == "not_evaluated"
    no_deps = activity("Copy", kind="Copy")
    del no_deps["depends_on"]
    got = _policy_outcomes([no_deps])
    assert got["FEA-REL-006"]["outcome"] == "not_evaluated" and got["FEA-REL-004"]["outcome"] == "pass"


def test_policy_controls_are_not_evaluated_without_a_definition_or_an_older_inspection():
    snap = run([pipeline("unread", None, definition="not_read"), pipeline("gone", None)])
    for cid in POLICY_CONTROLS:
        assert outcome_of(snap, cid, "unread")["outcome"] == "not_evaluated"
        assert outcome_of(snap, cid, "gone")["outcome"] == "not_evaluated"
    old = finish([pipeline("old", [activity("Copy", kind="Copy")])])
    for k in ("calls_out", "no_retry", "no_retry_count", "no_timeout", "no_timeout_count", "policy_unknown",
              "failure_path", "depends_unknown"):
        old["workloads"][0]["inspected"].pop(k)  # evidence from before these settings were kept
    items = [{"id": "old", "type": "DataPipeline", "displayName": "old"}]
    records = (adapters.workspace({"id": WSID, "displayName": "Sales"}, items, collected_at=AT)
               + adapters.inspection(old, collected_at=AT))
    snap = assess.assess(records, catalog.registry(), assessed_at=AT, scope={"workspaces": [{"id": WSID}]})
    for cid in POLICY_CONTROLS:
        assert outcome_of(snap, cid, "old")["outcome"] == "not_evaluated"


def test_policy_inspection_keeps_capped_names_and_counts_never_the_settings():
    long = "A" * 400  # sorts first, so it is among the names kept
    acts = [activity(f"Copy {i:03}", kind="Copy", policy={"retry": 0, "timeout": "7.00:00:00",
                                                          "secureInput": True}) for i in range(60)]
    acts.append(activity(long, kind="WebActivity", policy={}))
    got = inspect_items.inspect(pipeline("p", acts))
    assert got["calls_out"] == 61 and got["no_retry_count"] == got["no_timeout_count"] == 61
    assert len(got["no_retry"]) == len(got["no_timeout"]) == inspect_items.MAX_NAMES
    assert all(isinstance(n, str) and len(n) <= 256 for n in got["no_retry"] + got["no_timeout"])
    assert "A" * 256 in got["no_retry"] and long not in got["no_retry"]
    text = json.dumps(got)
    assert "7.00:00:00" not in text and "secureInput" not in text and "retryInterval" not in text
    snap = run([pipeline("p", acts)])
    ev_text = json.dumps(snap["evidence"]) + json.dumps(snap["outcomes"])
    assert "7.00:00:00" not in ev_text and "secureInput" not in ev_text and long not in ev_text


# ---- scope: other item types, coverage, evidence


def test_other_item_types_are_untouched_and_coverage_counts_inspected_items():
    snap = run([notebook("nb", ["x = 1\n"]), pipeline("pl", [activity("A")])],
               extra_items=[{"id": "lh", "type": "Lakehouse", "displayName": "LH"}])
    ours = {c.id for c in catalog_artifacts.CONTROLS}  # the architecture controls do read lakehouses
    assert not [o for o in snap["outcomes"] if o["subject"]["id"] == "lh" and o["control_id"] in ours]
    assert not [r for r in snap["evidence"] if r["kind"] == "item.inspection" and r["subject"]["id"] == "lh"]
    cov = {c["item"]["id"]: c["state"] for c in snap["item_coverage"]}
    # Architecture controls cover lakehouses; this run reads no lakehouse properties, so it counts as not inspected.
    assert cov == {"nb": "inspected", "pl": "inspected", "lh": "not_inspected"}
    assert inspect_items.inspect({"type": "Lakehouse", "definition": "unsupported"}) == {}


def test_inspection_evidence_needs_the_hook_and_says_when_unread():
    plain = {"workspaces": [{"id": WSID}], "workloads": [notebook("nb", None)], "opportunities": []}
    assert adapters.inspection(plain, collected_at=AT) == []  # assessed without the hook: no evidence, not a pass
    recs = adapters.inspection(finish([notebook("u", None, definition="not_read"), notebook("r", ["x = 1\n"])]),
                               collected_at=AT)
    by = {r["subject"]["id"]: r for r in recs}
    assert by["u"]["confidence"] == "insufficient" and by["u"]["payload"]["problem"] == "403"
    assert by["r"]["confidence"] == "verified_configuration"


# ---- the optimization engine's hook (opportunities/assess.py)


def _demo(**kw) -> dict:
    d = fixture.load()
    return opp_assess.assess([d["source"]], usage_imports=d["usage_imports"], price=d["price"], now=d["now"],
                             days=30, scope={"kind": "demo"}, fixture=True, **kw)


def test_the_hook_changes_nothing_when_it_isnt_given():
    assert json.dumps(_demo(), sort_keys=True) == json.dumps(_demo(inspect=None), sort_keys=True)
    assert all("inspected" not in w for w in _demo()["workloads"])


def test_the_hook_sees_the_code_once_and_the_result_still_carries_none():
    seen: list[tuple[str, bool]] = []

    def spy(w: dict) -> dict:
        seen.append((w["item_id"], "cells" in w or "activities" in w))
        return {"n": 1}

    r = _demo(inspect=spy)
    assert sorted(i for i, _ in seen) == sorted(w["item_id"] for w in r["workloads"])
    assert all(had for i, had in seen if i in {w["item_id"] for w in r["workloads"] if w["definition"] == "read"})
    assert all(w["inspected"] == {"n": 1} and "cells" not in w and "activities" not in w for w in r["workloads"])
    plain = _demo()
    assert [{k: v for k, v in w.items() if k != "inspected"} for w in r["workloads"]] == plain["workloads"]


def test_the_hook_isnt_reachable_over_rpc():
    assert "_optimization_assess" not in server.METHODS
    import inspect as pyinspect
    assert "inspect" not in pyinspect.signature(Service.optimization_assess).parameters


# ---- the engine call


class FakeFabric:
    def __init__(self):
        self.calls: list[tuple] = []

    def get_workspace(self, ws):
        self.calls.append(("get_workspace", ws))
        return {"id": ws, "displayName": "Sales", "description": "d"}

    def list_items(self, ws):
        self.calls.append(("list_items", ws))
        return [{"id": "nb1", "type": "Notebook", "displayName": "nb_load"}]

    def get_item_definition(self, ws, item, fmt=None):
        self.calls.append(("get_item_definition", item))
        doc = {"cells": [{"cell_type": "code", "source": SECRET_CELL}], "metadata": {}}
        return {"definition": {"parts": [{"path": "notebook-content.ipynb", "payloadType": "InlineBase64",
                                          "payload": base64.b64encode(json.dumps(doc).encode()).decode()}]}}

    def list_job_instances(self, ws, item):
        self.calls.append(("list_job_instances", item))
        return []

    def list_role_assignments(self, ws):
        self.calls.append(("list_role_assignments", ws))
        return []

    def list_capacities(self):
        self.calls.append(("list_capacities",))
        return []


def test_the_call_inspects_what_it_already_read_and_keeps_no_code(tmp_path, monkeypatch):
    fake = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    r = Service(root=tmp_path / "app").excellence_assess(workspace_id=WSID)
    assert {c[0] for c in fake.calls} <= {"get_workspace", "list_items", "get_item_definition", "list_job_instances",
                                          "list_role_assignments", "list_capacities"}
    assert outcome_of(r, "FEA-SEC-005", "nb1")["outcome"] == "fail"
    assert outcome_of(r, "FEA-REL-001", "nb1")["outcome"] == "pass"
    assert FAKE_SECRET not in json.dumps(r) and "Server=tcp" not in json.dumps(r)


def test_the_demo_shows_the_new_controls_with_no_fabric_call(tmp_path, monkeypatch):
    fake = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    r = Service(root=tmp_path / "app").excellence_assess(demo=True)
    assert fake.calls == [] and r["fixture"] is True
    decided = {o["control_id"] for o in r["outcomes"] if o["outcome"] in ("pass", "fail")}
    assert set(NOTEBOOK_CONTROLS) | set(PIPELINE_CONTROLS) <= decided
    # The demo's pipelines set no policy and no failure path: Fabric's defaults, so these fail there.
    assert {o["outcome"] for o in r["outcomes"] if o["control_id"] in POLICY_CONTROLS} == {"fail"}
    assert assess.rescore(json.loads(json.dumps(r)), catalog.registry())["scores"] == r["scores"]



def test_an_excellence_run_takes_no_slot_kept_for_sharing(tmp_path, monkeypatch):
    fake = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    s = Service(root=tmp_path / "app")
    kept = []
    monkeypatch.setattr(s, "_keep_assessment", lambda result: kept.append(result) or "kept")
    s.excellence_assess(workspace_id=WSID)
    assert kept == []
    assert s.optimization_assess(workspace_id=WSID)["share_id"] == "kept" and len(kept) == 1


def test_an_unknown_schedule_leaves_the_scheduled_only_rules_unchecked():
    from fabric_desktop.excellence import inspect_items
    w = {"type": "Notebook", "definition": "read", "cells": ["display(df)"], "runs": {"read": False}}
    got = inspect_items.inspect(w)
    assert got["scheduled"] is None and "NB004" in got["unchecked"]
    known = inspect_items.inspect({**w, "runs": {"read": True, "scheduled": 0}})
    assert known["scheduled"] is False and known["unchecked"] == []
