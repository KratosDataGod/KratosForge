"""Workload health edges the main tests don't pin down (mutation batch 137, docs/quality/mutation-testing.md):
when a workload is partly observed, a definition problem that isn't about runs, default texts, and the
version line's short hashes."""

from fabric_desktop.workloads.health import assess, findings


def dep(state="live", run=None, **kw):
    return {"env": "prod", "env_label": "Prod", "location": {"label": "Prod"}, "observation": {"state": state},
            "latest_run": run, **kw}


OK = {"state": "ok", "id": "r1"}


def test_partly_observed_means_some_deployment_was_read_and_another_wasnt():
    assert assess({"deployments": [dep(), dep("signed_out")]})["partial"] is True
    assert assess({"deployments": [dep(), dep()]})["partial"] is False
    assert assess({"deployments": [dep("signed_out"), dep("error")]})["partial"] is False  # nothing read at all
    # One that couldn't be read now but has a last run is still observed.
    assert assess({"deployments": [dep(), dep("signed_out", OK)]})["partial"] is False


def test_a_definition_problem_alone_doesnt_make_a_running_workload_need_attention():
    out = assess({"errors": ["bad"], "deployments": [dep(run=OK)], "owners": {"o": {"value": "dana"}}})
    assert out["health"] == "healthy" and out["readiness"]["state"] == "blocked"


def test_a_run_waiting_for_a_decision_or_cancelled_has_a_default_text():
    [f] = [x for x in findings({"deployments": [dep(run={"state": "needs_decision"})]}) if x["kind"] == "run"]
    assert f["text"] == "Schema drift or quarantined rows are waiting for review."
    [f] = [x for x in findings({"deployments": [dep(run={"state": "cancelled"})]}) if x["kind"] == "run"]
    assert f["text"] == ""
    [f] = [x for x in findings({"deployments": [dep(run={"state": "cancelled", "message": "stopped by dana"})]})
           if x["kind"] == "run"]
    assert f["text"] == "stopped by dana"


def test_an_older_version_is_named_by_eight_characters_of_each_hash():
    d = dep(run=OK, version={"behind": True, "deployed": "0123456789abcdef", "current": "fedcba9876543210",  # pragma: allowlist secret (test value: fake commit hashes)
                             "at": "2026-11-17T10:00:00Z"})
    [f] = [x for x in findings({"deployments": [d]}) if x["kind"] == "release"]
    assert f["text"] == "Prod has version 01234567 (promoted 2026-11-17); the project is at fedcba98."
