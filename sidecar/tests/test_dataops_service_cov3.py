"""dataops_service.py: a drift check with no source or a source table, a replay that fails its checks again,
and Data health's refusals without a project or with a wrong environment. Fakes only."""

import datetime as dt
from types import SimpleNamespace

import polars as pl
import pytest

from fabric_desktop import dataops_service as ds
from fabric_desktop.service import Service


@pytest.fixture
def bare(tmp_path):
    return Service(root=tmp_path / "app")


def test_a_time_that_cant_be_read_is_the_earliest():
    assert ds._dh_time("not a time") == dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    assert ds._dh_time("2026-11-01T00:00:00").tzinfo is dt.timezone.utc


def _drift(bare, monkeypatch, source):
    entity = SimpleNamespace(spec={"source": source}, layer="bronze")
    monkeypatch.setattr(bare, "_project", lambda: SimpleNamespace(entities={"bronze.t": entity}))

    def no_history(table, cond):
        raise OSError("no schema history")
    monkeypatch.setattr(bare, "_store", lambda project, env: SimpleNamespace(meta_where=no_history))
    monkeypatch.setattr(ds.drift_mod, "detect", lambda project, e, src: {"source_columns": src})


def test_drift_without_a_source_to_read_compares_nothing(bare, monkeypatch):
    _drift(bare, monkeypatch, {"custom": "step.py"})
    out = bare.meta_drift("bronze.t")
    assert out == {"source_columns": None, "env": "dev", "problem": None, "history": []}


def test_drift_reads_a_source_table(bare, monkeypatch):
    _drift(bare, monkeypatch, {"table": "dbo.customers"})
    frame = pl.DataFrame({"id": [1]})

    class Engine:
        def __init__(self, project, store, source_options=None):
            pass

        def read_source_table(self, table):
            assert table == "dbo.customers"
            return frame
    monkeypatch.setattr(ds, "Engine", Engine)
    out = bare.meta_drift("bronze.t")
    assert out["problem"] is None and out["source_columns"] is frame


def test_a_replay_that_fails_its_checks_again_keeps_the_rows_quarantined(bare, monkeypatch):
    q = pl.DataFrame({"_qid": ["q1"], "_check": ["not null"], "id": [None]}, schema_overrides={"id": pl.String})
    monkeypatch.setattr(bare, "_project", lambda: SimpleNamespace())
    monkeypatch.setattr(bare, "_quarantine_table", lambda project, name, env: ("store", "LH", "t", q))
    monkeypatch.setattr(bare, "_table_lock", lambda project, env, store: None)
    monkeypatch.setattr(bare, "_drop_from_quarantine", lambda *a: pytest.fail("rows left quarantine"))
    monkeypatch.setattr(ds, "impact", lambda project, name: ["gold.t"])

    class Engine:
        def __init__(self, project, store, table_lock=None):
            pass

        def replay(self, name, picked):
            return SimpleNamespace(status="failed", to_dict=lambda: {"status": "failed"}), None
    monkeypatch.setattr(ds, "Engine", Engine)
    assert bare.meta_replay("silver.t", "dev", ["q1"]) == {"status": "failed", "held": 0, "downstream": ["gold.t"]}


def test_data_health_refuses_an_unknown_environment(bare):
    with pytest.raises(ValueError, match="env is one of dev, test, prod"):
        bare.meta_data_health_list("qa")
    with pytest.raises(ValueError, match="env is one of"):
        bare.meta_data_health_watch("silver.t", "qa")
    with pytest.raises(ValueError, match="env is one of"):
        bare.meta_data_health_quiet("silver.t", "qa", "late", 1)


def test_data_health_without_a_project_lists_nothing(bare):
    out = bare.meta_data_health_list("dev")
    assert out["no_project"] is True and out["tables"] == [] and out["entities"] == []
    assert out["rule"] == ds.data_health_mod.rule()
