"""Mutation batch 357: the notebook and pipeline controls' exact texts and edge values
(catalog_artifacts.py), which the excellence tests check only by outcome."""

from __future__ import annotations

from fabric_desktop.excellence import catalog_artifacts as ca
from fabric_desktop.excellence import controls as ct


def ctx(**payload):
    return ct.Context({"id": "it"}, {ca.KIND: [{"id": "e1", "payload": payload}]})


def at(n):
    return [{"cell": i, "line": i + 10} for i in range(1, n + 1)]


def test_places_lists_five_and_counts_the_rest():
    assert ca._places(at(2), 2) == "cell 1, line 11; cell 2, line 12"
    assert ca._places(at(6), 6) == ("cell 1, line 11; cell 2, line 12; cell 3, line 13; cell 4, line 14; "
                                    "cell 5, line 15 and 1 more")
    assert ca._places(at(5), 5).endswith("cell 5, line 15")


def test_names_lists_five_and_counts_the_rest():
    assert ca._names(["a", "b"], 2) == "a, b"
    assert ca._names(list("abcdef"), 6) == "a, b, c, d, e and 1 more"
    assert ca._names(list("abcde"), 5) == "a, b, c, d, e"


def test_a_notebook_rule_names_one_place_and_many():
    one = ca._secret(ctx(rules={"NB001": {"count": 1, "where": at(1)}}, cells=4))
    assert one["text"] == "A secret is written in the notebook's code (NB001, 1 place: cell 1, line 11)."
    two = ca._secret(ctx(rules={"NB001": {"count": 2, "where": at(2)}}))
    assert two["text"].startswith("A secret is written in the notebook's code (NB001, 2 places: ")
    no_count = ca._secret(ctx(rules={"NB001": {"where": at(1)}}))
    assert no_count["detail"]["count"] == 0
    assert "(NB001, 0 places: cell 1, line 11)" in no_count["text"]


def test_a_clean_notebook_counts_its_cells_or_zero():
    assert ca._secret(ctx(rules={}))["text"] == ("No key, password or connection string found in the code "
                                                  "(NB001, 0 code cells checked).")
    assert ca._secret(ctx(rules={}, cells=3))["text"].endswith("(NB001, 3 code cells checked).")


def test_short_vacuum_names_seven_days():
    assert ca.MAX_SAFE_RETENTION_DAYS == 7
    assert ca._short_vacuum(ctx(rules={}))["text"].startswith("No VACUUM keeps less than 7 days of history")


def test_an_unknown_activity_is_named_once_or_many():
    one = ca._deactivated(ctx(kind="pipeline", activities=3, state_unknown=["a"]))
    assert one["text"] == "What this checks couldn't be read for 1 activity (a)."
    many = ca._deactivated(ctx(kind="pipeline", activities=9, state_unknown=list("abcdef")))
    assert many["text"] == "What this checks couldn't be read for 6 activities (a, b, c, d, e)."


def test_deactivated_texts():
    one = ca._deactivated(ctx(kind="pipeline", activities=3, inactive_count=1, inactive=["Copy"]))
    assert one["text"] == "1 activity is deactivated: Copy."
    assert one["detail"] == {"inactive": ["Copy"], "count": 1}
    two = ca._deactivated(ctx(kind="pipeline", activities=3, inactive_count=2, inactive=["A", "B"]))
    assert two["text"] == "2 activities are deactivated: A, B."
    assert ca._deactivated(ctx(kind="pipeline", activities=3))["text"] == "None of its 3 activities is deactivated."


def test_fixed_workspace_texts():
    one = ca._fixed_workspace(ctx(kind="pipeline", activities=3, fixed_workspace_count=1, fixed_workspace=["Nb"]))
    assert one["text"] == "1 activity names a fixed workspace id rather than a parameter: Nb."
    assert one["detail"] == {"activities": ["Nb"], "count": 1}
    two = ca._fixed_workspace(ctx(kind="pipeline", activities=3, fixed_workspace_count=2, fixed_workspace=["A", "B"]))
    assert two["text"] == "2 activities name a fixed workspace id rather than a parameter: A, B."
