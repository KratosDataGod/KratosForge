"""Mutation-testing gaps in validate.py, the notebook code checks (docs/quality/mutation-testing.md, batch 32).

Each test pins a behaviour that a surviving mutant changed while every existing test still passed.
The code checks only read cell text; nothing here starts a kernel or touches a lakehouse.
"""

from __future__ import annotations

import pytest

from fabric_desktop import validate


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def code(source, **meta):
    return {"type": "code", "source": source, "meta": meta}


def md(source):
    return {"type": "markdown", "source": source, "meta": {}}


def found(out, code_):
    return [f for f in out["findings"] if f["code"] == code_]


LH = {"name": "LH_Sales"}


# ---- finding()

def test_a_finding_takes_the_cell_id_of_its_cell():
    cells = [{"cell_id": "a"}, {"cell_id": "b"}]
    assert validate.finding("info", "x", "t", cell=2, cells=cells)["cell_id"] == "b"
    assert validate.finding("info", "x", "t", cell=None, cells=cells)["cell_id"] is None
    assert validate.finding("info", "x", "t", cell=1)["cell_id"] is None


# ---- code cells and an empty notebook

def test_markdown_and_blank_cells_are_not_code():
    assert [f["code"] for f in validate.check([md("# Title"), code("   ")], None)["findings"]] == ["empty"]
    assert found(validate.check([code("x = 1")], None), "empty") == []


# ---- where it can run

def test_the_missing_lakehouse_is_said_once_not_as_a_fabric_reason():
    out = validate.check([code('open(r"C:\\data\\x.csv").read()')], None)
    fabric = found(out, "runs_on_fabric")
    assert [(f["title"], f["cell"]) for f in fabric] == [("Cell 1 can't run on Fabric", 1)]
    assert fabric[0]["fix"] == "Set Runs on to the other place, or change the cell."


def test_a_reason_without_a_cell_is_about_the_notebook(monkeypatch):
    def fake(cells, lakehouse, load, resources=None):
        return {"local": {"reasons": [{"cell": None, "text": "needs something"}], "unknown": []},
                "fabric": {"reasons": [{"cell": None, "text": "needs a lakehouse"}], "unknown": []}}
    monkeypatch.setattr(validate, "compute_check", fake)
    out = validate.check([code("x = 1")], LH, runs_on="pc")
    (pc,) = found(out, "runs_on_pc")
    assert pc["title"] == "The notebook can't run on this PC" and pc["fix"] == "" and pc["cell"] is None
    assert pc["severity"] == "error" and pc["text"] == "It needs something."
    (fabric,) = found(out, "runs_on_fabric")  # shown: a lakehouse is attached
    assert fabric["title"] == "The notebook can't run on Fabric" and fabric["severity"] == "info"


# ---- packages

@pytest.mark.parametrize("runs_on, missing, unknown", [(None, "error", "warning"), ("pc", "error", "info"),
                                                        ("fabric", "info", "warning")])
def test_package_severity_follows_where_it_runs(runs_on, missing, unknown):
    out = validate.check([code("import xyzzy_not_here")], None, runs_on=runs_on)
    assert [f["severity"] for f in found(out, "package_missing_pc")] == [missing]
    (f,) = found(out, "package_unknown_fabric")
    assert f["severity"] == unknown
    assert f["text"] == "No Fabric run of this workspace has reported its version yet."


def test_an_installed_package_says_its_version_on_this_pc():
    (f,) = found(validate.check([code("import pandas as pd")], None), "package_unknown_fabric")
    assert f["text"].startswith("This PC has pandas ") and f["severity"] == "warning"


def test_an_import_needed_only_inside_a_function_is_not_a_finding():
    out = validate.check([code("def f():\n    import xyzzy_not_here\n    return 1")], None)
    assert found(out, "package_missing_pc") == [] and found(out, "package_unknown_fabric") == []


# ---- parameters, frozen cells, the lakehouse

def test_the_second_parameter_cell_is_the_one_to_fix():
    out = validate.check([code("x = 1", tags=["parameters"]), code("z = 0"), code("y = 2", tags=["parameters"])], None)
    (f,) = found(out, "parameters")
    assert f["cell"] == 3 and "Cells 1, 3" in f["text"]


def test_frozen_cells_are_listed():
    (f,) = found(validate.check([code("x = 1"), code("y = 2", frozen=True)], None), "frozen")
    assert f["title"] == "Frozen: cell 2" and f["cell"] == 2
    (f,) = found(validate.check([code("x = 1", frozen=True), code("y = 2", frozen=True)], None), "frozen")
    assert f["title"] == "Frozen: cells 1, 2" and f["cell"] == 1


def test_the_lakehouse_finding_names_it():
    (f,) = found(validate.check([code("x = 1")], LH), "lakehouse")
    assert f["title"] == "Lakehouse: LH_Sales"
    (f,) = found(validate.check([code("x = 1")], {"workspace_id": "w"}), "lakehouse")
    assert f["title"] == "Lakehouse: attached"


def test_reading_a_lakehouse_file_needs_a_lakehouse():
    out = validate.check([code('import pandas as pd\npd.read_csv("Files/x.csv")')], None)
    assert [f["severity"] for f in found(out, "no_lakehouse")] == ["error"]
    assert found(validate.check([code("x = 1")], None), "no_lakehouse") == []


# ---- the clean run's plan

def test_the_first_spark_cell_stops_a_run_without_spark():
    cells = [{**code("x = 1"), "cell_id": "c1"}, {**code('spark.sql("select 1")'), "cell_id": "c2"}]
    plan = validate.run_plan(cells, LH, spark=False)
    assert plan["ok"] is False
    assert (plan["blockers"][0]["cell"], plan["blockers"][0]["cell_id"]) == (2, "c2")


# ---- findings from a clean run

def test_a_run_failure_makes_the_matching_warning_an_error():
    static = [validate.finding("warning", "undefined", "Cell 1 uses a name no cell defines", "nmae isn't defined", cell=1)]
    run = {"failed_cell": 1, "error": {"ename": "NameError", "evalue": "name 'nmae' is not defined"}}
    assert validate.run_findings([code("print(nmae)")], run, static) == []
    assert static[0]["severity"] == "error" and static[0]["found_by"] == ["code", "run"]


# ---- helpers

def test_findings_without_a_cell_sort_first_within_a_severity():
    a = validate.finding("info", "a", "t", cell=1)
    b = validate.finding("info", "b", "t")
    assert [f["code"] for f in validate._sorted([a, b])] == ["b", "a"]


def test_names_lists_six_then_says_how_many_more():
    assert validate._names(list("abcdefg")) == "a, b, c, d, e, f and 1 more"
    assert validate._names(list("abcdef")) == "a, b, c, d, e, f"


def test_an_unknown_effect_keeps_the_text_before_its_semicolon():
    assert validate._effect("does a thing; and more; detail") == ("does a thing", "")


def test_the_report_shows_only_sections_with_findings():
    record = {"name": "nb", "verdict": "ready_with_warnings", "code": {"revision": "0123456789abcdefXYZ"},
              "findings": [validate.finding("warning", "w", "Watch out", "It might.", fix="Do this."),
                           validate.finding("warning", "v", "Also", "Text.")],
              "cells": [{"cell": 1, "status": "ok", "seconds": 1.5}, {"cell": 2, "status": "skipped", "seconds": None}]}
    text = validate.to_markdown(record)
    assert "- Code revision: 0123456789abcdef\n" in text
    assert "## Warnings" in text and "## Errors" not in text and "## Info" not in text
    assert "- **Watch out.** It might. Do this.\n" in text and "- **Also.** Text.\n" in text
    assert "| 1 | ok | 1.5 |" in text and "| 2 | skipped |  |" in text
