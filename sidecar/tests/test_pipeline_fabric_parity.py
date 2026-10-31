"""ORCH-H3 (#1229): local pipeline runs against cases with a known Fabric outcome.

Each file in tests/pipelines/fabric_recorded/ is one case: a pipeline, its parameters and mocks,
its expected outcome, and where that outcome comes from. ``documented`` cases quote Microsoft's
documentation (today Data Factory's, whose semantics Fabric pipelines share but which no tenant
run has confirmed yet); ``recorded`` cases come from a run in a Fabric tenant (added only with
Chris's yes, since a run uses capacity). A mismatch names the case, the activity and the field.
No network, no Fabric."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from fabric_desktop.orchestration import loads
from fabric_desktop.orchestration.runner import FABRIC_STATUS, PipelineRun

CASES = Path(__file__).parent / "pipelines" / "fabric_recorded"
FILES = sorted(CASES.glob("*.json"))
STATUSES = ("Succeeded", "Failed", "Skipped", "Cancelled")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a parity case must not open network connections")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check_format(case: dict) -> list[str]:
    """Why a case file can't be trusted as a parity case; empty when it can."""
    problems = []
    source = case.get("source") or {}
    if source.get("kind") == "documented":
        if not str(source.get("url", "")).startswith("https://learn.microsoft.com/") or not source.get("quote"):
            problems.append("a documented case needs a learn.microsoft.com url and a quote")
    elif source.get("kind") == "recorded":
        if not source.get("run_id") or not source.get("date"):
            problems.append("a recorded case needs the Fabric run_id and the date it ran")
    else:
        problems.append("source.kind must be documented or recorded")
    expected = case.get("expected") or {}
    if expected.get("status") not in (None, *STATUSES):
        problems.append(f"expected.status must be one of {STATUSES} or null")
    acts = expected.get("activities")
    if not isinstance(acts, dict) or not acts:
        problems.append("expected.activities must name at least one activity")
    elif any(isinstance(v, str) and v not in STATUSES for v in acts.values()):
        problems.append(f"each expected activity status must be one of {STATUSES}")
    return problems


def compare(case: dict) -> list[str]:
    """Run the case on this PC and list every difference from the Fabric outcome."""
    r = PipelineRun(loads(json.dumps(case["pipeline"])), parameters=case.get("parameters") or {},
                    mocks=case.get("mocks") or None, wait_scale=0).run()
    record = r.to_json()
    name, out = case["case"], []
    want = case["expected"]
    if want.get("status") is not None and FABRIC_STATUS[r.state] != want["status"]:
        out.append(f"{name}: pipeline status is {FABRIC_STATUS[r.state]}, expected {want['status']}")
    rows = {a["name"]: a for a in record["activities"] if not a["label"]}
    for act, expected in want["activities"].items():
        row = rows.get(act)
        if row is None:
            out.append(f"{name}: activity {act} has no run record")
            continue
        fields = expected if isinstance(expected, dict) else {"status": expected}
        for field, value in fields.items():
            got = row.get(field)
            if isinstance(value, dict) and isinstance(got, dict):
                got = {k: got.get(k) for k in value}
            if got != value:
                out.append(f"{name}: activity {act} {field} is {got!r}, expected {value!r}")
    return out


def test_there_are_cases_and_names_match_files():
    assert len(FILES) >= 9
    for path in FILES:
        assert read(path)["case"] == path.stem, path.name


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_case_is_well_formed(path):
    assert check_format(read(path)) == []


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_local_run_matches_fabric(path):
    assert compare(read(path)) == []


def test_the_format_check_refuses_an_unsourced_or_unrecorded_case():
    good = read(FILES[0])
    assert check_format({**good, "source": {"kind": "documented", "url": "https://example.com", "quote": "x"}})
    assert check_format({**good, "source": {"kind": "recorded", "run_id": "", "date": "2026-10-31"}})
    assert check_format({**good, "source": {}})
    assert check_format({**good, "expected": {"status": "Done", "activities": {"A": "Succeeded"}}})


def test_a_mismatch_names_the_case_activity_and_field():
    case = read(CASES / "try_catch_previous_fails.json")
    wrong = {**case, "expected": {"status": "Failed", "activities": {"Catch": "Skipped", "Nope": "Succeeded"}}}
    found = compare(wrong)
    assert "try_catch_previous_fails: pipeline status is Succeeded, expected Failed" in found
    assert "try_catch_previous_fails: activity Catch status is 'Succeeded', expected 'Skipped'" in found
    assert "try_catch_previous_fails: activity Nope has no run record" in found
    # Fields other than status, such as the error a catch branch reads, compare only the keys given.
    err = {**case, "expected": {"status": None, "activities": {"Previous": {"error": {"errorCode": "E2"}}}}}
    assert compare(err) == ["try_catch_previous_fails: activity Previous error is {'errorCode': 'E1'}, "
                            "expected {'errorCode': 'E2'}"]
