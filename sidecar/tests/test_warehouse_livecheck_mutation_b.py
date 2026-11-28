"""The warehouse live check (warehouse_livecheck.py), mutation batch 402: what a run without a connection
writes, the counts and the summary's cells, the default drain limit, a case's defaults, and how two error
answers are compared. The connection is a fake: nothing reaches a warehouse."""

import dataclasses
import json

import pytest

from fabric_desktop import warehouse_livecheck as wl


class Cursor:
    def nextset(self):
        raise RuntimeError("Msg 8134: Divide by zero error encountered.")


def test_a_case_is_frozen_and_runs_in_a_transaction_unless_it_says_otherwise():
    case = wl.Case("c1", "WR-1", "s", "t", "SELECT 1", "1")
    assert case.autocommit is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        case.query = "SELECT 2"  # type: ignore[misc]


def test_draining_stops_after_twenty_errors_by_default():
    errors: list[str] = []
    wl._drain(Cursor(), errors)
    assert len(errors) == 20


def test_two_error_answers_compare_from_just_after_the_colon():
    assert wl.same("error:ab", "error:ab")
    assert not wl.same("error:ab", "error:xb")
    assert wl.same("error: divide by zero", "error: Msg 8134: Divide by zero error encountered.")


def test_a_run_without_its_own_id_gets_twelve_hex_characters():
    check = wl.WarehouseCheck("ws", "wh", None, None, "out")
    assert len(check.run_id) == 12 and int(check.run_id, 16) >= 0
    assert check.scratch == "fdlt_" + check.run_id


def test_a_refused_run_writes_its_results_and_counts_each_case_once(tmp_path):
    bad = wl.Case("BAD-01", "WR-1", "s", "t", "DROP TABLE dbo.customers", None)
    other = wl.Case("BAD-02", "WR-1", "s", "t", "DROP TABLE dbo.orders", None)
    out = tmp_path / "wh"
    out.mkdir()  # an earlier run's folder is reused
    check = wl.WarehouseCheck("ws", "wh", None, None, out, run_id="abcdef12", cases=(bad, other))
    result = check.run()
    assert result["outcome"] == "refused" and result["refused"]
    assert result["counts"] == {}
    assert (out / "warehouse-check.json").exists() and (out / "warehouse-check.md").exists()
    assert json.loads((out / "warehouse-check.json").read_text())["outcome"] == "refused"
    finished = check._finish([check._result(bad, {}), check._result(other, {})], dry=False)
    assert finished["counts"] == {"not run": 2}


def _result(**case):
    row = {"id": "DEC-01", "wr": "WR-206", "title": "a title", "basis": "reference", "local": "1",
           "documented": "1", "warehouse": "1", "verdict": "as documented"}
    row.update(case)
    return {"run_id": "r1", "outcome": "passed", "refused": [], "cleanup": "", "cases": [row]}


def test_the_summary_names_the_cleanup_only_when_there_is_one():
    assert "Dropped fdlt_r1." not in wl.summary(_result())
    lines = wl.summary({**_result(), "cleanup": "Dropped fdlt_r1."}).splitlines()
    assert lines[4:6] == ["Dropped fdlt_r1.", ""]


def test_the_summary_shows_each_answer_cut_to_200_characters():
    row = wl.summary(_result(local="1" * 300, documented="2" * 300, warehouse="3" * 300)).splitlines()[-1]
    cells = [c.strip() for c in row.split(" | ")]
    assert cells[3:6] == ["1" * 199 + "…", "2" * 199 + "…", "3" * 199 + "…"]


def test_the_summary_shows_the_documented_and_warehouse_answers_or_says_there_is_none():
    row = wl.summary(_result(documented="0.333333", warehouse="0.333333")).splitlines()[-1]
    assert "| 0.333333 | 0.333333 |" in row
    row = wl.summary(_result(documented=None, warehouse=None, verdict="not run")).splitlines()[-1]
    assert "| not documented | - | not run |" in row
