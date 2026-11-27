"""OpenLineage run events (meta/openlineage.py): what mutation testing (docs/quality/mutation-testing.md)
showed no test checked. Each test works on the sample project in its own folder; nothing runs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop.meta import openlineage
from fabric_desktop.meta.templates import init_project

RUN = {"run_id": "r1", "status": "ok", "started_at": "2026-11-14T10:00:00+00:00", "seconds": 2}


@pytest.fixture
def proj(tmp_path):
    return init_project(tmp_path / "crm", "3", "Chris", True)


def event(proj, entity, run=RUN):
    return openlineage.entity_event(proj, openlineage.Names(proj, "dev", None), entity, run)


def test_the_project_is_named_by_its_info_and_by_its_folder_only_without_one():
    named = SimpleNamespace(info={"name": "sales"}, root=Path("crm"))
    assert openlineage.Names(named, "dev", None).job_namespace == "kratosforge://sales"
    for info in ({}, {"name": ""}, None, "sales"):
        unnamed = SimpleNamespace(info=info, root=Path("crm"))
        assert openlineage.Names(unnamed, "dev", None).job_namespace == "kratosforge://crm", info


def test_a_run_shorter_than_a_second_still_ends_after_it_starts():
    run = {"started_at": "2026-11-14T10:00:00+00:00", "seconds": 0.5}
    assert openlineage._event_time(run) == "2026-11-14T10:00:00.500000+00:00"
    assert openlineage._event_time({**run, "seconds": 0}) == "2026-11-14T10:00:00+00:00"


def test_a_name_with_a_hash_or_question_mark_that_isnt_a_driver_url_is_kept_whole():
    assert openlineage.safe_origin("orders#1.csv") == "orders#1.csv"
    assert openlineage.safe_origin("landing/what?.csv") == "landing/what?.csv"


def test_inputs_from_an_entity_the_project_doesnt_have_are_left_out(proj):
    proj.entities["silver.orders"].spec["source"] = {"entity": "bronze.gone"}
    ev = event(proj, "silver.orders")
    assert ev["inputs"] == []
    assert "columnLineage" not in ev["outputs"][0]["facets"]


def test_two_runs_alike_but_for_the_type_of_their_seconds_export_the_same_event_in_either_order(proj):
    a = {**RUN, "entity": "silver.orders", "seconds": 1}
    b = {**RUN, "entity": "silver.orders", "seconds": "1"}
    first = openlineage.run_events(proj, [a, b], "dev")["events"]
    second = openlineage.run_events(proj, [b, a], "dev")["events"]
    assert first == second and first[0]["eventTime"] == "2026-11-14T10:00:01+00:00"
