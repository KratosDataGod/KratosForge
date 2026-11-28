"""sameSemantics, Observation and spark.streams on this PC (spark_df_extras.py): the refusals and fallbacks
the notebook tests don't reach. Called directly, with stand-ins where a whole DataFrame isn't needed."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import spark_df_extras as ex
from fabric_desktop import sql


def test_a_plan_sqlglot_cant_write_back_compares_by_its_text(monkeypatch):
    def refuse(tree):
        raise ValueError("can't write it whole")

    monkeypatch.setattr(sql, "duckdb_sql", refuse)
    df = SimpleNamespace(relation=SimpleNamespace(sql_query=lambda: "SELECT 1 AS a"))
    assert ex._canonical_plan(df) == "SELECT 1 AS a"


@pytest.mark.parametrize(("name", "error", "match"), [(3, TypeError, "should be a str, got int"),
                                                      ("", ValueError, "should not be empty")])
def test_an_observation_needs_a_name_that_is_text(name, error, match):
    with pytest.raises(error, match=match):
        ex.Observation(name)


def test_observe_with_neither_an_observation_nor_a_name_is_refused():
    from duckdb.experimental.spark.sql import functions as F
    with pytest.raises(TypeError, match="needs an Observation or a name, got int"):
        ex.observe(SimpleNamespace(), 7, F.lit(1))


def test_a_listener_defined_here_has_spark_s_empty_hooks():
    listener = ex.StreamingQueryListener()
    for hook in (listener.onQueryStarted, listener.onQueryProgress, listener.onQueryIdle, listener.onQueryTerminated):
        assert hook(object()) is None


def test_spark_streams_answers_dunder_lookups_as_missing():
    streams = ex._Streams()
    with pytest.raises(AttributeError):
        streams.__wrapped__  # noqa: B018 - copy and inspect ask for these
    with pytest.raises(NotImplementedError, match="spark.streams.active runs in Fabric"):
        streams.active  # noqa: B018
