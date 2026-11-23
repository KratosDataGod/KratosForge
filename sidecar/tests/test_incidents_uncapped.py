"""Incident log edges an uncapped mutation run found unchecked (batch 253, docs/quality/mutation-testing.md):
the list's order, which record of a key counts when the document holds two, a resolve's note and its
length, a priority change on a record written before priorities existed, the stored document's layout,
and the minutes an escalation's timeline entry gives."""

import json

import pytest

from fabric_desktop.leases import LocalBackend
from fabric_desktop.workloads.incidents import KEY, IncidentLog


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def sig(evidence_id="r1"):
    return {
        "kind": "run_failed",
        "workload": "meta:sales:silver.orders",
        "name": "silver.orders",
        "title": "Run failed",
        "text": "boom",
        "evidence_id": evidence_id,
    }


def stored(backend):
    return json.loads(backend.read(KEY).data)


def rewrite(backend, change):
    doc = stored(backend)
    change(doc)
    backend.replace(KEY, json.dumps(doc).encode(), backend.read(KEY).etag)


@pytest.fixture
def backend(tmp_path):
    return LocalBackend(tmp_path / "control")


def test_active_incidents_are_listed_newest_first_then_the_resolved_ones(backend):
    clock = Clock()
    log = IncidentLog(backend, "prod", clock)
    first = log.report("First", "ana")
    clock.t += 60
    second = log.report("Second", "ana")
    clock.t += 60
    done = log.report("Done", "ana")
    log.resolve(done["id"], "ana")
    assert [i["id"] for i in log.list()] == [second["id"], first["id"], done["id"]]
    assert [i["id"] for i in log.active()] == [second["id"], first["id"]]


def test_of_two_records_for_one_problem_the_newest_counts_wherever_it_is_in_the_document(
    backend,
):
    clock = Clock()
    log = IncidentLog(backend, "prod", clock)
    [old] = log.sync([sig("r1")], evaluated=True, by="agent")
    log.resolve(old["incident"]["id"], "ana")
    clock.t += 3600
    [new] = log.sync([sig("r2")], evaluated=True, by="agent")
    assert new["change"] == "reopened"
    # The newer record first, the resolved one last.
    rewrite(backend, lambda doc: doc["incidents"].reverse())
    clock.t += 60
    # Still open: seen again, not reopened.
    assert log.sync([sig("r3")], evaluated=True, by="agent") == []
    assert len(stored(backend)["incidents"]) == 2


def test_a_resolve_keeps_its_note_and_one_without_a_note_adds_none(backend):
    log = IncidentLog(backend, "prod", Clock())
    a = log.report("A", "ana")
    b = log.report("B", "ana")
    assert [
        n["text"] for n in log.resolve(a["id"], "ana", "fixed the feed")["notes"]
    ] == ["fixed the feed"]
    assert log.resolve(b["id"], "ana")["notes"] == []


def test_a_priority_set_on_a_record_with_none_says_what_it_was_by_policy(backend):
    log = IncidentLog(backend, "prod", Clock())
    log.sync([sig()], evaluated=True, by="agent")

    def older(doc):  # written before priorities existed
        for key in ("priority", "priority_source"):
            doc["incidents"][0].pop(key, None)

    rewrite(backend, older)
    inc_id = stored(backend)["incidents"][0]["id"]
    inc = log.set_priority(inc_id, "P1", "ana", "the board meets at nine")
    assert inc["events"][-1]["text"] == "P3 → P1: the board meets at nine"


def test_the_document_is_written_indented_one_space_per_level(backend):
    IncidentLog(backend, "prod", Clock()).report("A", "ana")
    assert backend.read(KEY).data.startswith(
        b'{\n "contract": 1,\n "kind": "incidents"'
    )


def test_an_escalations_timeline_entry_says_the_minutes(backend):
    clock = Clock()
    log = IncidentLog(backend, "prod", clock)
    log.sync([sig()], evaluated=True, by="agent")
    clock.t += 900
    [change] = log.escalate(600, "on-call", "agent")
    assert (
        change["incident"]["events"][-1]["text"]
        == "To on-call: not acknowledged in 10 min"
    )


def test_a_resolve_note_is_kept_to_2000_characters(backend):
    log = IncidentLog(backend, "prod", Clock())
    a = log.report("A", "ana")
    [note] = log.resolve(a["id"], "ana", "x" * 2001)["notes"]
    assert len(note["text"]) == 2000
