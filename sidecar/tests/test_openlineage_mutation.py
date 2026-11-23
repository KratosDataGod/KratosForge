"""Mutation tests for the OpenLineage export (meta/openlineage.py): which text loses its query, how own
columns trace back, the project's name, a run's time and which of two runs is last. Found by
tools/mutate.py."""

import types
from pathlib import Path

from fabric_desktop.meta import lineage, openlineage
from tests.test_openlineage import by_job, events, svc  # noqa: F401  (svc is a fixture)


def test_only_a_driver_url_loses_its_query_when_it_isnt_a_url():
    assert openlineage.safe_origin("landing/orders?v=1#top") == "landing/orders?v=1#top"
    assert openlineage.safe_origin("jdbc:oracle:thin:@//h:1521/svc?wallet_password=x") == "jdbc:oracle:thin:@//h:1521/svc"


def test_own_columns_trace_back_through_cycles_and_gaps_to_outside_inputs():
    cols = {"a": {"inputs": [{"entity": "e", "column": "b"}, {"entity": "e", "column": "gone"}]},
            "b": {"inputs": [{"entity": "e", "column": "a"}, {"entity": "src", "column": "x"}]}}
    assert openlineage._own_sources(cols, "e", "a", set()) == [{"entity": "src", "column": "x"}]
    assert openlineage._own_sources(cols, "e", "gone", set()) == []


def test_the_project_is_named_by_its_info_else_its_folder():
    named = types.SimpleNamespace(info={"name": "Sales"}, root=Path("/x/crm"))
    assert openlineage.Names(named, "dev", None).project_name == "Sales"
    assert openlineage.Names(types.SimpleNamespace(info=None, root=Path("/x/crm")), "dev", None).project_name == "crm"


def test_a_run_of_under_a_second_still_counts_its_time():
    run = {"started_at": "2026-11-14T10:00:00+00:00", "seconds": 0.5}
    assert openlineage._event_time(run) == "2026-11-14T10:00:00.500000+00:00"


def test_of_two_runs_that_started_together_the_later_one_listed_is_the_last(svc):  # noqa: F811
    at = "2026-11-14T10:00:00+00:00"
    runs = [{"run_id": "r1", "entity": "silver.orders", "status": "failed", "started_at": at},
            {"run_id": "r2", "entity": "silver.orders", "status": "ok", "started_at": at}]
    assert by_job(events(svc, runs))["silver.orders"]["eventType"] == "COMPLETE"


def test_a_column_copied_through_another_of_its_own_is_a_transformation_and_unknown_inputs_are_skipped(svc, monkeypatch):  # noqa: F811
    cols = {"id": {"how": "source", "inputs": [{"entity": "bronze.orders", "column": "id"}]},
            "copy": {"how": "source", "inputs": [{"entity": "silver.orders", "column": "id"}]},
            "odd": {"how": "source", "inputs": [{"entity": "nowhere.table", "column": "z"}]}}
    monkeypatch.setattr(lineage, "entity_columns", lambda project, entity: cols)
    fields = by_job(events(svc))["silver.orders"]["outputs"][0]["facets"]["columnLineage"]["fields"]
    assert fields["id"]["inputFields"][0]["transformations"][0]["subtype"] == "IDENTITY"
    assert fields["copy"]["inputFields"][0]["transformations"][0]["subtype"] == "TRANSFORMATION"
    assert "odd" not in fields
