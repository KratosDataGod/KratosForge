"""Opportunity records (opportunities/model.py), the paths the other tests leave: a record with a status the
model doesn't know and one with no evidence."""

from __future__ import annotations

from fabric_desktop.opportunities import model as M


def _record(**changes):
    out = {k: "x" for k in M.REQUIRED} | {"status": "proposed", "evidence": [{"source": "run log"}]}
    return out | changes


def test_a_complete_record_has_no_problems():
    assert M.problems(_record()) == []


def test_an_unknown_status_and_missing_evidence_are_named():
    assert M.problems(_record(status="maybe", evidence=[])) == ["unknown status 'maybe'", "no evidence"]
    assert M.problems(_record(evidence=None)) == ["no evidence"]
