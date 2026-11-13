"""A run stopped between its table commit and its run-log row (the app closed, the PC switched off)
is recorded by the next run of the entity, from the table's history, once that commit is old enough
that no run can still be on its way to writing its own row."""

import datetime as dt

import polars as pl
import pytest
from test_large_tables import DAY1, DAY2, DAY3, Setup, rows
from test_write_failures import failing

from fabric_desktop.meta import engine as engine_mod


def run_log(s: Setup) -> pl.DataFrame:
    return s.store.read("LH_Meta", "meta", "run_log").filter(pl.col("entity") == "bronze.events")


def stopped_after_commit(s: Setup) -> str:
    """Day 2's run commits its three rows and then records nothing, as a run killed at that point."""
    s.source(rows(range(3), DAY1), mode="overwrite")
    s.run(day=DAY1)
    s.source(rows([10, 11, 12], DAY2))
    real = failing(s.store, "run_log", None, OSError("killed"))
    run = s.engine(DAY2).run("bronze.events").run_id
    s.store.meta_append = real
    assert run not in run_log(s)["run_id"].to_list()
    return run


@pytest.mark.parametrize("merge", [True, False], ids=["merge", "rewrite"])
def test_the_next_run_records_a_run_that_stopped_after_its_commit(tmp_path, monkeypatch, merge):
    s = Setup(tmp_path, merge)
    stopped = stopped_after_commit(s)
    monkeypatch.setattr(engine_mod, "UNLOGGED_AFTER", dt.timedelta(0))
    res = s.run(day=DAY3)
    log = run_log(s)
    row = log.filter(pl.col("run_id") == stopped).row(0, named=True)
    assert (row["status"], row["rows_written"], row["load_date"]) == ("ok", 3, DAY2.isoformat())
    assert row["message"].startswith("Stopped after its commit (Delta version 1)") and res.run_id in row["message"]
    assert any(st.name == "record" and stopped in st.detail for st in res.steps)
    # Recorded once: the run after that finds the row and adds none.
    s.run(day=DAY3)
    assert log.height + 1 == run_log(s).height
    assert run_log(s).group_by("run_id").len()["len"].max() == 1


def test_a_recent_commit_is_left_to_its_own_run(tmp_path):
    s = Setup(tmp_path, True)
    stopped = stopped_after_commit(s)
    res = s.run(day=DAY3)  # its commit is seconds old: that run may still be writing its row
    assert stopped not in run_log(s)["run_id"].to_list()
    assert not any(st.name == "record" for st in res.steps)


def test_a_run_whose_row_was_saved_is_never_recorded_again(tmp_path, monkeypatch):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    s.run(day=DAY1)
    monkeypatch.setattr(engine_mod, "UNLOGGED_AFTER", dt.timedelta(0))
    s.source(rows([10], DAY2))
    s.run(day=DAY2)
    s.run(day=DAY3)
    assert run_log(s).group_by("run_id").len()["len"].max() == 1


def test_a_history_that_cant_be_read_never_fails_the_run(tmp_path, monkeypatch):
    s = Setup(tmp_path, True)
    stopped_after_commit(s)
    monkeypatch.setattr(engine_mod, "UNLOGGED_AFTER", dt.timedelta(0))

    def history(*a, **kw):
        raise OSError("OneLake unreachable")
    monkeypatch.setattr(s.store, "history", history)
    res = s.run(day=DAY3)
    assert res.status == "ok"


def _tampered(s: Setup, monkeypatch, **fields) -> None:
    """The table's history as if another writer (a notebook, say) had set these commit fields."""
    real = s.store.history

    def history(*a, **kw):
        commits = real(*a, **kw)
        for c in commits:
            if c.get("fabric_desktop_run_id"):
                c.update(fields)
        return commits
    monkeypatch.setattr(s.store, "history", history)


def test_a_commit_this_app_didnt_make_is_never_recorded_as_a_run(tmp_path, monkeypatch):
    s = Setup(tmp_path, True)
    stopped_after_commit(s)
    before = run_log(s).height
    monkeypatch.setattr(engine_mod, "UNLOGGED_AFTER", dt.timedelta(0))
    _tampered(s, monkeypatch, fabric_desktop_run_id="made-up")
    res = s.run(day=DAY3)
    assert run_log(s).height == before + 1  # only this run's own row
    assert "made-up" not in run_log(s)["run_id"].to_list()
    assert not any(st.name == "record" for st in res.steps)


def test_a_recorded_run_keeps_only_metadata_in_this_app_s_formats(tmp_path, monkeypatch):
    s = Setup(tmp_path, True)
    stopped = stopped_after_commit(s)
    monkeypatch.setattr(engine_mod, "UNLOGGED_AFTER", dt.timedelta(0))
    _tampered(s, monkeypatch, fabric_desktop_logic="x" * 500, fabric_desktop_metadata=["not", "text"])
    res = s.engine(DAY3).run("bronze.events")
    # With no logic of a known form on the last run, this run can't tell its logic is unchanged: it asks.
    assert res.status == "needs_decision"
    row = run_log(s).filter(pl.col("run_id") == stopped).row(0, named=True)
    assert (row["logic"], row["metadata"]) == ("", "")
