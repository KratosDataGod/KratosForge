"""The predeployment risk check (FINOPS-004): pass, warning, blocked and unknown, each with evidence.

The analyzer is pure, so most tests hand it facts. The service tests use real local stores and a
fake Fabric that only answers reads, and check nothing is started."""

import datetime as dt
import json

import pytest

from fabric_desktop import app_settings, estate_service, risk
from fabric_desktop import measurement as ms
from fabric_desktop.workloads import model
from tests.test_workloads import scan, signed_in, svc  # noqa: F401 - the fixture is used by name

NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.timezone.utc)


def at(minutes_ago: float) -> str:
    return (NOW - dt.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def capacity(status="running", access="ok", age_s=60, cached=False, **extra):
    obs = {"access": access, "access_text": "Read", "status": status, "status_text": "", "action": "Do the thing.",
           "workspace_id": "ws1", "workspace_name": "Sales Dev", "capacity_id": "cap1",
           "capacity": {"name": "fabcapdev", "sku": "F8", "state": "Active" if status == "running" else "Inactive"},
           "observed_at": at(age_s / 60), "age_seconds": age_s, "stale": age_s > 3600, "cached": cached}
    return obs | extra


def facts(**kw):
    base = {"workload_id": "fabric:ws1:pl1", "workload": "pl_load_erp", "action": "run_item", "label": "run item",
            "env": None, "env_label": "", "where": "fabric", "policy": {"allow_fabric_runs": True}, "signed_in": True,
            "capacity": capacity(), "runs": [], "measured": [], "estimated": [], "schedules": []}
    return base | kw


def run(state, started_min_ago, seconds=None, message="", job="j"):
    started = NOW - dt.timedelta(minutes=started_min_ago)
    ended = started + dt.timedelta(seconds=seconds) if seconds is not None else None
    return {"state": state, "started_at": started.isoformat(), "ended_at": ended.isoformat() if ended else None,
            "seconds": None, "message": message, "where": "fabric", "job_id": job, "ran_on": "Fabric · Sales Dev"}


def by_check(result):
    return {f["check"]: f for f in result["findings"]}


# --------------------------------------------------------------------------- outcomes


def test_a_running_capacity_with_good_history_passes_and_says_utilization_is_unknown():
    history = [run("succeeded", 60 * d, 600 + d) for d in (1, 2, 3)]
    r = risk.analyze(facts(runs=history), now=NOW)
    assert r["result"] == risk.PASS and r["can_proceed"] and r["enforced"] is False
    assert "Reports only" in r["note"]
    cap = by_check(r)["capacity"]
    assert cap["outcome"] == risk.PASS and "How busy it is isn't known" in cap["detail"]
    assert by_check(r)["runtime"]["title"] == "Usually takes about 10 min"
    assert r["note"].startswith("Read only")


def test_a_paused_capacity_read_this_hour_blocks_and_an_older_reading_only_warns():
    blocked = risk.analyze(facts(capacity=capacity("not_running")), now=NOW)
    assert blocked["result"] == risk.BLOCKED and not blocked["can_proceed"]
    cap = by_check(blocked)["capacity"]
    assert cap["title"] == "fabcapdev is paused" and "resume" in cap["remedy"] and not cap["can_override"]
    assert any("F8" in e for e in cap["evidence"])
    old = risk.analyze(facts(capacity=capacity("not_running", age_s=3 * 3600)), now=NOW)
    assert old["result"] == risk.WARNING and "3 hours ago" in by_check(old)["capacity"]["detail"]
    cached = risk.analyze(facts(capacity=capacity("not_running", cached=True)), now=NOW)
    assert cached["result"] == risk.WARNING  # the reading is the last good one, not a current one


def test_policy_and_sign_in_block_fabric_runs_but_not_runs_on_this_pc():
    r = risk.analyze(facts(policy={"allow_fabric_runs": False}, signed_in=False), now=NOW)
    assert r["result"] == risk.BLOCKED
    assert {f["check"] for f in r["findings"] if f["outcome"] == risk.BLOCKED} == {"policy", "sign_in"}
    assert "allow_fabric_runs" in by_check(r)["policy"]["source"]
    here = risk.analyze(facts(where="this_pc", policy={"allow_fabric_runs": False}, signed_in=False, capacity=None), now=NOW)
    assert here["result"] == risk.PASS  # no history, nothing blocks: unknown run time isn't essential
    assert here["result"] != risk.BLOCKED and by_check(here)["capacity"]["title"] == "Runs on this PC"


def test_missing_permissions_are_explained_and_give_unknown_not_blocked():
    for access in ("capacity_not_visible", "scope_not_granted"):
        r = risk.analyze(facts(capacity=capacity(access=access, access_text="The capacity isn't visible to you",
                                                 action="Ask your capacity admin.")), now=NOW)
        cap = by_check(r)["capacity"]
        assert r["result"] == risk.UNKNOWN and cap["outcome"] == risk.UNKNOWN
        assert "doesn't stop the run" in cap["detail"] and cap["remedy"] == "Ask your capacity admin."
    no_access = risk.analyze(facts(capacity=capacity(access="workspace_no_access", status="unknown")), now=NOW)
    assert no_access["result"] == risk.BLOCKED and by_check(no_access)["capacity"]["title"] == "You can't open the workspace"
    unread = risk.analyze(facts(capacity=None), now=NOW)
    assert unread["result"] == risk.UNKNOWN and "Refresh" in by_check(unread)["capacity"]["remedy"]
    stale_ok = risk.analyze(facts(capacity=capacity(age_s=5 * 3600)), now=NOW)
    assert stale_ok["result"] == risk.UNKNOWN and "5 hours ago" in by_check(stale_ok)["capacity"]["detail"]


def test_failures_a_run_in_progress_and_a_schedule_due_soon_warn_with_evidence():
    r = risk.analyze(facts(runs=[run("failed", 30, 60, "Activity Copy failed"), run("succeeded", 90, 60)]), now=NOW)
    f = by_check(r)["failures"]
    assert r["result"] == risk.WARNING and f["can_override"] and "Activity Copy failed" in f["evidence"][0]
    often = risk.analyze(facts(runs=[run("succeeded", 10, 60), run("failed", 20, 60), run("failed", 30, 60)]), now=NOW)
    assert by_check(often)["failures"]["title"] == "2 of the last 3 runs failed"
    off = risk.analyze(facts(runs=[run("succeeded", 10, 60), run("failed", 20, 60), run("failed", 30, 60)]),
                       {"warn_failed_runs": 0}, now=NOW)
    assert "failures" not in by_check(off)
    going = risk.analyze(facts(runs=[run("running", 5)]), now=NOW)
    assert by_check(going)["concurrency"]["outcome"] == risk.WARNING
    soon = risk.analyze(facts(where="this_pc", capacity=None, runs=[run("succeeded", 100, 60) | {"where": "this_pc"}],
                              schedules=[{"where": "This PC", "text": "Daily at 12:30", "enabled": True,
                                          "next_run": (NOW + dt.timedelta(minutes=30)).isoformat()}]), now=NOW)
    assert soon["result"] == risk.WARNING and by_check(soon)["schedule"]["title"] == "A schedule runs it soon"
    later = risk.analyze(facts(schedules=[{"where": "This PC", "text": "Daily", "enabled": True,
                                           "next_run": (NOW + dt.timedelta(hours=5)).isoformat()}]), now=NOW)
    assert by_check(later)["schedule"]["outcome"] == risk.PASS


def test_unknown_cu_stays_unknown_and_a_threshold_warns_on_measured_or_estimated_cu():
    r = risk.analyze(facts(runs=[run("succeeded", 60, 600)]), now=NOW)
    c = by_check(r)["consumption"]
    assert c["outcome"] == risk.UNKNOWN and c["measurement"] is None and "rather than 0" in c["detail"]
    assert r["result"] == risk.PASS  # unknown CU alone doesn't make the whole result unknown
    est = ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, 7200 * 8, method="run time x 8 CU",
                  assumptions=["8 CU"], run_id="j")
    measured = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_SECONDS, 3600 * 2, source="Capacity Metrics export",
                       collected_at=at(5), run_id="j")
    plain = by_check(risk.analyze(facts(estimated=[est]), now=NOW))["consumption"]
    assert plain["outcome"] == risk.PASS and "(estimated)" in plain["title"] and "16.00 CU hours" in plain["title"]
    over = by_check(risk.analyze(facts(estimated=[est]), {"warn_run_cu_hours": 10}, now=NOW))["consumption"]
    assert over["outcome"] == risk.WARNING and "estimated" in over["title"]
    prefer = by_check(risk.analyze(facts(measured=[measured], estimated=[est]), {"warn_run_cu_hours": 10}, now=NOW))
    assert prefer["consumption"]["outcome"] == risk.PASS and "(measured)" in prefer["consumption"]["title"]
    broken = by_check(risk.analyze(facts(estimated=[est], measured_problem="Imported usage couldn't be read (bad file)"),
                                   now=NOW))["consumption"]
    assert "Imported usage couldn't be read (bad file)" in broken["evidence"]  # said, not swallowed


def test_a_notebook_that_needs_fabric_is_not_offered_for_this_pc():
    needs = {"local": {"ok": False, "reasons": [{"cell": 2, "text": "uses mssparkutils"}]}, "fabric": {"ok": True, "reasons": []}}
    alt = by_check(risk.analyze(facts(compatibility=needs), now=NOW))["local_alternative"]
    assert alt["title"] == "Needs Fabric" and "Cell 2: uses mssparkutils" in alt["evidence"]
    either = {"local": {"ok": True, "reasons": []}, "fabric": {"ok": True, "reasons": []}}
    assert by_check(risk.analyze(facts(compatibility=either), now=NOW))["local_alternative"]["title"] == "This PC could run it too"
    broken = {"local": {"ok": True, "reasons": []},
              "fabric": {"ok": False, "reasons": [{"cell": 1, "text": "reads a path on this PC"}]}}
    w = risk.analyze(facts(compatibility=broken), now=NOW)
    assert w["result"] == risk.WARNING and by_check(w)["compatibility"]["evidence"] == ["Cell 1: reads a path on this PC"]


def test_definition_problems_and_the_memory_guard_block_and_an_old_version_warns():
    r = risk.analyze(facts(definition_errors=["source table missing"], version={"behind": True, "deployed": "abcdef123"}),
                     now=NOW)
    assert r["result"] == risk.BLOCKED and r["findings"][0]["title"] == "The definition has problems"
    assert any(f["title"] == "This environment runs an older version" for f in r["findings"])
    est = {"files": 3, "files_total": 3, "rows": 5_000_000, "bytes_on_disk": 400 * 1024 ** 2, "memory_bytes": 9 * 1024 ** 3,
           "exact_rows": True}
    local = risk.analyze(facts(where="this_pc", capacity=None, input={"estimate": est, "stop": "needs 27 GB, 8 GB free"}),
                         now=NOW)
    assert local["result"] == risk.BLOCKED and by_check(local)["input"]["title"] == "Too big for this PC's free memory"
    fabric = by_check(risk.analyze(facts(input={"estimate": est, "stop": "needs 27 GB"}), now=NOW))["input"]
    assert fabric["outcome"] == risk.PASS and "5,000,000 rows" in fabric["title"]
    unknown = by_check(risk.analyze(facts(input={"error": "no access"}), now=NOW))["input"]
    assert unknown["outcome"] == risk.UNKNOWN


def test_the_same_findings_give_the_same_id_and_findings_are_ordered_worst_first():
    a = risk.analyze(facts(runs=[run("failed", 30, 60, "x")]), now=NOW)
    b = risk.analyze(facts(runs=[run("failed", 30, 60, "x")]), now=NOW + dt.timedelta(minutes=1))
    assert a["id"] == b["id"]
    c = risk.analyze(facts(runs=[run("failed", 30, 60, "x")], capacity=capacity("not_running")), now=NOW)
    assert c["id"] != a["id"] and c["findings"][0]["outcome"] == risk.BLOCKED
    with pytest.raises(ValueError):
        risk.finding("x", "maybe", "t", "d")
    assert risk.settings({"warn_run_cu_hours": 5, "nonsense": 1}) == risk.DEFAULTS | {"warn_run_cu_hours": 5}


# --------------------------------------------------------------------------- the service


class ReadOnlyFabric:
    """Answers the two reads the check makes; anything else would be a mistake."""
    calls: list = []

    def __init__(self, token=None):
        pass

    def list_capacities(self):
        ReadOnlyFabric.calls.append("list_capacities")
        return [{"id": "CAP1", "displayName": "fabcapdev", "sku": "F8", "region": "West Europe", "state": "Inactive"}]

    def get_workspace(self, ws):
        ReadOnlyFabric.calls.append(f"get_workspace {ws}")
        return {"id": ws, "displayName": "Sales Dev", "capacityId": "cap1"}

    def __getattr__(self, name):
        raise AssertionError(f"the risk check must not call {name}")


@pytest.fixture
def item(svc, monkeypatch):  # noqa: F811
    signed_in(svc)
    scan(svc, monkeypatch)
    ReadOnlyFabric.calls = []
    monkeypatch.setattr(estate_service, "FabricClient", ReadOnlyFabric)
    return model.fabric_id("ws1", "pl1")


def test_the_service_reads_the_capacity_and_blocks_a_paused_one_without_starting_anything(svc, item):  # noqa: F811
    r = svc.risk_check(item, "run_item")
    assert r["result"] == risk.BLOCKED and by_check(r)["capacity"]["title"] == "fabcapdev is paused"
    assert ReadOnlyFabric.calls == ["list_capacities", "get_workspace ws1"]
    assert any("GET /workspaces" in x for x in r["reads"])
    assert svc._read_fabric_runs() == [] and svc.workload_actions() == []
    with pytest.raises(ValueError):
        svc.risk_check(item, "cancel")


def test_the_run_dialog_carries_the_risk_and_the_run_itself_does_not_wait_for_it(svc, item, monkeypatch):  # noqa: F811
    pre = svc.workload_preflight(item, "run_item")
    assert pre["allowed"] and pre["risk"]["result"] == risk.BLOCKED
    monkeypatch.setattr(svc, "_risk_facts", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    pre = svc.workload_preflight(item, "run_item")
    assert pre["allowed"] and pre["risk"]["result"] == risk.UNKNOWN and "boom" in pre["risk"]["summary"]
    assert svc.workload_preflight(item, "run_item", assess=False)["risk"] is None
    monkeypatch.setattr(svc, "run_fabric_item_now", lambda ws, it, job, name, ws_name="": svc._log_fabric_run(
        {"kind": "Scheduled item run", "name": name, "workspace_id": ws, "item_id": it, "job_id": "job-1"}))
    assert svc.workload_act(item, "run_item")["action"]["status"] == "submitted"  # the check didn't run


def test_warnings_can_be_acknowledged_and_recorded_but_blocked_or_changed_results_cannot(svc, item, monkeypatch):  # noqa: F811
    with pytest.raises(PermissionError):
        svc.risk_acknowledge(item, "run_item", "r-whatever")
    monkeypatch.setattr(ReadOnlyFabric, "list_capacities", lambda self: [{"id": "cap1", "displayName": "fabcapdev",
                                                                          "sku": "F8", "state": "Active"}])
    svc._log_fabric_run({"kind": "Scheduled item run", "name": "pl_load_erp", "workspace_id": "ws1", "item_id": "pl1",
                         "job_id": "j1", "status": "Failed", "message": "Activity Copy failed",
                         "finished_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    svc._wl_records = None
    r = svc.risk_check(item, "run_item")
    assert r["result"] == risk.WARNING and by_check(r)["failures"]["outcome"] == risk.WARNING
    with pytest.raises(ValueError):
        svc.risk_acknowledge(item, "run_item", "r-0000000000000000")
    rec = svc.risk_acknowledge(item, "run_item", r["id"], note="known: source fixed")
    assert rec["warnings"] == ["The last run failed"] and rec["note"] == "known: source fixed"
    assert svc.risk_acknowledgements()[0]["risk_id"] == r["id"]
    line = json.loads((svc.root / "risk_acknowledgements.jsonl").read_text().splitlines()[-1])
    assert line["user"] and line["result"] == risk.WARNING


def test_settings_turn_warnings_on_and_off(svc):  # noqa: F811
    assert svc.risk_settings() == risk.DEFAULTS
    cfg = svc.set_risk_settings(warn_run_cu_hours=2.5)
    assert cfg["warn_run_cu_hours"] == 2.5 and svc.risk_settings()["warn_run_cu_hours"] == 2.5
    assert "require_acknowledgement" not in cfg  # not offered until something enforces it
    assert svc.set_risk_settings(warn_run_cu_hours=0)["warn_run_cu_hours"] is None
    with pytest.raises(ValueError):
        svc.set_risk_settings(warn_failed_runs=-1)


def test_the_policy_is_read_from_the_organization_settings(svc, item, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app_settings, "value", lambda key: False if key == "allow_fabric_runs" else None)
    r = svc.risk_check(item, "run_item")
    assert by_check(r)["policy"]["outcome"] == risk.BLOCKED


def test_reading_the_risk_is_not_audited_and_acknowledging_is():
    from fabric_desktop import audit
    assert {"risk_check", "risk_settings", "risk_acknowledgements"} <= audit.READ_ONLY
    assert not {"risk_acknowledge", "set_risk_settings"} & audit.READ_ONLY


def test_an_external_tool_can_ask_for_the_risk_and_gets_the_same_answer(svc, item):  # noqa: F811
    from fabric_desktop.integrations import services
    from fabric_desktop.integrations.registry import default_registry
    cap = default_registry().get("fabric_desktop_check_run_risk")
    assert cap.permission == "READ_METADATA" and cap.status == "preview"
    out = services.check_run_risk(svc, None, item, "run_item")
    assert out["result"] == risk.BLOCKED and out["id"] == svc.risk_check(item, "run_item")["id"]
