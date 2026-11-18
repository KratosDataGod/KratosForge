"""Incident log edges the main tests don't pin down (mutation batch 95, docs/quality/mutation-testing.md):
limits at their exact boundaries, defaults for missing fields, the store's clock for escalation, and
the retries' waits."""

import json
import re
import types

import pytest

from fabric_desktop.leases import LocalBackend
from fabric_desktop.workloads import incidents as inc_mod
from fabric_desktop.workloads.incidents import KEY, Conflict, IncidentLog


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def sig(kind="run_failed", workload="meta:sales:silver.orders", evidence_id="r1", **extra):
    return {"kind": kind, "workload": workload, "name": "silver.orders", "title": "Run failed",
            "text": "boom", "evidence_id": evidence_id, **extra}


def stored(backend):
    return json.loads(backend.read(KEY).data)


@pytest.fixture
def backend(tmp_path):
    return LocalBackend(tmp_path / "control")


def test_a_new_list_is_contract_1_and_a_new_incident_has_its_defaults(backend):
    log = IncidentLog(backend, "prod", Clock())
    [change] = log.sync([sig(owner=None, team=None, criticality=None)], evaluated=True, by="agent")
    doc = stored(backend)
    assert doc["contract"] == 1 and doc["kind"] == "incidents"
    inc = doc["incidents"][0]
    assert re.fullmatch(r"inc-[0-9a-f]{12}", inc["id"])
    assert inc["occurrences"] == 1 and inc["owner"] == "" and inc["team"] == "" and inc["criticality"] == ""
    assert inc["evidence"] == {} and change["change"] == "opened"


def test_seen_again_counts_from_one_when_the_count_is_missing_and_keeps_empty_evidence(backend):
    log = IncidentLog(backend, "prod", Clock())
    log.sync([sig()], evaluated=True, by="agent")
    doc = stored(backend)
    del doc["incidents"][0]["occurrences"]
    got = backend.read(KEY)
    backend.replace(KEY, json.dumps(doc).encode(), got.etag)
    log.sync([sig(evidence_id="r2")], evaluated=True, by="agent")
    inc = stored(backend)["incidents"][0]
    assert inc["occurrences"] == 2 and inc["evidence"] == {}


def test_still_happening_is_noted_from_exactly_seen_every_seconds(backend):
    clock = Clock()
    log = IncidentLog(backend, "prod", clock)
    log.sync([sig()], evaluated=True, by="agent")
    first = stored(backend)["incidents"][0]["last_seen_at"]
    clock.t += inc_mod.SEEN_EVERY - 1
    assert log.sync([sig()], evaluated=True, by="agent") == []
    assert stored(backend)["incidents"][0]["last_seen_at"] == first  # nothing written yet
    clock.t += 1  # exactly SEEN_EVERY after the first sighting
    log.sync([sig()], evaluated=True, by="agent")
    assert stored(backend)["incidents"][0]["last_seen_at"] != first
    assert inc_mod.SEEN_EVERY == 900


def test_a_critical_failure_becomes_p1_from_its_third_occurrence():
    base = {"kind": "run_failed", "env": "prod", "criticality": "critical"}
    assert inc_mod.default_priority(base | {"occurrences": 2}) == "P2"
    assert inc_mod.default_priority(base | {"occurrences": 3}) == "P1"
    assert inc_mod.default_priority(base) == "P2"


def test_a_time_that_isnt_one_counts_as_the_epoch():
    assert inc_mod._epoch("not a time") == 0.0 and inc_mod._epoch(None) == 0.0
    assert inc_mod._epoch("1970-01-01T00:01:00Z") == 60.0


def test_events_keep_exactly_the_limit_with_the_first_always_kept():
    inc: dict = {}
    for n in range(inc_mod.KEEP_EVENTS):
        inc_mod._event(inc, "t", "me", "note", str(n))
    assert len(inc["events"]) == inc_mod.KEEP_EVENTS  # at the limit: nothing dropped
    inc_mod._event(inc, "t", "me", "note", "one more")
    texts = [e["text"] for e in inc["events"]]
    assert len(texts) == inc_mod.KEEP_EVENTS and texts[0] == "0" and texts[1] == "2" and texts[-1] == "one more"
    assert inc_mod.KEEP_EVENTS == 100


def _resolved(n, at):
    return {"id": f"inc-{n:012x}", "key": f"k{n}", "kind": "run_failed", "state": "resolved", "resolved_at": at,
            "opened_at": at, "title": "", "events": []}


def test_resolved_incidents_keep_exactly_the_newest_limit(backend):
    keep = inc_mod.KEEP_RESOLVED
    doc = {"contract": 1, "kind": "incidents", "env": "prod",
           "incidents": [_resolved(n, f"2026-01-01T00:00:{n % 60:02d}Z" if n else None) for n in range(keep + 1)]}
    backend.create(KEY, json.dumps(doc).encode())
    IncidentLog(backend, "prod", Clock()).report("Wrong numbers", "me")
    ids = {i["id"] for i in stored(backend)["incidents"] if i["state"] == "resolved"}
    assert len(ids) == keep and _resolved(0, None)["id"] not in ids  # the one with no time is the oldest
    assert keep == 200
    # At the limit, nothing is dropped.
    IncidentLog(backend, "prod", Clock()).report("More wrong numbers", "me")
    assert len([i for i in stored(backend)["incidents"] if i["state"] == "resolved"]) == keep


def test_the_list_sorts_resolved_incidents_without_a_time_last(backend):
    doc = {"contract": 1, "kind": "incidents", "env": "prod",
           "incidents": [_resolved(1, None), _resolved(2, "2026-01-01T00:00:00Z")]}
    backend.create(KEY, json.dumps(doc).encode())
    assert [i["id"] for i in IncidentLog(backend, "prod").list()] == [_resolved(2, "")["id"], _resolved(1, "")["id"]]


def test_a_lost_race_waits_longer_each_time_and_gives_up_after_eight_tries(backend, monkeypatch):
    log = IncidentLog(backend, "prod", Clock())
    log.sync([sig()], evaluated=True, by="agent")
    waits = []
    monkeypatch.setattr(backend, "replace", lambda key, data, etag: None)
    monkeypatch.setattr(inc_mod.time, "sleep", waits.append)
    with pytest.raises(Conflict):
        log.report("Wrong numbers", "me")
    assert waits == pytest.approx([0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4])
    assert inc_mod.ATTEMPTS == 8


def test_escalation_starts_at_exactly_its_time_and_says_how_long_in_minutes(backend):
    clock = Clock()
    log = IncidentLog(backend, "prod", clock)
    log.sync([sig()], evaluated=True, by="agent")
    assert log.escalate(1800, "on-call", "agent") == []  # first sight: age 0
    clock.t += 1799
    assert log.escalate(1800, "on-call", "agent") == []
    clock.t += 1
    [change] = log.escalate(1800, "on-call", "agent")
    assert change["incident"]["notes"][-1]["text"] == "Escalated to on-call: not acknowledged in 30 min"


class StoreClock:
    """A backend whose reads carry the store's clock, as OneLake's Date header does."""

    def __init__(self, inner, now):
        self.inner, self.now = inner, now

    def read(self, key):
        got = self.inner.read(key)
        return None if got is None else types.SimpleNamespace(data=got.data, etag=got.etag, store_now=self.now)

    def create(self, key, data):
        return self.inner.create(key, data)

    def replace(self, key, data, etag):
        return self.inner.replace(key, data, etag)


def test_escalation_goes_by_the_stores_clock_when_it_has_one(backend):
    store = StoreClock(backend, 1_000_000.0)
    clock = Clock()
    log = IncidentLog(store, "prod", clock)
    log.sync([sig()], evaluated=True, by="agent")
    assert stored(backend)["incidents"][0]["opened_store_at"] is None  # the first read found no list
    doc = stored(backend)
    doc["incidents"][0]["opened_store_at"] = inc_mod._iso(1_000_000.0 - 100)
    backend.replace(KEY, json.dumps(doc).encode(), backend.read(KEY).etag)
    clock.t += 10_000  # this PC's clock says it's been open for hours ...
    assert log.escalate(1800, "on-call", "agent") == []  # ... but the store says 100 s
    store.now += 1700
    assert len(log.escalate(1800, "on-call", "agent")) == 1  # 1,800 s on the store's clock


def test_notes_and_reports_are_cut_to_their_limits_and_wrong_moves_are_refused(backend):
    log = IncidentLog(backend, "prod", Clock())
    reported = log.report("t" * 250, "me", text="x" * 5000)
    assert len(reported["title"]) == 200 and len(reported["text"]) == 4000 and reported["occurrences"] == 1
    assert len(reported["events"][0]["text"]) == 200
    acked = log.acknowledge(reported["id"], "me", note="n" * 2500)
    assert len(acked["notes"][-1]["text"]) == 2000
    log.resolve(reported["id"], "me")
    with pytest.raises(ValueError, match="resolved already"):
        log.resolve(reported["id"], "me")
    with pytest.raises(ValueError, match="resolved already"):
        log.acknowledge(reported["id"], "me")
    with pytest.raises(ValueError, match="Priority is one of"):
        log.report("Wrong numbers", "me", priority="P9")


def test_an_incident_opened_before_the_store_had_a_clock_goes_by_this_pcs_clock(backend):
    store = StoreClock(backend, 1_000_000.0)
    log = IncidentLog(store, "prod", Clock())
    log.sync([sig()], evaluated=True, by="agent")  # no list yet, so no store time on it
    assert stored(backend)["incidents"][0]["opened_store_at"] is None
    assert log.escalate(1800, "on-call", "agent") == []  # just opened, by this PC's clock


def test_the_priority_of_a_resolved_incident_cant_change(backend):
    log = IncidentLog(backend, "prod", Clock())
    reported = log.report("Wrong numbers", "me")
    log.resolve(reported["id"], "me")
    with pytest.raises(ValueError, match="resolved already"):
        log.set_priority(reported["id"], "P1", "me", "worse than we thought")
