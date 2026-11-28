"""Mutation batch 261: excellence/catalog_artifacts.py (docs/quality/mutation-testing.md). The survivors
that matter most were in FEA-REL-004, 005 and 006: an inspection made before the retry, timeout or
dependency settings were kept must leave the control not evaluated, and with ``and`` for ``or`` it
passed a pipeline nobody had checked (``_callouts``) or broke (``_no_failure_path``). The rest were the
wording of the findings (singular and plural, the first five places or names and "and N more", the
count of places), the lists kept in a finding's detail, and the 7-day VACUUM threshold."""

from __future__ import annotations

import pytest

from fabric_desktop.excellence import catalog_artifacts as ca
from fabric_desktop.excellence import controls as ct

RID = "ev-0000000000000001"
PIPE = {"kind": "pipeline", "activities": 9, "calls_out": 4, "state_unknown": [], "properties_unknown": [],
        "retry_unknown": [], "timeout_unknown": [], "depends_unknown": [], "failure_path": False,
        "failure_path_unknown": False}


def ctx(payload: dict) -> ct.Context:
    return ct.Context({"type": "item", "id": "it"}, {ca.KIND: [{"id": RID, "payload": payload}]})


def test_places_show_five_and_count_the_rest():
    where = [{"cell": i, "line": 1} for i in range(1, 7)]
    shown = "cell 1, line 1; cell 2, line 1; cell 3, line 1; cell 4, line 1; cell 5, line 1"
    assert ca._places(where, 8) == shown + " and 3 more"
    assert ca._places(where, 6) == shown + " and 1 more"
    assert ca._places(where[:5], 5) == shown
    assert ca._places(where[:1], 1) == "cell 1, line 1"


def test_names_show_five_and_count_the_rest():
    names = ["a", "b", "c", "d", "e", "f"]
    assert ca._names(names, 8) == "a, b, c, d, e and 3 more"
    assert ca._names(names, 6) == "a, b, c, d, e and 1 more"
    assert ca._names(names[:5], 5) == "a, b, c, d, e"


def test_a_notebook_finding_counts_its_places_in_the_singular_and_plural():
    one = {"kind": "notebook", "cells": 4, "rules": {"NB012": {"count": 1, "where": [{"cell": 2, "line": 3}]}}}
    got = ca._swallowed(ctx(one))
    assert got["text"] == "An error is caught and ignored (NB012, 1 place: cell 2, line 3)."
    many = {"kind": "notebook", "cells": 4, "rules": {"NB012": {"count": 3, "where": [{"cell": 2, "line": 3}]}}}
    got = ca._swallowed(ctx(many))
    assert got["text"] == "An error is caught and ignored (NB012, 3 places: cell 2, line 3 and 2 more)."
    assert got["detail"] == {"rule": "NB012", "count": 3, "where": [{"cell": 2, "line": 3}]}


def test_the_vacuum_control_names_seven_days():
    got = ca._short_vacuum(ctx({"kind": "notebook", "cells": 1, "rules": {}}))
    assert got["text"] == "No VACUUM keeps less than 7 days of history (NB020, 1 code cells checked)."


@pytest.mark.parametrize("unknown_key", ["retry_unknown", "timeout_unknown"])
def test_an_inspection_from_before_the_policy_was_kept_is_not_evaluated_never_a_pass(unknown_key):
    old = {k: v for k, v in PIPE.items() if k != unknown_key}
    evaluate = ca._no_retry if unknown_key == "retry_unknown" else ca._no_timeout
    got = evaluate(ctx(old))
    assert got["outcome"] == "not_evaluated" and got["needs"] == "the activities' policy"
    no_count = {k: v for k, v in PIPE.items() if k != "calls_out"}
    assert evaluate(ctx(no_count))["outcome"] == "not_evaluated"
    assert evaluate(ctx(PIPE))["outcome"] == "pass"


def test_an_inspection_from_before_the_dependencies_were_kept_is_not_evaluated():
    old = {k: v for k, v in PIPE.items() if k != "failure_path"}
    got = ca._no_failure_path(ctx(old))
    assert got["outcome"] == "not_evaluated" and got["needs"] == "the activities' dependsOn"
    no_count = {k: v for k, v in PIPE.items() if k != "calls_out"}
    assert ca._no_failure_path(ctx(no_count))["outcome"] == "not_evaluated"
    assert ca._no_failure_path(ctx(PIPE))["outcome"] == "fail"


def test_unreadable_activities_are_named_in_the_singular_and_plural_five_at_most():
    got = ca._deactivated(ctx({**PIPE, "state_unknown": ["A"]}))
    assert got["text"] == "What this checks couldn't be read for 1 activity (A)."
    names = ["A", "B", "C", "D", "E", "F"]
    got = ca._deactivated(ctx({**PIPE, "state_unknown": names}))
    assert got["text"] == "What this checks couldn't be read for 6 activities (A, B, C, D, E)."


def test_deactivated_activities_wording_and_detail():
    got = ca._deactivated(ctx({**PIPE, "inactive_count": 1, "inactive": ["Copy"]}))
    assert got["text"] == "1 activity is deactivated: Copy."
    got = ca._deactivated(ctx({**PIPE, "inactive_count": 2, "inactive": ["Copy", "Load"]}))
    assert got["text"] == "2 activities are deactivated: Copy, Load."
    assert got["detail"] == {"inactive": ["Copy", "Load"], "count": 2}


def test_fixed_workspace_wording_and_detail():
    got = ca._fixed_workspace(ctx({**PIPE, "fixed_workspace_count": 1, "fixed_workspace": ["Run"]}))
    assert got["text"] == "1 activity names a fixed workspace id rather than a parameter: Run."
    got = ca._fixed_workspace(ctx({**PIPE, "fixed_workspace_count": 2, "fixed_workspace": ["Run", "Copy"]}))
    assert got["text"] == "2 activities name a fixed workspace id rather than a parameter: Run, Copy."
    assert got["detail"] == {"activities": ["Run", "Copy"], "count": 2}


def test_no_retry_wording_singular_and_plural():
    got = ca._no_retry(ctx({**PIPE, "no_retry_count": 1, "no_retry": ["Copy"]}))
    assert got["text"] == "1 of its 4 activities that call out has no retry: Copy."
    got = ca._no_retry(ctx({**PIPE, "no_retry_count": 2, "no_retry": ["Copy", "Web"]}))
    assert got["text"] == "2 of its 4 activities that call out have no retry: Copy, Web."
    assert got["detail"] == {"activities": ["Copy", "Web"], "count": 2}


def test_no_timeout_wording_singular_and_plural():
    got = ca._no_timeout(ctx({**PIPE, "no_timeout_count": 1, "no_timeout": ["Copy"]}))
    assert got["text"] == "1 of its 4 activities that call out has no timeout under the 12-hour default: Copy."
    got = ca._no_timeout(ctx({**PIPE, "no_timeout_count": 2, "no_timeout": ["Copy", "Web"]}))
    assert got["text"] == "2 of its 4 activities that call out have no timeout under the 12-hour default: Copy, Web."
    assert got["detail"] == {"activities": ["Copy", "Web"], "count": 2}
