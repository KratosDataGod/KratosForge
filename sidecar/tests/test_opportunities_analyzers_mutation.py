"""Mutation batch 25: the optimization analyzers (OE-001) word, order and score what they find
exactly. Which copy is kept, which run counts as running, the cell a full rewrite is in, and the
scan cap all decide what a person is told to remove."""

from __future__ import annotations

import base64
import datetime as dt
import json

import pytest

from fabric_desktop.opportunities import analyzers, inventory

NOW = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
WS = {"id": "11111111-2222-3333-4444-555555555555", "displayName": "Sales", "capacityId": "cap-1"}
LH = "aaaaaaaa-0000-4000-8000-000000000001"
LH2 = "aaaaaaaa-0000-4000-8000-000000000002"
CTX = analyzers.Context(usage={}, now=NOW)
LOAD = ["import x\n", "df = spark.read.table('bronze')\ndf.write.mode('overwrite').saveAsTable('silver')\n"]


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def _nb(cells: list[str], lakehouse: str | None = LH) -> dict:
    doc = {"cells": [{"cell_type": "code", "source": c} for c in cells],
           "metadata": {"dependencies": {"lakehouse": {"default_lakehouse": lakehouse}}}}
    payload = base64.b64encode(json.dumps(doc).encode()).decode()
    return {"definition": {"parts": [{"path": "notebook-content.ipynb", "payload": payload, "payloadType": "InlineBase64"}]}}


def _pl(name: str, activities: list[dict]) -> dict:
    payload = base64.b64encode(json.dumps({"name": name, "properties": {"activities": activities}}).encode()).decode()
    return {"definition": {"parts": [{"path": "pipeline-content.json", "payload": payload, "payloadType": "InlineBase64"}]}}


def _copy(name: str, query: str = "SELECT * FROM dbo.sales", table: str = "bronze_sales", action: str = "Append",
          **source_extra) -> dict:
    return {"name": name, "type": "Copy", "dependsOn": [], "typeProperties": {
        "source": {"type": "SqlServerSource", "sqlReaderQuery": query,
                   "datasetSettings": {"externalReferences": {"connection": "c1"}}, **source_extra},
        "sink": {"type": "LakehouseTableSink", "tableActionOption": action,
                 "datasetSettings": {"typeProperties": {"table": table}}}}}


def _wait(name: str) -> dict:
    return {"name": name, "type": "Wait", "dependsOn": [], "typeProperties": {"waitTimeInSeconds": 1}}


def _daily(n: int, seconds: float = 60, invoke: str = "Scheduled") -> list[dict]:
    out = []
    for d in range(1, n + 1):
        start = NOW - dt.timedelta(days=d)
        out.append({"invokeType": invoke, "status": "Completed", "startTimeUtc": start.isoformat(),
                    "endTimeUtc": (start + dt.timedelta(seconds=seconds)).isoformat()})
    return out


def _wl(items: list[tuple]) -> list[dict]:
    return inventory.workloads(WS, [{"id": i, "type": t, "displayName": n} for i, t, n, _, _ in items],
                               {i: d for i, _, _, d, _ in items}, {i: j for i, _, _, _, j in items}, now=NOW, days=30)


def _ev(opp: dict, kind: str) -> dict:
    return next(e for e in opp["evidence"] if e["kind"] == kind)


# ---- normalizing code


def test_other_cell_languages_are_kept_with_their_language_and_spacing_folded():
    assert analyzers.normalize_cell("%%spark\nval  x =   1\n") == "scala:val x = 1"


def test_python_that_doesnt_parse_is_compared_as_text_without_comments():
    assert analyzers.normalize_cell("def (:\n  # c\n x  y") == "text:def (: x y"


@pytest.mark.parametrize("source", ['"""only a docstring"""', "pass", "# only a comment\n"])
def test_a_cell_with_no_code_normalizes_to_nothing(source):
    assert analyzers.normalize_cell(source) == ""


# ---- run history text


def test_run_history_text_says_unread_or_counts_and_seconds():
    assert analyzers._runs_text({"runs": {"read": False}}) == "run history not read"
    read = {"read": True, "runs": 3, "days": 30, "scheduled": 2, "seconds_total": 1200.4}
    assert analyzers._runs_text({"runs": read}) == "3 run(s) in the last 30 days (2 scheduled), 1200 s in total"
    none = {"read": True, "runs": 0, "days": 30, "scheduled": 0, "seconds_total": 0.0}
    assert analyzers._runs_text({"runs": none}) == "0 run(s) in the last 30 days (0 scheduled)"


# ---- identical notebooks


def test_a_running_copy_names_the_kept_notebook_and_counts_both():
    ws = _wl([("a", "Notebook", "a", _nb(LOAD + ["# only a comment\n"]), _daily(3)),
              ("b", "Notebook", "b", _nb(LOAD), _daily(1))])
    [opp] = analyzers.duplicate_notebooks(ws, CTX)
    assert opp["title"] == "2 notebooks do the same work: keep a"
    assert opp["risk"] == "medium" and opp["confidence"] == "proven"
    assert opp["affects"] == ["b"]
    assert [(a["name"], a["role"]) for a in opp["artifacts"]] == [("a", "keep"), ("b", "remove")]
    assert _ev(opp, "identical_code")["detail"]["cells"] == 3  # the first notebook's cells, the comment-only one too
    assert len(opp["validation"]) == 2


def test_a_copy_read_with_no_runs_is_not_running():
    ws = _wl([("a", "Notebook", "a", _nb(LOAD), _daily(3)), ("b", "Notebook", "b", _nb(LOAD), [])])
    [opp] = analyzers.duplicate_notebooks(ws, CTX)
    assert opp["title"] == "Copies of a that don't run: b"
    assert opp["risk"] == "low" and opp["affects"] == []
    assert opp["impact"]["basis"] == "no runs to avoid"


def test_a_copy_with_unread_history_is_named_in_the_validation():
    ws = _wl([("a", "Notebook", "a", _nb(LOAD), _daily(3)), ("b", "Notebook", "b", _nb(LOAD), None)])
    [opp] = analyzers.duplicate_notebooks(ws, CTX)
    assert opp["confidence"] == "high"
    assert opp["validation"][-1] == "Read the run history of b; it couldn't be read."


def test_the_notebook_that_runs_most_is_kept_then_the_most_scheduled_then_by_name():
    ws = _wl([("a", "Notebook", "a", _nb(LOAD), _daily(1)), ("z", "Notebook", "z", _nb(LOAD), _daily(5, invoke="Manual"))])
    assert analyzers.duplicate_notebooks(ws, CTX)[0]["artifacts"][0]["name"] == "z"
    ws = _wl([("a", "Notebook", "a", _nb(LOAD), _daily(1, invoke="Manual") + _daily(1)),
              ("z", "Notebook", "z", _nb(LOAD), _daily(2))])
    assert analyzers.duplicate_notebooks(ws, CTX)[0]["artifacts"][0]["name"] == "z"


def test_unread_history_ranks_like_no_runs_and_the_name_decides():
    ws = _wl([("a", "Notebook", "a", _nb(LOAD), []), ("b", "Notebook", "b", _nb(LOAD), None)])
    assert analyzers.duplicate_notebooks(ws, CTX)[0]["artifacts"][0]["name"] == "a"


def test_one_run_or_one_scheduled_run_outranks_none():
    ws = _wl([("a", "Notebook", "a", _nb(LOAD), []), ("b", "Notebook", "b", _nb(LOAD), _daily(1, invoke="Manual"))])
    assert analyzers.duplicate_notebooks(ws, CTX)[0]["artifacts"][0]["name"] == "b"
    ws = _wl([("a", "Notebook", "a", _nb(LOAD), _daily(1, invoke="Manual")), ("b", "Notebook", "b", _nb(LOAD), _daily(1))])
    assert analyzers.duplicate_notebooks(ws, CTX)[0]["artifacts"][0]["name"] == "b"


def test_only_read_notebook_definitions_are_compared():
    ws = _wl([("a", "Notebook", "a", _nb(LOAD), _daily(1)), ("b", "Notebook", "b", _nb(LOAD), _daily(1))])
    for w in ws:
        w["definition"] = "not_read"
    assert analyzers.duplicate_notebooks(ws, CTX) == []


def test_the_tables_evidence_names_what_is_read_and_what_isnt_written():
    reads = ["df = spark.read.table('bronze')\ndisplay(df)\n"]
    ws = _wl([("a", "Notebook", "a", _nb(reads), []), ("b", "Notebook", "b", _nb(reads), [])])
    ev = _ev(analyzers.duplicate_notebooks(ws, CTX)[0], "tables")
    assert ev["text"] == "Reads dbo.bronze; writes no named table"
    assert ev["detail"] == {"reads": ["dbo.bronze"], "writes": []}
    writes = ["spark.range(3).write.saveAsTable('silver')\n"]
    ws = _wl([("a", "Notebook", "a", _nb(writes), []), ("b", "Notebook", "b", _nb(writes), [])])
    assert _ev(analyzers.duplicate_notebooks(ws, CTX)[0], "tables")["text"] == "Reads no named table; writes dbo.silver"


def test_the_fingerprint_shown_is_the_first_16_characters():
    ws = _wl([("a", "Notebook", "a", _nb(LOAD, LH), []), ("b", "Notebook", "b", _nb(LOAD, LH2), [])])
    [opp] = analyzers.duplicate_notebooks(ws, CTX)
    assert opp["rule"] == "OE-DUP-NB-PARAM"
    fp = _ev(opp, "identical_code")["detail"]["fingerprint"]
    assert fp == analyzers.code_fingerprint(LOAD)[:16] and len(fp) == 16


# ---- the same data copied twice


def _two(c1: dict, c2: dict, j1=None, j2=None) -> list[dict]:
    return _wl([("p1", "DataPipeline", "p1", _pl("p1", [c1]), j1 if j1 is not None else _daily(3)),
                ("p2", "DataPipeline", "p2", _pl("p2", [c2]), j2 if j2 is not None else _daily(1))])


def test_the_same_source_into_the_same_sink_is_one_opportunity_worded_for_it():
    [opp] = analyzers.repeated_copies(_two(_copy("c1"), _copy("c2")), CTX)
    assert opp["rule"] == "OE-DUP-COPY" and opp["action"] == "eliminate" and opp["effort"] == "low"
    assert opp["title"] == "The same data is copied into the same place by 2 pipelines"
    assert opp["problem"].startswith("The same rows are read from the source and written to the same table")
    assert _ev(opp, "same_source")["text"].endswith(" and write the same sink")
    assert opp["confidence"] == "high"
    assert len(opp["validation"]) == 1 and opp["validation"][0].startswith("Check the copies run at times")
    assert opp["affects"] == ["p2"]  # a single run is a run


def test_the_same_source_into_other_sinks_is_worded_as_repeated_extraction():
    [opp] = analyzers.repeated_copies(_two(_copy("c1", table="t1"), _copy("c2", table="t2")), CTX)
    assert opp["rule"] == "OE-REPEAT-EXTRACT" and opp["effort"] == "medium"
    assert opp["problem"] == "The source is read, and the data moved, once per consumer."
    assert _ev(opp, "same_source")["text"].endswith(", each into its own sink")
    assert opp["validation"][-1].startswith("Check each sink's consumers")


def test_an_expression_in_the_source_is_checked_first():
    q = "@pipeline().parameters.query"
    [opp] = analyzers.repeated_copies(_two(_copy("c1", query=q), _copy("c2", query=q)), CTX)
    assert opp["validation"][0].startswith("The source uses expressions (@...)")
    assert opp["validation"][1].startswith("Check the copies run at times")


def test_descriptions_and_annotations_dont_make_sources_differ_even_inside_lists():
    c1 = _copy("c1", description="mine", columns=[{"name": "id", "annotations": ["x"]}])
    c2 = _copy("c2", description="theirs", columns=[{"name": "id", "annotations": ["y"]}])
    assert [o["rule"] for o in analyzers.repeated_copies(_two(c1, c2), CTX)] == ["OE-DUP-COPY"]


def test_sources_that_differ_inside_a_list_are_not_the_same_source():
    c1, c2 = _copy("c1", columns=[{"name": "id"}]), _copy("c2", columns=[{"name": "other"}])
    assert analyzers.repeated_copies(_two(c1, c2), CTX) == []


def test_a_copy_with_no_sink_settings_is_left_out():
    ws = _two(_copy("c1"), _copy("c2"))
    for w in ws:
        w["activities"][0]["type_properties"].pop("sink")
    assert analyzers.repeated_copies(ws, CTX) == []


def test_only_read_pipeline_definitions_are_compared():
    ws = _two(_copy("c1"), _copy("c2"))
    for w in ws:
        w["definition"] = "not_read"
    assert analyzers.repeated_copies(ws, CTX) == []


def test_a_pipeline_read_with_no_runs_is_not_counted_as_removed():
    [opp] = analyzers.repeated_copies(_two(_copy("c1"), _copy("c2"), j2=[]), CTX)
    assert opp["affects"] == [] and opp["impact"]["basis"] == "no runs to avoid"


def test_a_copy_inside_a_larger_pipeline_makes_the_whole_saving_partial():
    ws = _wl([("p1", "DataPipeline", "p1", _pl("p1", [_copy("c1")]), _daily(5)),
              ("p2", "DataPipeline", "p2", _pl("p2", [_copy("c2")]), _daily(2)),
              ("p3", "DataPipeline", "p3", _pl("p3", [_copy("c3"), _wait("w")]), _daily(1))])
    [opp] = analyzers.repeated_copies(ws, CTX)
    assert sorted(opp["affects"]) == ["p2", "p3"]
    assert opp["impact"]["basis"] == "part of the workload"


# ---- full refreshes


def test_a_full_refresh_says_whether_it_is_a_notebook_or_a_pipeline():
    ws = _wl([("n", "Notebook", "n", _nb(LOAD), _daily(2)),
              ("p", "DataPipeline", "p", _pl("p", [_copy("c", action="Overwrite")]), _daily(2))])
    got = {o["artifacts"][0]["name"]: o["current"] for o in analyzers.full_refreshes(ws, CTX)}
    assert got["n"].startswith("The notebook ran 2 time(s) on a schedule in the last 30 days")
    assert got["p"].startswith("The pipeline ran 2 time(s) on a schedule in the last 30 days")


def test_sql_full_rewrites_are_numbered_from_the_first_cell():
    assert analyzers._sql_full(["%%sql\nINSERT OVERWRITE TABLE s SELECT 1"]) == [(1, "INSERT OVERWRITE TABLE s")]


def test_the_sql_scan_stops_exactly_at_the_cap():
    stmt = "CREATE OR REPLACE TABLE t AS SELECT"
    cap = 200_000  # written out: the test must not follow the constant it checks

    def cell(end: int) -> str:  # the statement's last letter at index ``end`` of the cell body
        return "%%sql\n" + "x" * (end - 1 - len(stmt)) + " " + stmt  # the body keeps the newline

    from fabric_desktop import magics
    assert len(magics.cell(cell(cap - 1)).body) == cap
    assert analyzers._sql_full([cell(cap - 1)]) == [(1, stmt)]
    assert analyzers._sql_full([cell(cap)]) == []
