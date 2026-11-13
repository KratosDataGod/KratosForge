"""Compare before promote, quarantine replay and Teams cards: the edges the main tests don't reach.
A target that can't be read, a deployment history that can't be read, quarantine replays with
nothing to replay, edits to columns that aren't there, long cards and Teams errors without JSON."""

from __future__ import annotations

import types

import pyarrow as pa
import pytest

from fabric_desktop import dataops_service
from tests.test_dataops import svc  # noqa: F401  (fixture)


def _broken(*a, **k):
    raise OSError("the target lakehouse can't be reached")


def test_promote_impact_says_when_the_target_cant_be_read(svc, monkeypatch):
    monkeypatch.setattr(svc, "_store", _broken)
    out = svc.meta_promote_impact("dev", "test")
    assert out == {"source": "dev", "target": "test", "tables": [], "problem": "the target lakehouse can't be reached"}


def test_promote_impact_without_the_deployment_history_still_gives_each_table_s_state(svc, monkeypatch):
    monkeypatch.setattr(svc, "_deployed_entities", _broken)
    out = svc.meta_promote_impact("dev", "dev")
    assert out["problem"] is None and out["removed"] == []
    assert {t["state"] for t in out["tables"]} == {"same"} and all(t["changes"] == [] for t in out["tables"])


def test_a_diff_against_an_environment_that_cant_be_read_says_so(svc, monkeypatch):
    monkeypatch.setattr(svc, "_deployed_entities", _broken)
    out = svc.meta_diff("test")
    assert out == {"against": "test", "entities": [], "problem": "the target lakehouse can't be reached"}


def test_nothing_to_replay_or_discard_where_nothing_is_quarantined(svc):
    with pytest.raises(LookupError, match="silver.customers has no quarantined rows in dev"):
        svc.meta_replay("silver.customers", "dev", ["x"])
    assert svc.meta_discard_quarantine("silver.customers", "dev", ["x"])["total"] == 0


def test_replaying_rows_no_longer_in_quarantine_says_to_refresh(svc):
    with pytest.raises(LookupError, match="no longer in quarantine"):
        svc.meta_replay("silver.orders", "dev", ["gone-id"])


def test_an_edit_to_a_column_the_row_doesnt_have_is_ignored(svc):
    row = svc.meta_quarantine("silver.orders")["rows"][0]
    res = svc.meta_replay("silver.orders", "dev", [row["_qid"]], {row["_qid"]: {"no_such_column": "1"}})
    assert res["status"] == "ok" and res["held"] == 1 and res["rows_written"] == 0


def test_rows_are_counted_from_the_files_when_a_writer_left_out_the_statistics():
    table = types.SimpleNamespace(
        get_add_actions=lambda flatten: pa.table({"path": ["a.parquet"], "num_records": pa.array([None], pa.int64())}),
        to_pyarrow_dataset=lambda: types.SimpleNamespace(count_rows=lambda: 42))
    assert dataops_service._rows_in(table) == 42


def test_a_card_with_many_lines_shows_twenty_and_says_how_many_more():
    card = dataops_service.teams_card("Late tables", [f"table {n}" for n in range(25)])
    body = card["attachments"][0]["content"]["body"]
    assert len(body) == 22 and body[-1]["text"] == "and 5 more in KratosForge"


@pytest.mark.parametrize("resp", [
    types.SimpleNamespace(json=lambda: (_ for _ in ()).throw(ValueError("not JSON"))),
    object(),  # no json() at all
])
def test_a_teams_answer_without_json_adds_nothing(resp):
    assert dataops_service._teams_error(resp) == ""


def test_teams_own_error_message_is_kept_on_one_line():
    assert dataops_service._teams_error(types.SimpleNamespace(json=lambda: {"error": {"message": "Bad\nflow"}})) \
        == ": Bad flow"
    assert dataops_service._teams_error(types.SimpleNamespace(json=lambda: {"message": "Throttled"})) == ": Throttled"


def test_drift_says_when_the_upstream_table_isnt_loaded_yet(svc, monkeypatch):
    from fabric_desktop.meta.store import LocalStore
    monkeypatch.setattr(LocalStore, "scan", lambda self, *a, **k: None)
    d = svc.meta_drift("silver.orders")
    assert d["problem"] == "bronze.orders hasn't been loaded in dev yet"


def test_drift_says_when_no_files_match_yet(svc, monkeypatch):
    from fabric_desktop.meta.store import LocalStore
    monkeypatch.setattr(LocalStore, "read_files", lambda self, *a, **k: (None, None))
    d = svc.meta_drift("bronze.orders")
    assert d["problem"] == "no files match landing/orders/*.csv yet"


def test_drift_says_when_the_source_table_doesnt_exist(svc, monkeypatch):
    project = svc._project()
    project.entities["bronze.orders"].spec["source"] = {"table": "sales.orders", "from": "SalesDB.sales.orders"}
    monkeypatch.setattr(svc, "_project", lambda: project)
    monkeypatch.setattr(dataops_service.Engine, "read_source_table", lambda self, table: None)
    d = svc.meta_drift("bronze.orders")
    assert d["problem"] == "the source table SalesDB.sales.orders doesn't exist"


def test_drift_when_the_environment_cant_be_opened_still_shows_the_contract(svc, monkeypatch):
    monkeypatch.setattr(svc, "_store", _broken)
    logged = []
    monkeypatch.setattr(dataops_service._log, "info", lambda *a: logged.append(a))
    d = svc.meta_drift("silver.orders", "dev")
    assert d["problem"] == "the target lakehouse can't be reached" and d["history"] == []
    assert logged and logged[0][0] == "No schema history for this entity: %s"
