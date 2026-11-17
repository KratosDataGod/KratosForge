"""Databricks discovery, edges the mutation run (batch 82) found unchecked: where a name or an error
is cut, which compute is suggested first, and what the summary says. Against the fake workspace in
test_databricks_discovery.py; nothing reaches a workspace."""

from __future__ import annotations

import pytest

import test_databricks_discovery as t
from test_databricks_discovery import app_python  # noqa: F401 - the app's Python 3.12, as there
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_discovery as disc
from fabric_desktop.engines import databricks_sql as dbsql


def test_a_name_of_exactly_the_limit_is_kept_and_one_past_it_is_cut_to_the_limit():
    assert disc._TEXT_MAX == 200
    exact = "a" * 200
    assert disc._text(exact) == exact
    cut = disc._text("b" * 201)
    assert len(cut) == 200 and cut == "b" * 199 + "…"
    assert disc._text("abcdef", limit=4) == "abc…"
    assert disc._text("ab  cdef", limit=4) == "ab…", "the cut never leaves a space before the …"


def test_a_parts_error_is_cut_at_600_characters_in_the_summary():
    assert disc._ERROR_MAX == 600
    result = t.discover(t.FakeWorkspace())
    result["parts"]["clusters"] = {"ok": False, "error": "x" * 600}
    line = next(x for x in disc.summary_lines(result) if x.startswith("Clusters: "))
    assert line == "Clusters: couldn't read: " + "x" * 600
    result["parts"]["clusters"] = {"ok": False, "error": "y" * 601}
    line = next(x for x in disc.summary_lines(result) if x.startswith("Clusters: "))
    assert line == "Clusters: couldn't read: " + "y" * 599 + "…"


def test_a_cluster_with_auto_termination_has_no_note_and_one_without_has_one():
    on = disc.cluster_row({"cluster_id": "a", "spark_version": "16.4.x-scala2.12",
                           "data_security_mode": "SINGLE_USER", "autotermination_minutes": 30})
    off = disc.cluster_row({"cluster_id": "b", "spark_version": "16.4.x-scala2.12",
                            "data_security_mode": "SINGLE_USER", "autotermination_minutes": 0})
    assert on["notes"] == [] and on["auto_stop_mins"] == 30
    assert off["notes"] == ["No auto-termination: once started, it bills DBUs until someone stops it."]


def test_an_access_mode_that_isnt_set_is_named_not_set():
    problems = disc.connect_problems("16.4.x-scala2.12", "")
    assert any(p.startswith("Its access mode (not set) has no Unity Catalog") for p in problems)
    problems = disc.connect_problems("16.4.x-scala2.12", "NONE")
    assert any(p.startswith("Its access mode (NONE) has no Unity Catalog") for p in problems)


def test_after_a_failed_sign_in_every_part_is_not_ok():
    def fail():
        raise dba.DatabricksAuthError("Sign in first.")

    result = disc.Discovery(dbsql.Client(dba.TokenProvider(t.HOST, fail, "test"), t.FakeWorkspace())).run()
    assert result["parts"]["identity"] == {"ok": False, "error": "Sign in first."}
    for name in disc.PARTS[1:]:
        assert result["parts"][name] == {"ok": False, "skipped": True, "error": "Sign in first."}


def _wh(i, state):
    return {"id": i, "state": state, "auto_stop_mins": 10}


def _cl(i, state):
    return {"id": i, "state": state, "auto_stop_mins": 10, "connect": {"usable": True, "problems": []}}


@pytest.mark.parametrize("states, want", [
    (["STARTING", "RUNNING"], "b"),
    (["STOPPED", "STARTING"], "b"),
    (["STARTING", "STOPPED"], "a"),
    (["STOPPED", "RUNNING"], "b"),
])
def test_running_comes_before_starting_and_starting_before_anything_else(states, want):
    parts = {"warehouses": {"value": [_wh(i, s) for i, s in zip("ab", states, strict=True)]},
             "clusters": {"value": [_cl(i, s) for i, s in zip("ab", states, strict=True)]}}
    assert disc.suggested(parts) == {"warehouse_id": want, "cluster_id": want}


def _summary_with_catalogs(n):
    result = t.discover(t.FakeWorkspace())
    result["parts"]["catalogs"] = {"ok": True, "value": [{"name": f"c{i}"} for i in range(n)]}
    return next(x for x in disc.summary_lines(result) if x.startswith("Catalogs ("))


def test_the_summary_lists_20_catalogs_and_marks_more_with_an_ellipsis():
    assert _summary_with_catalogs(20) == "Catalogs (20): " + ", ".join(f"c{i}" for i in range(20))
    assert _summary_with_catalogs(21) == "Catalogs (21): " + ", ".join(f"c{i}" for i in range(20)) + " …"
    assert _summary_with_catalogs(19) == "Catalogs (19): " + ", ".join(f"c{i}" for i in range(19))


def test_the_summary_says_which_clusters_databricks_connect_can_use():
    lines = disc.summary_lines(t.discover(t.FakeWorkspace()))
    ok = next(x for x in lines if x.startswith("  dev [c-ok]"))
    assert ok == "  dev [c-ok] TERMINATED, 16.4.x-scala2.12, Databricks Connect can use it"
    old = next(x for x in lines if x.startswith("  old [c-old]"))
    assert old.startswith("  old [c-old] RUNNING, 14.3.x-scala2.12, Databricks Connect can't: ")


def _try_first(pick):
    result = t.discover(t.FakeWorkspace())
    result["suggested"] = pick
    return [x for x in disc.summary_lines(result) if x.startswith("Try first")]


def test_try_first_names_whatever_was_suggested_and_is_left_out_when_nothing_was():
    assert _try_first({"warehouse_id": "w1", "cluster_id": ""}) == ["Try first: warehouse w1"]
    assert _try_first({"warehouse_id": "", "cluster_id": "c1"}) == ["Try first: cluster c1"]
    assert _try_first({"warehouse_id": "w1", "cluster_id": "c1"}) == ["Try first: warehouse w1, cluster c1"]
    assert _try_first({"warehouse_id": "", "cluster_id": ""}) == []
