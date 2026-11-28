"""Mutation-testing follow-up for the "Before a run starts" analyzer (risk.py): exact wording of
ages and durations at their boundaries, the defaults, which finding wins the summary, and the
evidence each check writes. Everything here is pure: facts in, an assessment out."""

import datetime as dt

import pytest

from fabric_desktop import risk
from fabric_desktop import measurement as ms

NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def at(minutes_ago: float) -> str:
    return (NOW - dt.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def reading(status="running", access="ok", age_s=60, **extra):
    obs = {"access": access, "access_text": "", "status": status, "status_text": "", "action": "Act.",
           "workspace_id": "ws1", "workspace_name": "Sales Dev", "capacity_id": "cap1",
           "capacity": {"name": "fabcapdev", "sku": "F8"}, "observed_at": at(age_s / 60), "age_seconds": age_s,
           "stale": False, "cached": False}
    return obs | extra


def facts(**kw):
    base = {"workload_id": "w1", "workload": "pl", "action": "run_item", "label": "run item", "env": None,
            "env_label": "", "where": "fabric", "policy": {"allow_fabric_runs": True}, "signed_in": True,
            "capacity": reading(), "runs": [], "measured": [], "estimated": [], "schedules": []}
    return base | kw


def local(**kw):
    return facts(where="this_pc", capacity=None) | kw


def run(state, started_min_ago, seconds=None, message="", ran_on="Fabric · Sales Dev"):
    started = NOW - dt.timedelta(minutes=started_min_ago)
    ended = started + dt.timedelta(seconds=seconds) if seconds is not None else None
    return {"state": state, "started_at": started.isoformat(), "ended_at": ended.isoformat() if ended else None,
            "seconds": None, "message": message, "ran_on": ran_on}


def by_check(result):
    return {f["check"]: f for f in result["findings"]}


# ------------------------------------------------------------------ wording


@pytest.mark.parametrize("seconds,text", [
    (None, "at an unknown time"), (0, "just now"), (89, "just now"), (90, "2 minutes ago"),
    (5399, "90 minutes ago"), (5400, "2 hours ago"), (2 * 86400 - 1, "48 hours ago"),
    (2 * 86400, "2 days ago"), (3 * 86400, "3 days ago"),
])
def test_ages_read_in_the_right_unit_at_each_boundary(seconds, text):
    assert risk._age(seconds) == text


@pytest.mark.parametrize("seconds,text", [
    (0, "0 s"), (89, "89 s"), (90, "2 min"), (5399, "90 min"), (5400, "1.5 h"), (7200, "2.0 h"),
])
def test_durations_read_in_the_right_unit_at_each_boundary(seconds, text):
    assert risk.duration(seconds) == text


def test_the_defaults_and_the_worst_first_order():
    assert risk.DEFAULTS == {"warn_run_cu_hours": None, "warn_failed_runs": 2, "schedule_window_minutes": 60,
                             "require_acknowledgement": False}
    assert risk._ORDER == {risk.BLOCKED: 0, risk.WARNING: 1, risk.UNKNOWN: 2, risk.PASS: 3}
    assert risk.settings({"warn_failed_runs": 4, "bogus": 1}) == risk.DEFAULTS | {"warn_failed_runs": 4}
    assert risk.settings(None) == risk.DEFAULTS


def test_a_finding_keeps_its_fields_and_only_a_warning_can_be_overridden():
    f = risk.finding("x", risk.WARNING, "T", "D", evidence=["a", "", None, "b"], remedy="R", source="S",
                     observed_at="when", essential=True, measurement={"v": 1})
    assert f == {"check": "x", "outcome": "warning", "label": "Warning", "title": "T", "detail": "D",
                 "evidence": ["a", "b"], "remedy": "R", "source": "S", "observed_at": "when", "essential": True,
                 "measurement": {"v": 1}, "can_override": True}
    assert risk.finding("x", risk.BLOCKED, "T", "D")["can_override"] is False
    with pytest.raises(ValueError, match="unknown outcome 'maybe'"):
        risk.finding("x", "maybe", "T", "D")


# ------------------------------------------------------------------ policy and sign-in


def test_policy_passes_by_default_and_blocks_only_when_turned_off():
    ok = risk.check_policy(facts(policy={}))
    assert ok["outcome"] == risk.PASS and ok["essential"] is False and ok["source"] == risk.POLICY_SOURCE
    no = risk.check_policy(facts(policy={"allow_fabric_runs": False}))
    assert no["outcome"] == risk.BLOCKED and no["essential"] is True
    assert risk.check_policy(local()) is None


def test_sign_in_is_needed_for_fabric_or_when_asked_and_blocks_as_essential():
    out = risk.check_sign_in(facts(signed_in=False))
    assert out["outcome"] == risk.BLOCKED and out["essential"] is True and out["remedy"] == "Sign in first."
    assert risk.check_sign_in(local(signed_in=False)) is None
    assert risk.check_sign_in(local(signed_in=False, needs_sign_in=True))["outcome"] == risk.BLOCKED
    assert risk.check_sign_in(facts(signed_in=True)) is None


# ------------------------------------------------------------------ capacity


def test_a_cached_reading_says_so_in_its_evidence():
    fresh = risk.check_capacity(facts(), NOW)
    assert fresh["evidence"] == ["Workspace: Sales Dev", "Capacity: fabcapdev (F8)", "Read just now"]
    cached = risk.check_capacity(facts(capacity=reading(cached=True)), NOW)
    assert cached["evidence"][2] == "Read just now (an older reading: the last one failed)"
    assert cached["outcome"] == risk.UNKNOWN  # cached counts as stale: running then, not known now


def test_capacity_not_visible_uses_its_own_title_or_a_default():
    titled = risk.check_capacity(facts(capacity=reading(access="scope_not_granted", access_text="Not granted")), NOW)
    assert titled["outcome"] == risk.UNKNOWN and titled["title"] == "Not granted" and titled["remedy"] == "Act."
    plain = risk.check_capacity(facts(capacity=reading(access="capacity_not_visible")), NOW)
    assert plain["title"] == "The capacity can't be read" and "doesn't stop the run" in plain["detail"]
    other = risk.check_capacity(facts(capacity=reading(access="throttled")), NOW)
    assert other["title"] == "The capacity couldn't be read" and other["detail"] == "Its state isn't known."


def test_a_paused_capacity_uses_the_status_text_when_there_is_one():
    paused = risk.check_capacity(facts(capacity=reading(status="not_running", status_text="Paused by admin")), NOW)
    assert paused["outcome"] == risk.BLOCKED and paused["detail"] == "Paused by admin"
    default = risk.check_capacity(facts(capacity=reading(status="not_running")), NOW)
    assert default["detail"] == "The capacity isn't running, so Fabric refuses new jobs."
    stale = risk.check_capacity(facts(capacity=reading(status="not_running", stale=True, age_s=7200)), NOW)
    assert stale["outcome"] == risk.WARNING and stale["detail"].startswith("It was not running 2 hours ago.")


def test_an_unknown_status_names_the_capacity_and_essential_unknown_decides_the_result():
    out = risk.check_capacity(facts(capacity=reading(status="weird", capacity={})), NOW)
    assert out["title"] == "cap1's state isn't known" and out["essential"] is True
    r = risk.analyze(facts(capacity=reading(status="weird")), now=NOW)
    assert r["result"] == risk.UNKNOWN and r["summary"] == "Couldn't check everything: fabcapdev's state isn't known"


# ------------------------------------------------------------------ definition and compatibility


def test_definition_errors_show_the_first_three_and_an_old_version_is_named():
    out = risk.check_definition({"definition_errors": ["a", "b", "c", "d"], "env_label": "Test",
                                 "version": {"behind": True, "deployed": "0123456789abcdef"}})
    assert out[0]["detail"] == "a; b; c" and out[0]["essential"] is True
    assert out[1]["detail"].startswith("Test has version 01234567; your newer changes")
    unnamed = risk.check_definition({"version": {"behind": True, "deployed": "abc"}})
    assert unnamed[0]["detail"].startswith("This environment has version abc;")


def test_compatibility_names_the_place_the_run_goes():
    compat = {"local": {"reasons": [{"cell": 2, "text": "uses spark.conf"}]}, "fabric": {}}
    here = risk.check_compatibility(local(compatibility=compat))
    assert here[0]["title"] == "Parts of the notebook may not run on this PC"
    assert here[0]["evidence"] == ["Cell 2: uses spark.conf"]
    there = risk.check_compatibility(facts(compatibility={"fabric": {"reasons": [{"text": "uses %pip"}]}, "local": {}}))
    assert there[0]["title"] == "Parts of the notebook may not run on Fabric"
    assert there[0]["evidence"] == ["uses %pip"]


# ------------------------------------------------------------------ history


def test_a_running_run_says_when_it_started_and_where_it_ran():
    out = risk.check_history({"runs": [run("running", 30), run("succeeded", 90, 60)]}, risk.DEFAULTS, NOW)
    going = out[0]
    assert going["check"] == "concurrency"
    assert going["detail"].startswith("A run of this workload started 30 minutes ago and hasn't finished.")
    assert going["evidence"] == [f"Fabric · Sales Dev · started {run('running', 30)['started_at']}"]
    blank = risk.check_history({"runs": [run("running", 30, ran_on=None) | {"started_at": None}]}, risk.DEFAULTS, NOW)
    assert blank[0]["detail"].startswith("A run of this workload started earlier and")
    assert blank[0]["evidence"] == [" · started None"]


def test_only_the_last_five_finished_runs_count():
    runs = [run("succeeded", 10 * i, 60) for i in range(1, 6)] + [run("failed", 100 + i) for i in range(5)]
    out = {f["check"]: f for f in risk.check_history({"runs": runs}, risk.DEFAULTS, NOW)}
    assert "failures" not in out
    assert out["runtime"]["detail"] == "Median of 5 successful runs; the longest took 60 s."


def test_one_successful_run_is_singular():
    out = {f["check"]: f for f in risk.check_history({"runs": [run("succeeded", 10, 120)]}, risk.DEFAULTS, NOW)}
    assert out["runtime"]["detail"] == "Median of 1 successful run; the longest took 2 min."
    assert out["runtime"]["title"] == "Usually takes about 2 min"


def test_frequent_failures_warn_at_the_threshold_and_a_zero_threshold_turns_it_off():
    runs = [run("succeeded", 10, 60), run("failed", 20, message="boom"), run("failed", 30)]
    out = {f["check"]: f for f in risk.check_history({"runs": runs}, risk.DEFAULTS, NOW)}
    assert out["failures"]["title"] == "2 of the last 3 runs failed"
    assert out["failures"]["evidence"] == [f"{runs[1]['started_at']}: boom", f"{runs[2]['started_at']}: no message recorded"]
    off = {f["check"] for f in risk.check_history({"runs": runs}, risk.settings({"warn_failed_runs": 0}), NOW)}
    assert "failures" not in off


# ------------------------------------------------------------------ consumption


def _est(value=7200 * 8, run_id="j", **kw):
    return ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, value, method="run time x 8 CU", run_id=run_id,
                   assumptions=["8 CU"], **kw)


def test_consumption_skips_empty_and_valueless_measurements():
    out = risk.check_consumption(facts(measured=[None, {"value": None}], estimated=[None, _est()]), risk.DEFAULTS)
    assert out["outcome"] == risk.PASS and "(estimated)" in out["title"]
    assert out["evidence"][0] == "Estimated: 16.000 CU hours (run j)"
    assert out["evidence"][1] == "No import measured these runs; the figure is the app's estimate from run time."
    assert out["source"] == "run time x 8 CU"
    nothing = risk.check_consumption(facts(estimated=[{"value": None}]), risk.DEFAULTS)
    assert nothing["outcome"] == risk.UNKNOWN


def test_consumption_evidence_without_a_run_id_and_with_a_source():
    m = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_SECONDS, 3600, source="Capacity Metrics export",
                collected_at=at(5))
    out = risk.check_consumption(facts(measured=[m]), risk.settings({"warn_run_cu_hours": 0.5}))
    assert out["outcome"] == risk.WARNING and out["evidence"] == ["Measured: 1.000 CU hours"]
    assert out["source"] == "Capacity Metrics export"
    assert out["title"] == "The last run used more than 0.5 CU hours (measured)"
    under = risk.check_consumption(facts(measured=[m]), risk.settings({"warn_run_cu_hours": 2}))
    assert under["outcome"] == risk.PASS and under["source"] == "Capacity Metrics export"
    assert risk.check_consumption(local(), risk.DEFAULTS) is None


# ------------------------------------------------------------------ input and schedules


def test_input_notes_and_defaults():
    assert risk.check_input({"input": {"error": "no log"}})["detail"] == "The source couldn't be sized: no log"
    assert risk.check_input({"input": {"note": "CSV"}})["detail"] == "CSV"
    assert risk.check_input({"input": {"x": 1}})["detail"] == "Only Delta sources are sized."
    est = {"rows": 1000, "files": 2, "files_total": 3, "bytes_on_disk": 2 * 1024 ** 2}
    ok = risk.check_input(facts(input={"estimate": est}))
    assert ok["outcome"] == risk.PASS and ok["title"] == "Reads about 1,000 rows in 2 of 3 files (2.0 MB on disk)"
    assert ok["detail"] == "From the Delta log, without reading the data."
    assert risk.check_input(facts(input={"estimate": est, "note": "Sampled"}))["detail"] == "Sampled"
    big = risk.check_input(local(input={"estimate": est, "stop": "Needs 9 GB"}))
    assert big["outcome"] == risk.BLOCKED and big["essential"] is True
    assert risk.check_input(facts(input={"estimate": est, "stop": "Needs 9 GB"}))["outcome"] == risk.PASS


def test_schedules_list_at_most_four_and_say_the_window():
    soon = at(-30)
    scheds = [{"enabled": True, "where": f"pc{i}", "text": "daily", "next_run": soon} for i in range(5)]
    scheds.append({"enabled": True, "where": "pcX"})
    out = risk.check_schedules({"schedules": scheds}, risk.DEFAULTS, NOW)
    assert out["outcome"] == risk.WARNING and "within the next 60 minutes;" in out["detail"]
    assert out["evidence"] == [f"pc{i}: daily, next {soon}" for i in range(4)]
    plain = risk.check_schedules({"schedules": [{"enabled": True, "where": "pc"}]}, risk.DEFAULTS, NOW)
    assert plain["outcome"] == risk.PASS and plain["evidence"] == ["pc: scheduled"]
    later = risk.check_schedules({"schedules": [{"enabled": True, "where": "pc", "next_run": at(-61)}]}, risk.DEFAULTS, NOW)
    assert later["outcome"] == risk.PASS
    assert risk.check_schedules({"schedules": [{"enabled": False, "next_run": soon}]}, risk.DEFAULTS, NOW) is None


# ------------------------------------------------------------------ the result


def test_a_blocked_summary_names_the_first_blocked_finding():
    r = risk.analyze(facts(signed_in=False), now=NOW)
    assert r["result"] == risk.BLOCKED and r["summary"] == "This run can't start: You're not signed in"
    assert r["can_proceed"] is False and r["needs_acknowledgement"] is False


def test_warnings_are_counted_and_acknowledgement_follows_the_setting():
    runs = [run("running", 5), run("failed", 30)]
    r = risk.analyze(facts(runs=runs), now=NOW)
    assert r["result"] == risk.WARNING and r["summary"] == "2 thing(s) to check before you run"
    assert r["needs_acknowledgement"] is False
    assert risk.analyze(facts(runs=runs), {"require_acknowledgement": True}, now=NOW)["needs_acknowledgement"] is True
    passing = risk.analyze(local(), {"require_acknowledgement": True}, now=NOW)
    assert passing["result"] == risk.PASS and passing["needs_acknowledgement"] is False


def test_a_non_essential_unknown_alone_still_passes():
    r = risk.analyze(local(), now=NOW)
    assert {f["outcome"] for f in r["findings"]} == {risk.PASS, risk.UNKNOWN}
    assert r["result"] == risk.PASS and r["summary"] == "Nothing found that should stop this run"


def test_the_id_is_short_and_changes_with_the_findings():
    a = risk.analyze(facts(), now=NOW)["id"]
    assert a.startswith("r-") and len(a) == 18
    assert risk.analyze(facts(), now=NOW + dt.timedelta(hours=1))["id"] == a
    assert risk.analyze(facts(signed_in=False), now=NOW)["id"] != a


def test_long_ages_and_durations_round_at_the_unit_boundary():
    assert risk._age(2.5 * 86400 + 1) == "3 days ago"
    assert risk.duration(7381) == "2.1 h"


def test_a_sixth_finished_run_doesnt_count_toward_failures():
    runs = [run("succeeded", 10, 60), run("failed", 20), run("succeeded", 30, 60), run("succeeded", 40, 60),
            run("succeeded", 50, 60), run("failed", 60)]
    assert "failures" not in {f["check"] for f in risk.check_history({"runs": runs}, risk.DEFAULTS, NOW)}


def test_at_most_three_running_runs_are_listed():
    out = risk.check_history({"runs": [run("running", m) for m in (5, 6, 7, 8)]}, risk.DEFAULTS, NOW)
    assert len(out[0]["evidence"]) == 3
