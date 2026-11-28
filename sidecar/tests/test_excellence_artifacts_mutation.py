"""excellence/catalog_artifacts.py's notebook and pipeline controls, called directly (mutation testing, batch 375).

test_excellence_artifacts.py runs them through an assessment. These pin the exact wording: places and names (five
shown, the rest counted), singular and plural, missing counts, the 7-day VACUUM threshold, and the policy and
failure-path controls' "not inspected" answer when an older inspection didn't keep the setting.
"""

from __future__ import annotations

import pytest

from fabric_desktop.excellence import catalog_artifacts as ca, controls as ct

RID = "ev-1"


def ctx(payload) -> ct.Context:
    return ct.Context({"type": "item", "id": "i1"}, {ca.KIND: [{"id": RID, "payload": payload}]})


def where(n: int) -> list[dict]:
    return [{"cell": i, "line": i + 10} for i in range(1, n + 1)]


def pl(**kw) -> dict:
    base = {"kind": "pipeline", "activities": 4, "calls_out": 3, "state_unknown": [], "properties_unknown": [],
            "retry_unknown": [], "timeout_unknown": [], "depends_unknown": [], "failure_path": True}
    base.update(kw)
    return base


def test_the_vacuum_threshold_is_seven_days():
    assert ca.MAX_SAFE_RETENTION_DAYS == 7
    got = ca._short_vacuum(ctx({"rules": {}, "cells": 3}))
    assert got == {"outcome": "pass", "evidence_ids": [RID],
                   "text": "No VACUUM keeps less than 7 days of history (NB020, 3 code cells checked)."}
    got = ca._short_vacuum(ctx({"rules": {"NB020": {"count": 1, "where": where(1)}}}))
    assert got["text"] == "VACUUM keeps less than 7 days of history (NB020, 1 place: cell 1, line 11)."


def test_a_notebook_without_a_cell_count_says_none_were_checked():
    got = ca._secret(ctx({"rules": {}}))
    assert got["text"] == "No key, password or connection string found in the code (NB001, 0 code cells checked)."


def test_a_rule_hit_lists_five_places_and_counts_the_rest():
    got = ca._swallowed(ctx({"rules": {"NB012": {"count": 7, "where": where(7)}}}))
    assert got["text"] == ("An error is caught and ignored (NB012, 7 places: cell 1, line 11; cell 2, line 12; "
                           "cell 3, line 13; cell 4, line 14; cell 5, line 15 and 2 more).")
    assert got["detail"] == {"rule": "NB012", "count": 7, "where": where(7)}
    got = ca._swallowed(ctx({"rules": {"NB012": {"count": 6, "where": where(6)}}}))
    assert got["text"].endswith("cell 5, line 15 and 1 more).")
    got = ca._swallowed(ctx({"rules": {"NB012": {"count": 5, "where": where(5)}}}))
    assert got["text"].endswith("cell 5, line 15).")
    got = ca._unpinned(ctx({"rules": {"NB009": {"count": 2, "where": where(2)}}}))
    assert got["text"] == ("A package is installed without a version (NB009, 2 places: cell 1, line 11; "
                           "cell 2, line 12).")


def test_a_rule_hit_without_a_count_says_zero_places():
    got = ca._fixed_paths(ctx({"rules": {"NB002": {"where": where(1)}}}))
    assert got["text"] == "A OneLake path names a workspace or item id (NB002, 0 places: cell 1, line 11)."
    assert got["detail"]["count"] == 0


@pytest.mark.parametrize("names,count,text", [
    (["a"], 1, "a"), (["a", "b"], 2, "a, b"), ([f"a{i}" for i in range(5)], 5, "a0, a1, a2, a3, a4"),
    ([f"a{i}" for i in range(6)], 6, "a0, a1, a2, a3, a4 and 1 more"),
    ([f"a{i}" for i in range(7)], 7, "a0, a1, a2, a3, a4 and 2 more")])
def test_activity_names_show_five_and_count_the_rest(names, count, text):
    assert ca._names(names, count) == text


def test_deactivated_activities_wording():
    got = ca._deactivated(ctx(pl(inactive_count=1, inactive=["Copy"])))
    assert got["text"] == "1 activity is deactivated: Copy."
    assert got["detail"] == {"inactive": ["Copy"], "count": 1}
    got = ca._deactivated(ctx(pl(inactive_count=2, inactive=["A", "B"])))
    assert got["text"] == "2 activities are deactivated: A, B."
    assert got["detail"] == {"inactive": ["A", "B"], "count": 2}


def test_fixed_workspace_wording():
    got = ca._fixed_workspace(ctx(pl(fixed_workspace_count=1, fixed_workspace=["Nb"])))
    assert got["text"] == "1 activity names a fixed workspace id rather than a parameter: Nb."
    assert got["detail"] == {"activities": ["Nb"], "count": 1}
    got = ca._fixed_workspace(ctx(pl(fixed_workspace_count=2, fixed_workspace=["A", "B"])))
    assert got["text"] == "2 activities name a fixed workspace id rather than a parameter: A, B."


def test_retry_and_timeout_wording():
    got = ca._no_retry(ctx(pl(no_retry_count=1, no_retry=["Copy"])))
    assert got["text"] == "1 of its 3 activities that call out has no retry: Copy."
    got = ca._no_retry(ctx(pl(no_retry_count=2, no_retry=["A", "B"])))
    assert got["text"] == "2 of its 3 activities that call out have no retry: A, B."
    got = ca._no_timeout(ctx(pl(no_timeout_count=1, no_timeout=["Copy"])))
    assert got["text"] == "1 of its 3 activities that call out has no timeout under the 12-hour default: Copy."
    got = ca._no_timeout(ctx(pl(no_timeout_count=2, no_timeout=["A", "B"])))
    assert got["text"] == "2 of its 3 activities that call out have no timeout under the 12-hour default: A, B."


@pytest.mark.parametrize("unknown,text", [
    (["A"], "What this checks couldn't be read for 1 activity (A)."),
    (["A", "B"], "What this checks couldn't be read for 2 activities (A, B)."),
    ([f"a{i}" for i in range(6)], "What this checks couldn't be read for 6 activities (a0, a1, a2, a3, a4).")])
def test_activities_that_couldnt_be_read_are_named(unknown, text):
    got = ca._deactivated(ctx(pl(state_unknown=unknown)))
    assert got == {"outcome": "not_evaluated", "evidence_ids": [], "text": text,
                   "needs": "the activities' definitions"}


@pytest.mark.parametrize("evaluate,key,text", [
    (ca._no_retry, "retry_unknown", "The activities' retry settings weren't inspected."),
    (ca._no_timeout, "timeout_unknown", "The activities' timeout settings weren't inspected.")])
def test_an_inspection_that_didnt_keep_the_policy_is_not_evaluated(evaluate, key, text):
    p = pl()
    del p[key]
    assert evaluate(ctx(p)) == {"outcome": "not_evaluated", "evidence_ids": [], "text": text,
                                "needs": "the activities' policy"}
    assert evaluate(ctx(pl(calls_out="3")))["text"] == text


def test_an_inspection_that_didnt_keep_the_failure_path_is_not_evaluated():
    p = pl()
    del p["failure_path"]
    want = {"outcome": "not_evaluated", "evidence_ids": [], "needs": "the activities' dependsOn",
            "text": "The activities' dependencies weren't inspected."}
    assert ca._no_failure_path(ctx(p)) == want
    assert ca._no_failure_path(ctx(pl(calls_out=None))) == want
