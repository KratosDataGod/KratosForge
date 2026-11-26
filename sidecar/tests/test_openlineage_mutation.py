"""openlineage's smaller rules, read directly (mutation testing, batch 330).

test_openlineage.py exports the sample project's runs; these pin what it doesn't reach: a plain source name
keeping a ? or #, the project's own name, a run of under a second, two runs with the same start, a column
traced through the entity's own columns, and an input whose entity isn't in the project.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop.meta import lineage, openlineage
from fabric_desktop.service import Service


@pytest.fixture
def project(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s._project()


def test_a_plain_source_name_keeps_its_question_mark_and_hash():
    assert openlineage.safe_origin("landing/orders#1?x") == "landing/orders#1?x"


def test_the_project_is_named_by_its_info_before_its_folder():
    named = SimpleNamespace(info={"name": "Sales"}, root=Path("crm"))
    assert openlineage.Names(named, "dev", None).job_namespace == "kratosforge://Sales"
    assert openlineage.Names(SimpleNamespace(info=None, root=Path("crm")), "dev", None).job_namespace == \
        "kratosforge://crm"


def test_a_run_of_under_a_second_ends_that_much_after_its_start():
    assert openlineage._event_time({"started_at": "2026-11-14T10:00:00", "seconds": 0.5}) == \
        "2026-11-14T10:00:00.500000+00:00"


def test_of_two_runs_with_the_same_start_the_one_listed_last_counts(project):
    runs = [{"run_id": "a", "entity": "silver.orders", "status": "ok", "started_at": "2026-11-14T09:00:00"},
            {"run_id": "b", "entity": "silver.orders", "status": "failed", "started_at": "2026-11-14T09:00:00"}]
    events = openlineage.run_events(project, runs, "dev")["events"]
    assert [e["eventType"] for e in events] == ["FAIL"]


def test_own_sources_stop_at_a_column_the_entity_doesnt_make():
    cols = {"a": {"inputs": [{"entity": "e", "column": "b"}, {"entity": "x", "column": "c"}]}}
    assert openlineage._own_sources(cols, "e", "a", set()) == [{"entity": "x", "column": "c"}]


RUN = {"run_id": "r1", "entity": "gold.fact_sales", "status": "ok", "started_at": "2026-11-14T10:00:00+00:00"}


def test_a_column_copied_from_one_the_entity_derives_is_a_transformation(project, monkeypatch):
    cols = {"a": {"how": "source", "inputs": [{"entity": "gold.fact_sales", "column": "b"}]},
            "b": {"how": "derived", "inputs": [{"entity": "silver.orders", "column": "x"}]}}
    monkeypatch.setattr(lineage, "entity_columns", lambda p, e: cols)
    event = openlineage.entity_event(project, openlineage.Names(project, "dev", None), "gold.fact_sales", RUN)
    fields = event["outputs"][0]["facets"]["columnLineage"]["fields"]
    assert fields["a"]["inputFields"][0]["transformations"][0]["subtype"] == "TRANSFORMATION"


def test_an_input_whose_entity_isnt_in_the_project_is_left_out(project, monkeypatch):
    cols = {"a": {"how": "source", "inputs": [{"entity": "silver.gone", "column": "c"},
                                              {"entity": "silver.orders", "column": "x"}]}}
    monkeypatch.setattr(lineage, "entity_columns", lambda p, e: cols)
    event = openlineage.entity_event(project, openlineage.Names(project, "dev", None), "gold.fact_sales", RUN)
    assert [i["name"] for i in event["inputs"]] == ["LH_Silver.dbo.orders"]
    assert len(event["outputs"][0]["facets"]["columnLineage"]["fields"]["a"]["inputFields"]) == 1
