"""Health and readiness findings (workloads/health.py) for every kind of evidence: each finding says
what it rests on, and a source that can't be read is never a failure of the workload."""

import pytest

from fabric_desktop.workloads.health import assess, findings

OWNED = {"technical_owner": {"value": "chris@contoso.com"}}


def _dep(run=None, obs="live", **extra):
    return {"env": "dev", "env_label": "Dev", "location": {"kind": "this_pc", "label": "This PC"},
            "observation": {"state": obs, "source": "meta.run_log in Dev", "collected_at": "2026-11-06T09:00:00Z"},
            "latest_run": run, "actions": ["run"], **extra}


def _w(*deps, **extra):
    return {"deployments": list(deps), "owners": OWNED, **extra}


def _run(state, **extra):
    return {"id": "r1", "state": state, "source": "meta.run_log", "started_at": "2026-11-06T08:00:00Z", **extra}


@pytest.mark.parametrize("run, health, title", [
    (_run("failed", message="boom"), "failing", "Last run in Dev failed"),
    (_run("needs_decision"), "attention", "Last run in Dev needs a decision"),
    (_run("cancelled"), "attention", "Last run in Dev was cancelled"),
    (_run("ok", mocked=1), "attention", "Last run in Dev used mocks"),
    (_run("running"), "healthy", "Running in Dev"),
    (_run("ok"), "healthy", None),
])
def test_each_kind_of_last_run_gives_its_finding_and_health(run, health, title):
    out = assess(_w(_dep(run)))
    assert out["health"] == health and out["readiness"]["state"] == "ready"
    assert [f["title"] for f in out["findings"]] == ([title] if title else [])
    if title:
        assert out["findings"][0]["evidence"]["run_id"] == "r1"


def test_a_failed_run_offers_open_and_one_retry():
    [f] = findings(_w(_dep(_run("failed"), actions=["run", "run_schedule"])))
    assert [a["action"] for a in f["next"]] == ["open_run", "run"]


def test_mocks_say_how_many_activities_were_mocked():
    [one] = findings(_w(_dep(_run("ok", mocked=1))))
    [three] = findings(_w(_dep(_run("ok", mocked=3))))
    assert "1 activity was mocked" in one["text"] and "3 activities were mocked" in three["text"]


def test_a_last_run_with_a_status_nobody_knows_is_not_called_healthy():
    # It was "Healthy": the finding was raised at the "unknown" level, which didn't count towards health.
    out = assess(_w(_dep(_run("unknown", native_status="Deduped"))))
    assert out["health"] == "unknown"
    [f] = out["findings"]
    assert f["title"] == "Last run in Dev has a status KratosForge doesn't know" and f["text"] == "Deduped"


def test_a_source_that_cant_be_read_is_unknown_never_failing():
    out = assess(_w(_dep(obs="signed_out")))
    [f] = out["findings"]
    assert out["health"] == "unknown" and f["kind"] == "telemetry" and f["next"][0]["action"] == "sign_in"
    assert f["text"].endswith("This says nothing about the workload itself.")
    stale = findings(_w(_dep(_run("ok"), obs="stale")))
    assert [f["title"] for f in stale] == ["Dev: information is out of date"]


def test_one_unread_environment_makes_the_view_partial():
    prod = _dep(obs="unauthorized") | {"env": "prod", "env_label": "Prod"}
    out = assess(_w(_dep(_run("ok")), prod))
    assert out["health"] == "healthy" and out["partial"]


def test_freshness_late_and_never_loaded():
    late = assess(_w(_dep(_run("ok"), freshness={"status": "late", "reasons": ["no load since 06:00"],
                                                  "expected": "daily by 07:00"})))
    assert late["health"] == "late" and late["findings"][0]["text"] == "no load since 06:00"
    never = assess(_w(_dep(freshness={"status": "never", "expected": "daily by 07:00"})))
    assert never["health"] == "attention" and never["findings"][0]["title"] == "Never loaded in Dev"
    unread = findings(_w(_dep(obs="offline", freshness={"status": "never"})))
    assert [f["kind"] for f in unread] == ["telemetry"]  # never loaded isn't claimed from a source not read


def test_an_older_version_and_a_name_only_link_are_notices():
    fs = findings(_w(_dep(_run("ok"), version={"behind": True, "deployed": "abcdef123456", "current": "99999999aaaa",
                                               "at": "2026-11-01T00:00:00Z"},
                          link={"how": "name match", "item_id": "nb1"})))
    assert [f["kind"] for f in fs] == ["release", "identity"] and {f["level"] for f in fs} == {"notice"}
    assert "version abcdef12 (promoted 2026-11-01)" in fs[0]["text"]
    assert assess(_w(_dep(_run("ok"), version={"behind": True})))["health"] == "healthy"


def test_definition_problems_block_readiness():
    out = assess(_w(_dep(), errors=["a", "b", "c", "d"]))
    assert out["readiness"]["state"] == "blocked" and out["findings"][0]["text"] == "a; b; c"


def test_nothing_deployed_is_not_ready_and_no_owner_is_a_gap():
    out = assess({"deployments": [], "owners": {"team": {"value": "me", "placeholder": True}}})
    assert out["readiness"]["state"] == "unknown" and out["health"] == "unknown" and out["unowned"]
    assert out["findings"][0]["title"] == "Owner is a placeholder (me)"
    assert assess({"deployments": []})["findings"][0]["title"] == "No owner"


def test_findings_are_worst_first():
    fs = findings(_w(_dep(_run("ok"), version={"behind": True}),
                     _dep(_run("failed")) | {"env": "prod", "env_label": "Prod"}, errors=["x"]))
    assert [f["level"] for f in fs] == ["failing", "attention", "notice"]
