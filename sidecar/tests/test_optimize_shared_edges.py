"""Optimize's shared tables: a recommendation with no kind, a table that isn't there yet, any other read
failure, and recommendations outside the workspaces asked for. The lakehouse is a local folder."""

import pytest

from fabric_desktop.optimize import schema
from tests.test_optimize_shared import analysis, finding, lake, pc  # noqa: F401  (lake is a fixture)


def test_a_recommendation_with_a_blank_kind_is_refused_before_anything_is_written(tmp_path, lake):  # noqa: F811
    me = pc(tmp_path, lake=lake)
    rec = {"workspace_id": "w1", "item_id": "i1", "rule_id": "small_files", "target": "dbo.sales", "kind": "  "}
    with pytest.raises(ValueError, match="needs a kind"):
        analysis(me, "r1", [finding()], [rec])
    assert len(lake.rows("fdopt_findings")) == 0


class _Missing(Exception):
    pass


class TableNotFoundError(Exception):
    pass


@pytest.mark.parametrize("error", [_Missing("Table fdopt_findings not found"), TableNotFoundError("gone")])
def test_a_table_that_is_not_there_yet_reads_as_empty_with_its_columns(tmp_path, lake, monkeypatch, error):  # noqa: F811
    me = pc(tmp_path, lake=lake)

    def missing(name):
        raise error

    monkeypatch.setattr(me.lake, "rows", missing)
    got = me.rows("findings")
    assert len(got) == 0 and got.schema == schema.expected()[schema.table("findings")]


def test_a_table_that_reads_as_nothing_is_empty_too(tmp_path, lake, monkeypatch):  # noqa: F811
    me = pc(tmp_path, lake=lake)
    monkeypatch.setattr(me.lake, "rows", lambda name: None)
    assert len(me.rows("findings")) == 0


def test_any_other_read_failure_is_raised(tmp_path, lake, monkeypatch):  # noqa: F811
    me = pc(tmp_path, lake=lake)

    def busy(name):
        raise OSError("the lakehouse is busy")

    monkeypatch.setattr(me.lake, "rows", busy)
    with pytest.raises(OSError, match="busy"):
        me.rows("findings")


def test_recommendations_only_in_other_workspaces_read_as_none(tmp_path, lake):  # noqa: F811
    me = pc(tmp_path, lake=lake)
    analysis(me, "r1", [finding(ws="hr", item="secret")],
             [{"workspace_id": "hr", "item_id": "secret", "rule_id": "small_files", "target": "dbo.sales",
               "kind": "optimize"}], finished_at="2026-10-18T10:01:00Z")
    assert me.recommendations({"w1"}) == []
    assert len(me.recommendations({"hr"})) == 1


def test_no_recommendations_at_all_read_as_none(tmp_path, lake):  # noqa: F811
    assert pc(tmp_path, lake=lake).recommendations() == []
