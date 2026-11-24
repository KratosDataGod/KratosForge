"""The warehouse live check (warehouse_livecheck.py), held to exact values: cases can't change, which case runs
outside a transaction, an error's words after "error:", the error limit while draining a batch, the run id, a run
with no connection writing its files where asked, the counts, and the summary's every line. The warehouse and
Fabric are the fakes of the main tests: nothing reaches a warehouse."""

from __future__ import annotations

import dataclasses
import json
import re

import pytest

from fabric_desktop import warehouse_livecheck as wl
from tests.test_warehouse_livecheck import Fabric


def test_a_case_cant_be_changed_and_only_scr_01_runs_outside_a_transaction():
    with pytest.raises(dataclasses.FrozenInstanceError):
        wl.CASES[0].query = "SELECT 2"
    assert [c.id for c in wl.CASES if c.autocommit] == ["SCR-01"]


def test_an_error_without_a_space_after_the_colon_keeps_its_first_letter():
    assert wl.same("error:Q", "error: something else") is False
    assert wl.same("error: something else", "error:Q") is False
    assert wl.same("error:Bad", "error: bad thing") is True


def test_draining_a_batch_stops_after_twenty_errors():
    class Failing:
        def nextset(self):
            raise RuntimeError("Msg 8134: Divide by zero error encountered.")
    errors = []
    wl._drain(Failing(), errors)
    assert len(errors) == 20 and errors[0] == "error: Msg 8134: Divide by zero error encountered."


def test_a_run_without_its_own_id_gets_twelve_hex_characters(tmp_path):
    check = wl.WarehouseCheck("ws1", "wh1", Fabric(), None, tmp_path)
    assert re.fullmatch(r"[0-9a-f]{12}", check.run_id) and check.scratch == f"fdlt_{check.run_id}"


def test_a_run_with_no_connection_still_writes_its_files_in_a_new_folder(tmp_path):
    out = tmp_path / "a" / "b"
    got = wl.WarehouseCheck("ws1", "wh1", Fabric(), None, out, run_id="abcdef12").run()
    assert got["outcome"] == "passed" and got["counts"] == {}
    text = (out / "warehouse-check.json").read_text(encoding="utf-8")
    assert text == json.dumps({k: v for k, v in got.items() if k != "files"}, indent=2)
    assert got["files"] == [str(out / "warehouse-check.json"), str(out / "warehouse-check.md")]


def test_counts_are_one_per_case_of_each_verdict(tmp_path):
    check = wl.WarehouseCheck("ws1", "wh1", Fabric(), None, tmp_path, run_id="abcdef12")
    check.refused, check.cleanup = [], ""

    def result(local, warehouse):
        return wl.Result("c", "WR-1", "s", "t", "unverified", None, local, warehouse)
    got = check._finish([result("1", "1"), result("1", "2"), result("1", "1")], dry=False)
    assert got["counts"] == {"local matches the warehouse": 2, "local differs": 1}
    assert got["outcome"] == "findings"


def _result(**kw):
    case = {"id": "DEC-01", "wr": "WR-1", "title": "a | b", "basis": "reference", "local": "L" * 250,
            "documented": "D" * 250, "warehouse": "W" * 250, "verdict": "local matches the warehouse"}
    return {"run_id": "r1", "outcome": "passed", "refused": [], "cleanup": "", "cases": [case]} | kw


HEAD = "| Case | What | Basis | Local | Documented | Warehouse | Verdict |\n| --- | --- | --- | --- | --- | --- | --- |\n"


def test_the_summary_writes_each_case_cut_to_its_limits():
    assert wl.summary(_result()) == (
        "# Warehouse live check r1\n\nOutcome: **passed**.\n\n" + HEAD
        + f"| DEC-01 (WR-1) | a \\| b | reference | {'L' * 199}… | {'D' * 199}… | {'W' * 199}… | "
        "local matches the warehouse |\n")


def test_the_summary_says_what_wasnt_documented_or_run_and_the_cleanup():
    case = _result()["cases"][0] | {"local": "1", "documented": None, "warehouse": None, "verdict": "not run"}
    text = wl.summary(_result(outcome="refused", refused=["not | a test warehouse"], cleanup="Dropped fdlt_x.",
                              cases=[case]))
    assert text == ("# Warehouse live check r1\n\nOutcome: **refused**.\n\nNot run:\n- not \\| a test warehouse\n\n"
                    "Dropped fdlt_x.\n\n" + HEAD
                    + "| DEC-01 (WR-1) | a \\| b | reference | 1 | not documented | - | not run |\n")


def test_an_empty_warehouse_answer_is_shown_as_it_is():
    case = _result()["cases"][0] | {"warehouse": "", "documented": "", "local": ""}
    assert wl.summary(_result(cases=[case])).endswith("| reference |  | not documented |  | local matches the warehouse |\n")
