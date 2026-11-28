"""proof.py's check held to its exact rows: cells numbered from 1, the line and code each finding points at
(code of 80 characters kept, longer cut to 77 and "..."), markdown cells skipped, a %sql line that only
reads, which reasons from compute_check are said again, and when a notebook runs as Spark. Then the last
line of the Spark proof's first cell. It all reads code: nothing runs and nothing reaches Fabric."""

from __future__ import annotations

import pytest

from fabric_desktop import proof

LH = {"workspace_id": "w", "lakehouse_id": "l", "name": "LH"}
USES = "from fabric_desktop import lakehouse"


def cell(source, cell_id="c", kind="code"):
    return {"type": kind, "cell_id": cell_id, "source": source}


def test_a_finding_names_its_cell_from_1_its_line_and_its_code():
    out = proof.check([cell(f"{USES}\n\nimport requests\nrequests.get('x')", "a")], LH)
    assert out["warnings"] == [{"cell": 1, "cell_id": "a", "line": 4, "code": "requests.get('x')",
                                "text": "calls a network service; the proof can't contain or compare what that does"}]


def test_a_finding_on_the_line_after_a_leading_blank_line_is_on_line_2():
    out = proof.check([cell(USES, "a"), cell("\nrequests.get('x')", "b")], LH)
    (row,) = out["warnings"]
    assert (row["cell"], row["line"], row["code"]) == (2, 2, "requests.get('x')")


@pytest.mark.parametrize("width, code", [(80, None), (81, "cut")])
def test_code_of_80_characters_is_kept_and_longer_is_cut_to_77_and_dots(width, code):
    line = "requests.get('" + "a" * (width - 16) + "')"
    assert len(line) == width
    (row,) = proof.check([cell(f"{USES}\n{line}", "a")], LH)["warnings"]
    assert row["code"] == (line if code is None else line[:77] + "...")


def test_a_markdown_cell_is_never_read_as_code():
    out = proof.check([cell(USES, "a"), cell("requests.get('x')", "m", "markdown")], LH)
    assert out["warnings"] == [] and out["blockers"] == []


def test_a_sql_line_magic_that_only_reads_is_no_blocker_and_one_that_changes_names_its_line():
    assert proof.check([cell(f"{USES}\n%sql SELECT 1", "a")], LH)["blockers"] == []
    (row,) = proof.check([cell(USES, "a"), cell("x = 1\ny = 2\n%sql CREATE TABLE t AS SELECT 1\nz = 3\nw = 4", "b")], LH)["blockers"]
    assert (row["cell"], row["line"], row["code"]) == (2, 3, "%sql CREATE TABLE t AS SELECT 1")


def compat(local=(), fabric=()):
    return lambda *a, **k: {"local": {"reasons": list(local), "unknown": []},
                            "fabric": {"reasons": list(fabric), "unknown": []}}


def test_reasons_without_a_cell_are_said_again_unless_fabrics_is_the_missing_lakehouse(monkeypatch):
    reason = {"cell": None, "text": "needs a lakehouse"}
    monkeypatch.setattr(proof, "compute_check", compat(local=[reason], fabric=[reason]))
    texts = [b["text"] for b in proof.check([cell(USES)], None)["blockers"]]
    assert texts == [("No lakehouse is attached. A proof pins the attached lakehouse's tables and writes to its own "
                      "folder there."), "can't run on this PC: needs a lakehouse"]
    texts = [b["text"] for b in proof.check([cell(USES)], LH)["blockers"]]
    assert texts == ["can't run on this PC: needs a lakehouse", "can't run on Fabric: needs a lakehouse"]


def test_a_reason_in_a_cell_names_that_cell(monkeypatch):
    monkeypatch.setattr(proof, "compute_check", compat(fabric=[{"cell": 2, "text": "uses X"}]))
    out = proof.check([cell(USES, "a"), cell("x = 1", "b")], None)
    assert {"cell": 2, "cell_id": "b", "text": "can't run on Fabric: uses X"} in out["blockers"]


@pytest.mark.parametrize("cells, spark", [
    ([cell("%sql SELECT 1")], True),
    ([cell("%%sql\nSELECT 1")], True),
    ([cell("x = 1"), cell("%sql SELECT 1")], True),
    ([cell("%%sql\nSELECT 1", kind="markdown")], False),
    ([cell(f"{USES}\n%sql SELECT 1")], False),
    ([cell("x = 1")], False),
])
def test_a_notebook_runs_as_spark_when_it_has_sql_and_not_the_lakehouse_module(cells, spark):
    assert proof.runs_spark(cells) is spark


def test_a_spark_proof_reads_schemas_unless_told_not_to():
    first, _ = proof.fabric_cells({"id": "p"}, ["m"], dict(LH))
    assert first["source"].splitlines()[-1] == "_fd_spark = _fd_smod.bind(_fd_proof, spark, 'LH', True)"
    first, _ = proof.fabric_cells({"id": "p"}, ["m"], dict(LH, schemas=False))
    assert first["source"].splitlines()[-1] == "_fd_spark = _fd_smod.bind(_fd_proof, spark, 'LH', False)"
