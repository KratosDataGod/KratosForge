"""The optimization engine (OE-001): duplicates, repeated copies and full refreshes found with evidence,
impact that is measured or Unknown (never invented), totals that never count a workload twice, and a
service call that only reads Fabric."""

from __future__ import annotations

import base64
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

from fabric_desktop import audit, server
from fabric_desktop.opportunities import analyzers, assess, fixture, inventory, model
from fabric_desktop.service import Service

NOW = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
WS = {"id": "11111111-2222-3333-4444-555555555555", "displayName": "Sales", "capacityId": "cap-1"}
LH = "aaaaaaaa-0000-4000-8000-000000000001"


def _nb(cells: list[str], lakehouse: str | None = LH) -> dict:
    doc = {"cells": [{"cell_type": "code", "source": c} for c in cells] + [{"cell_type": "markdown", "source": "# notes"}],
           "metadata": {"dependencies": {"lakehouse": {"default_lakehouse": lakehouse}}}}
    payload = base64.b64encode(json.dumps(doc).encode()).decode()
    return {"definition": {"parts": [{"path": "notebook-content.ipynb", "payload": payload, "payloadType": "InlineBase64"}]}}


def _pl(name: str, activities: list[dict]) -> dict:
    payload = base64.b64encode(json.dumps({"name": name, "properties": {"activities": activities}}).encode()).decode()
    return {"definition": {"parts": [{"path": "pipeline-content.json", "payload": payload, "payloadType": "InlineBase64"}]}}


def _copy(name: str, query: str = "SELECT * FROM dbo.sales", table: str = "bronze_sales", action: str = "Append") -> dict:
    return {"name": name, "type": "Copy", "dependsOn": [], "typeProperties": {
        "source": {"type": "SqlServerSource", "sqlReaderQuery": query,
                   "datasetSettings": {"externalReferences": {"connection": "c1"}}},
        "sink": {"type": "LakehouseTableSink", "tableActionOption": action,
                 "datasetSettings": {"typeProperties": {"table": table}}}}}


def _daily(n: int, seconds: float, invoke: str = "Scheduled") -> list[dict]:
    out = []
    for d in range(1, n + 1):
        start = NOW - dt.timedelta(days=d)
        out.append({"invokeType": invoke, "status": "Completed", "startTimeUtc": start.isoformat(),
                    "endTimeUtc": (start + dt.timedelta(seconds=seconds)).isoformat()})
    return out


def _run(items: list[tuple[str, str, str, dict, list | None]], usage: list | None = None,
         price: dict | None = None) -> dict:
    src = {"workspace": WS, "items": [{"id": i, "type": t, "displayName": n} for i, t, n, _, _ in items],
           "definitions": {i: d for i, _, _, d, _ in items if d is not None},
           "jobs": {i: j for i, _, _, _, j in items}}
    return assess.assess([src], usage_imports=usage or [], price=price, now=NOW)


def _rules(result: dict) -> list[str]:
    return [o["rule"] for o in result["opportunities"]]


LOAD = ["# load\nimport x\n", "df = spark.read.table('bronze')\ndf.write.mode('overwrite').saveAsTable('silver')\n"]
LOAD_REFORMATTED = ["import x  # same, other comment\n",
                    'df = spark.read.table("bronze")\n\n# write it\ndf.write.mode("overwrite").saveAsTable("silver")\n']


# ---- acceptance 1: identical duplicate processing


def test_identical_code_with_other_comments_spacing_and_quotes_is_one_proven_duplicate():
    r = _run([("a", "Notebook", "nb_load", _nb(LOAD), _daily(30, 400)),
              ("b", "Notebook", "nb_load_copy", _nb(LOAD_REFORMATTED), _daily(30, 420))])
    dup = next(o for o in r["opportunities"] if o["rule"] == "OE-DUP-NB")
    assert dup["confidence"] == "proven" and dup["action"] == "eliminate"
    assert [a["role"] for a in dup["artifacts"]] == ["keep", "remove"]
    assert dup["affects"] == ["b"] or dup["affects"] == ["a"]
    kinds = {e["kind"] for e in dup["evidence"]}
    assert {"identical_code", "same_lakehouse", "run_history", "tables"} <= kinds


def test_names_alone_never_make_a_duplicate():
    r = _run([("a", "Notebook", "nb_load", _nb(LOAD), _daily(3, 10)),
              ("b", "Notebook", "nb_load", _nb(["print('other work')"]), _daily(3, 10))])
    assert "OE-DUP-NB" not in _rules(r)


def test_same_code_on_other_lakehouses_is_not_duplicate_processing():
    r = _run([("a", "Notebook", "eu", _nb(LOAD, "lh-eu"), _daily(30, 60)),
              ("b", "Notebook", "us", _nb(LOAD, "lh-us"), _daily(30, 60))])
    assert "OE-DUP-NB" not in _rules(r)
    param = next(o for o in r["opportunities"] if o["rule"] == "OE-DUP-NB-PARAM")
    assert param["affects"] == [] and param["counted"] is False and param["action"] == "consolidate"


def test_an_unknown_default_lakehouse_lowers_confidence_and_asks_for_a_check():
    r = _run([("a", "Notebook", "x", _nb(LOAD), _daily(5, 60)), ("b", "Notebook", "y", _nb(LOAD, None), _daily(5, 60))])
    dup = next(o for o in r["opportunities"] if o["rule"] == "OE-DUP-NB")
    assert dup["confidence"] == "high"
    assert "same lakehouse" in dup["validation"][0]


def test_python_is_compared_by_syntax_not_text():
    assert analyzers.code_fingerprint(["x = 1\n"]) != analyzers.code_fingerprint(["x = 2\n"])
    assert analyzers.code_fingerprint(['"""doc"""\nx = 1  # c\n']) == analyzers.code_fingerprint(["x=1"])
    assert analyzers.code_fingerprint(["# only a comment\n"]) is None


# ---- acceptance 2: redundant data movement


def test_two_pipelines_copying_the_same_source_into_the_same_sink():
    r = _run([("p1", "DataPipeline", "pl_a", _pl("pl_a", [_copy("Copy")]), _daily(30, 300)),
              ("p2", "DataPipeline", "pl_b", _pl("pl_b", [_copy("Copy again") | {"description": "x"}]), _daily(30, 300))])
    o = next(o for o in r["opportunities"] if o["rule"] == "OE-DUP-COPY")
    assert o["category"] == "redundant_data_movement" and o["confidence"] == "high"
    assert o["impact"]["basis"] == "whole workload"
    assert o["impact"]["run_seconds_per_month"]["classification"] == "estimated"


def test_the_same_source_into_different_sinks_is_repeated_extraction():
    r = _run([("p1", "DataPipeline", "pl_a", _pl("pl_a", [_copy("Copy", table="t1")]), _daily(3, 30)),
              ("p2", "DataPipeline", "pl_b", _pl("pl_b", [_copy("Copy", table="t2")]), _daily(3, 30))])
    o = next(o for o in r["opportunities"] if o["rule"] == "OE-REPEAT-EXTRACT")
    assert o["action"] == "consolidate" and o["confidence"] == "medium"


def test_a_copy_inside_a_larger_pipeline_has_an_unknown_share():
    big = [_copy("Copy"), {"name": "Wait", "type": "Wait", "dependsOn": [], "typeProperties": {"waitTimeInSeconds": 1}}]
    r = _run([("p1", "DataPipeline", "pl_a", _pl("pl_a", big), _daily(3, 300)),
              ("p2", "DataPipeline", "pl_b", _pl("pl_b", big), _daily(3, 300))])
    o = next(o for o in r["opportunities"] if o["rule"] == "OE-DUP-COPY")
    assert o["impact"]["basis"] == "part of the workload"
    assert o["impact"]["run_seconds_per_month"]["value"] is None


def test_expressions_in_the_source_lower_confidence():
    dyn = _copy("Copy", query="@pipeline().parameters.q")
    r = _run([("p1", "DataPipeline", "a", _pl("a", [dyn]), _daily(2, 5)), ("p2", "DataPipeline", "b", _pl("b", [dyn]), _daily(2, 5))])
    assert next(o for o in r["opportunities"] if o["rule"] == "OE-DUP-COPY")["confidence"] == "medium"


# ---- acceptance 3: unnecessary full refresh


def test_a_scheduled_whole_table_overwrite_is_a_full_refresh():
    r = _run([("a", "Notebook", "nb", _nb(LOAD), _daily(30, 60))])
    o = next(o for o in r["opportunities"] if o["rule"] == "OE-FULL-REFRESH")
    assert o["action"] == "optimize_in_place" and o["impact"]["basis"] == "part of the workload"
    assert o["impact"]["run_seconds_per_month"]["classification"] == "unknown"
    assert o["next_step"]["kind"] == "run_benchmark"


@pytest.mark.parametrize("cells,jobs", [
    (LOAD, _daily(30, 60, invoke="Manual")),  # never on a schedule
    (["df.write.mode('overwrite').option('replaceWhere', 'd > 1').saveAsTable('t')"], _daily(30, 60)),
    (["%%sql\nINSERT OVERWRITE TABLE t PARTITION (d='1') SELECT * FROM s"], _daily(30, 60)),
])
def test_no_full_refresh_when_unscheduled_or_selective(cells, jobs):
    assert "OE-FULL-REFRESH" not in _rules(_run([("a", "Notebook", "nb", _nb(cells), jobs)]))


@pytest.mark.parametrize("cell", ["%%sql\nINSERT OVERWRITE TABLE silver SELECT * FROM bronze",
                                  "spark.sql('CREATE OR REPLACE TABLE gold AS SELECT 1')"])
def test_sql_full_rewrites_are_found(cell):
    assert "OE-FULL-REFRESH" in _rules(_run([("a", "Notebook", "nb", _nb([cell]), _daily(5, 5))]))


def test_a_copy_that_overwrites_on_a_schedule_is_a_full_refresh():
    r = _run([("p", "DataPipeline", "pl", _pl("pl", [_copy("Copy", action="Overwrite")]), _daily(5, 5))])
    assert "OE-FULL-REFRESH" in _rules(r)


# ---- acceptance 10: no double counting


def test_overlapping_opportunities_count_once_per_workload():
    usage = [{"id": "u", "file_name": "m.csv", "rows": [
        {"key": "k1", "workspace": "Sales", "item": "nb_load_copy",
         "cu": {"value": 36000.0, "period": {"start": (NOW - dt.timedelta(days=30)).isoformat(), "end": NOW.isoformat()}}}]}]
    r = _run([("a", "Notebook", "nb_load", _nb(LOAD), _daily(30, 400)),
              ("b", "Notebook", "nb_load_copy", _nb(LOAD_REFORMATTED), _daily(30, 420))], usage=usage)
    dup = next(o for o in r["opportunities"] if o["rule"] == "OE-DUP-NB")
    refresh_b = next(o for o in r["opportunities"] if o["rule"] == "OE-FULL-REFRESH" and o["affects"] == dup["affects"])
    assert dup["counted"] and not refresh_b["counted"] and dup["id"] in refresh_b["overlaps"]
    # the copy's 30 x 420 s once, plus the kept notebook's full refresh as an Unknown part (not 2 x)
    t = r["totals"]["run_seconds_per_month"]
    assert t["value"] == pytest.approx(30 * 420) and t["unknown_parts"] == 1 and not t["complete"]
    assert r["totals"]["cu_per_month"]["value"] == pytest.approx(10.0)  # 36000 CU-s = 10 CU-h in 30 days


# ---- acceptance 12: CU is not money


def test_cu_value_is_never_called_a_realizable_saving():
    usage = [{"id": "u", "file_name": "m.csv", "rows": [
        {"key": "k", "workspace": "Sales", "item": "b",
         "cu": {"value": 7200.0, "period": {"start": (NOW - dt.timedelta(days=30)).isoformat(), "end": NOW.isoformat()}}}]}]
    r = _run([("a", "Notebook", "a", _nb(["x = 1"]), _daily(30, 10)), ("b", "Notebook", "b", _nb(["x = 1"]), _daily(30, 10))],
             usage=usage, price={"price_per_cu_hour": 0.5, "currency": "USD", "source": "test price"})
    dup = next(o for o in r["opportunities"] if o["rule"] == "OE-DUP-NB")
    value = dup["impact"]["cu_value_per_month"]
    assert value["value"] == pytest.approx(1.0) and value["metric"] == "cu_value"
    assert dup["impact"]["realizable"] is False and "capacity" in dup["impact"]["realizable_reason"]
    assert r["totals"]["realizable"] is False and r["totals"]["verified"]["value"] is None


def test_without_a_usage_import_cu_is_unknown_never_zero():
    r = _run([("a", "Notebook", "a", _nb(["x = 1"]), _daily(30, 10)), ("b", "Notebook", "b", _nb(["x = 1"]), _daily(30, 10))])
    cu = next(o for o in r["opportunities"] if o["rule"] == "OE-DUP-NB")["impact"]["cu_per_month"]
    assert cu["classification"] == "unknown" and cu["value"] is None and "Import" in cu["reason"]


# ---- acceptance 13, 14, 15: evidence, explanation, validation and rollback


def test_every_opportunity_is_complete():
    d = fixture.load()
    r = assess.assess([d["source"]], usage_imports=d["usage_imports"], price=d["price"], now=d["now"], fixture=True)
    assert r["opportunities"]
    for o in r["opportunities"]:
        assert model.problems(o) == [], o["rule"]
        assert o["validation"] and o["rollback"] and o["recommendation"] and o["alternatives"]


def test_ids_are_stable_across_runs():
    items = [("a", "Notebook", "a", _nb(LOAD), _daily(3, 1)), ("b", "Notebook", "b", _nb(LOAD), _daily(3, 1))]
    assert [o["id"] for o in _run(items)["opportunities"]] == [o["id"] for o in _run(items)["opportunities"]]


def test_unreadable_definitions_and_history_are_reported_not_guessed():
    r = _run([("a", "Notebook", "a", {"error": "Forbidden"}, None)])
    assert r["coverage"]["not_read"][0]["problem"] == "Forbidden" and r["coverage"]["history_not_read"] == 1
    assert r["opportunities"] == [] and r["next_action"]["kind"] == "fix_access"


def test_no_opportunity_says_so_without_claiming_there_is_none():
    r = _run([("a", "Notebook", "a", _nb(["x = 1"]), [])])
    assert r["next_action"]["kind"] == "none" and "isn't proof" in r["next_action"]["label"]
    assert r["totals"]["run_seconds_per_month"]["value"] == 0


def test_the_result_carries_no_code():
    r = _run([("a", "Notebook", "a", _nb(LOAD), _daily(3, 1))])
    assert all("cells" not in w and "activities" not in w for w in r["workloads"])


def test_fabric_py_notebook_format_is_read_too():
    text = ("# Fabric notebook source\n# METADATA ********************\n# META {\"dependencies\": {\"lakehouse\": "
            "{\"default_lakehouse\": \"lh1\"}}}\n# CELL ********************\nx = 1\n# MARKDOWN ********************\n"
            "# hi\n# CELL ********************\ny = 2\n")
    d = {"definition": {"parts": [{"path": "notebook-content.py", "payload": base64.b64encode(text.encode()).decode()}]}}
    cells, lh = inventory.notebook_content(d)
    assert lh == "lh1" and [c.strip() for c in cells] == ["x = 1", "y = 2"]


# ---- the demonstration fixture


def test_the_demo_is_labelled_a_fixture_everywhere():
    svc_result = _demo()
    assert svc_result["fixture"] is True and "fixture" in svc_result["fixture_note"].lower()
    assert all(o.get("fixture") for o in svc_result["opportunities"])
    assert "fixture" in svc_result["workspaces"][0]["name"]
    assert svc_result["next_action"]["opportunity_id"]
    assert {"OE-DUP-NB", "OE-DUP-COPY", "OE-FULL-REFRESH", "OE-DUP-NB-PARAM"} <= set(_rules(svc_result))


def _demo() -> dict:
    d = fixture.load()
    return assess.assess([d["source"]], usage_imports=d["usage_imports"], price=d["price"], now=d["now"], fixture=True)


# ---- acceptance 16 and 17: no AI, reads only


def test_the_engine_needs_no_ai_client():
    pkg = Path(fixture.__file__).parent
    text = "\n".join(p.read_text(encoding="utf-8") for p in pkg.glob("*.py"))
    for name in ("anthropic", "openai", "llm", "appstudio", "agent"):
        assert f"import {name}" not in text and f"from .{name}" not in text and f"from ..{name}" not in text
    assert not any(m.startswith(("anthropic", "openai")) for m in sys.modules if "opportunit" in m)


class FakeFabric:
    def __init__(self):
        self.calls: list[tuple] = []

    def get_workspace(self, ws):
        self.calls.append(("get_workspace", ws))
        return {"id": ws, "displayName": "Sales", "capacityId": "cap-1"}

    def list_workspaces(self):
        self.calls.append(("list_workspaces",))
        return [WS, {"id": "99999999-2222-3333-4444-555555555555", "displayName": "Other", "capacityId": "cap-2"}]

    def list_items(self, ws):
        self.calls.append(("list_items", ws))
        return [{"id": "a", "type": "Notebook", "displayName": "a"}, {"id": "b", "type": "Notebook", "displayName": "b"},
                {"id": "l", "type": "Lakehouse", "displayName": "lh"}]

    def get_item_definition(self, ws, item, fmt=None):
        self.calls.append(("get_item_definition", ws, item, fmt))
        if item == "b":
            raise RuntimeError("403 Forbidden token=abc.def.ghi")
        return _nb(LOAD)

    def list_job_instances(self, ws, item):
        self.calls.append(("list_job_instances", ws, item))
        return _daily(3, 10)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fake = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    s = Service(root=tmp_path / "app")
    s.fake = fake
    return s


def test_the_service_only_reads_fabric(svc):
    r = svc.optimization_assess(workspace_id=WS["id"])
    assert {c[0] for c in svc.fake.calls} <= {"get_workspace", "list_items", "get_item_definition", "list_job_instances"}
    assert ("get_item_definition", WS["id"], "a", "ipynb") in svc.fake.calls
    assert not any(c[0] == "get_item_definition" and c[2] == "l" for c in svc.fake.calls)  # lakehouses aren't read
    nr = r["coverage"]["not_read"]
    assert nr[0]["item_id"] == "b" and "abc.def.ghi" not in nr[0]["problem"]
    assert r["fixture"] is False and r["scope"]["kind"] == "workspace"
    assert "default list price" in r["price"]["source"]


def test_a_capacity_assesses_only_the_workspaces_on_it(svc):
    r = svc.optimization_assess(capacity_id="cccccccc-2222-3333-4444-555555555555")
    assert r["workspaces"] == [] and r["scope"]["kind"] == "capacity"
    svc.fake.list_workspaces = lambda: [WS | {"capacityId": "CCCCCCCC-2222-3333-4444-555555555555"}]
    r = svc.optimization_assess(capacity_id="cccccccc-2222-3333-4444-555555555555")
    assert [w["name"] for w in r["workspaces"]] == ["Sales"]


def test_the_demo_through_the_service_makes_no_fabric_call(svc):
    r = svc.optimization_assess(demo=True)
    assert r["fixture"] is True and svc.fake.calls == []


@pytest.mark.parametrize("kwargs", [{}, {"workspace_id": "nope"}, {"capacity_id": "nope"}])
def test_a_workspace_or_capacity_id_is_needed(svc, kwargs):
    with pytest.raises(ValueError):
        svc.optimization_assess(**kwargs)


def test_the_call_is_a_read_and_not_quick():
    assert "optimization_assess" in server.METHODS and "optimization_assess" in audit.READ_ONLY
    assert "optimization_assess" not in server.QUICK


def test_a_read_budget_stops_reads_and_says_so(svc, monkeypatch):
    from fabric_desktop import opportunities_service as ops
    monkeypatch.setattr(ops, "MAX_CALLS", 3)
    r = svc.optimization_assess(workspace_id=WS["id"])
    b = r["scope"]["budget"]
    assert b["reached"] is True and b["calls"] == 3
    assert len([c for c in svc.fake.calls if c[0] != "get_workspace"]) == 3
    assert "budget" in r["scope"]["note"]
    assert any("budget" in (n["problem"] or "") for n in r["coverage"]["not_read"]) or r["coverage"]["history_not_read"]


def test_a_huge_sql_cell_is_scanned_only_up_to_the_cap():
    cell = "%%sql\n" + "CREATE OR REPLACE TABLE t " * 50_000
    r = _run([("a", "Notebook", "nb", _nb([cell]), _daily(2, 1))])
    assert isinstance(r["opportunities"], list)
