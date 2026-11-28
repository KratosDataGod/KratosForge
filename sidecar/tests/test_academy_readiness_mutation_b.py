"""academy/readiness at its edges, from the mutation run (batch 473): retention at exactly a week, a
challenge with one hint, rows of another certification, platform-verified passes, knowledge points
and their rounding, the domain counts, and what next_exercises picks."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import progress
from fabric_desktop.academy import exercises, readiness, registry
from test_academy_readiness import PACK, _passed, _row, _state


@pytest.fixture
def cert():
    return registry.certification(PACK)


@pytest.mark.parametrize("days,kept", [(7, True), (6, False)])
def test_retention_counts_a_pass_exactly_a_week_later(cert, days, kept):
    profile = progress.empty()
    first = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
    progress.add(profile, _row("E37", cert, at=first.isoformat()))
    progress.add(profile, _row("E37", cert, at=(first + dt.timedelta(days=days)).isoformat()))
    assert _state(profile, "2.2.9")["measures"]["retention"] == (["dp-700/E37"] if kept else [])


def test_a_challenge_with_one_hint_is_still_independent(cert):
    challenge = {**exercises.get(PACK, "E36"), "mode": "challenge"}
    profile = progress.empty()
    progress.add(profile, progress.from_academy(_passed("E36"), challenge, cert, hints_used=1))
    assert _state(profile, "2.2.8")["measures"]["independent"] == ["dp-700/E36"]


def test_a_row_for_another_certification_is_left_out(cert):
    profile = progress.empty()
    row = _row("E37", cert)
    row["detail"]["certification"] = "dp-600"
    progress.add(profile, row)
    assert _state(profile, "2.2.9")["state"] == "not_started"


def test_only_a_platform_verified_pass_counts_as_platform_verified(cert):
    profile = progress.empty()
    progress.add(profile, _row("E37", cert))
    assert _state(profile, "2.2.9")["measures"]["platform_verified"] == []
    verified = _row("E36", cert)
    verified["detail"]["class"] = "fabric_verified"
    progress.add(profile, verified)
    x = _state(profile, "2.2.8")
    assert x["measures"]["platform_verified"] == ["dp-700/E36"] and x["best_class"] == "fabric_verified"


def assessment(cert, oid, per, n=1):
    return {"id": f"a{n}", "type": "academy.assessment", "status": "verified", "at": f"2026-11-0{n}T10:00:00+00:00",
            "artifact": {"name": f"dp-700/x{n}"}, "ref": {"id": f"dp-700/x{n}"},
            "detail": {"certification": PACK, "certification_version": cert["version"], "objectives": [oid],
                       "per_objective": {oid: per}}}


def test_knowledge_points_round_to_two_places_and_missing_counts_are_zero(cert):
    profile = progress.empty()
    profile["evidence"].append(assessment(cert, "2.2.1", {"correct": 1 / 3, "answered": 1}))
    assert _state(profile, "2.2.1")["measures"]["knowledge"] == {"correct": 0.33, "answered": 1}
    profile = progress.empty()
    profile["evidence"].append(assessment(cert, "2.2.1", {}))
    assert _state(profile, "2.2.1")["measures"]["knowledge"] == {"correct": 0.0, "answered": 0}


def test_domains_count_what_is_shown_and_what_is_practised(cert):
    profile = progress.empty()
    progress.add(profile, _row("E37", cert))  # 2.2.9 shown
    profile["evidence"].append(assessment(cert, "2.2.8", {"correct": 1, "answered": 2}))  # 2.2.8 practised
    out = readiness.readiness(profile, PACK)
    dom = next(o["domain"] for o in out["objectives"] if o["id"] == "2.2.9")
    d = next(x for x in out["domains"] if x["id"] == dom)
    assert (d["shown"], d["practised"]) == (1, 1)
    assert all((x["shown"], x["practised"]) == (0, 0) for x in out["domains"] if x["id"] != dom)


# ---- next_exercises, on exercises of the test's own


def state(oid, domain, shown=False, guided=()):
    return {"id": oid, "domain": domain, "state": "shown" if shown else "practised",
            "measures": {"knowledge": {"correct": 0.0, "answered": 0}, "guided": list(guided), "independent": [],
                         "troubleshooting": [], "platform_verified": [], "retention": []}}


def ex(eid, oid):
    return {"id": eid, "objectives": [oid], "title": eid, "mode": "guided", "class": "local_verified", "minutes": 5}


@pytest.fixture
def live(monkeypatch):
    items = [ex("A1", "oa"), ex("A2", "oa"), ex("B1", "ob"), ex("B2", "ob"), ex("B3", "ob")]
    monkeypatch.setattr(exercises, "exercises", lambda pack_id, include_drafts=False: list(items))
    return items


def test_next_picks_three_from_the_domain_with_fewest_shown_first(live):
    states = [state("a0", "a", shown=True), state("oa", "a"), state("ob", "b")]
    picks = readiness.next_exercises(states, PACK)
    assert [p["exercise"] for p in picks] == ["B1", "B2", "B3"]  # domain b has none shown, so it comes first
    assert all(p["objective"] == "ob" for p in picks)
    assert [p["exercise"] for p in readiness.next_exercises(states, PACK, limit=5)] == ["B1", "B2", "B3", "A1", "A2"]


def test_next_skips_exercises_already_passed(live):
    states = [state("ob", "b", guided=["dp-700/B1", "dp-700/B3"]), state("oa", "a")]
    assert [p["exercise"] for p in readiness.next_exercises(states, PACK)] == ["A1", "A2", "B2"]
