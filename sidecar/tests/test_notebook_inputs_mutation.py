"""Mutation testing batch 9 (docs/quality/mutation-testing.md): the notebook's Parameters bar.

Each test pins a behavior a surviving mutant changed without any other test noticing: an annotated
parameter, a widget left out when any of its arguments or its name isn't a literal, and when a
widget's value is checked against its choices (dropdown and multiselect only, a multiselect split
on commas, a dropdown choice that has a comma kept whole)."""

from __future__ import annotations

from fabric_desktop import notebook_inputs


def _code(source: str, cell_id: str = "c1", tags: list[str] | None = None) -> dict:
    cell = {"type": "code", "cell_id": cell_id, "source": source}
    if tags:
        cell["meta"] = {"tags": tags}
    return cell


def _widgets(source: str, values: dict | None = None) -> list[dict]:
    return notebook_inputs.inputs([_code(source)], {"widgets": values or {}})["widgets"]


def _error(source: str, value: str) -> str | None:
    (row,) = _widgets(source, {"w": value})
    return row["error"]


# Parameters


def test_an_annotated_assignment_is_a_parameter():
    got = notebook_inputs.inputs([_code("days: int = 7\nlabel: str", tags=["parameters"])])
    assert [(p["name"], p["type"], p["default"]) for p in got["parameters"]] == [("days", "int", "7")]


# Which widgets the bar shows


def test_a_widget_whose_default_is_a_variable_isnt_shown():
    assert _widgets("start = '2026'\ndbutils.widgets.text('start_date', start)") == []


def test_a_widget_with_one_argument_that_isnt_a_literal_isnt_shown():
    assert _widgets("dbutils.widgets.dropdown('env', 'dev', ENVS)") == []
    assert _widgets("dbutils.widgets.dropdown('env', 'dev', choices=ENVS)") == []


def test_a_widget_whose_name_isnt_text_isnt_shown():
    assert _widgets("dbutils.widgets.text(5, 'x')") == []


def test_a_widget_with_literal_arguments_is_shown():
    (row,) = _widgets("dbutils.widgets.text('start_date', '2026-01-01', 'Start')")
    assert (row["name"], row["default"], row["label"], row["choices"]) == ("start_date", "2026-01-01", "Start", None)


# Checking a value against the choices


def test_a_dropdown_value_must_be_one_of_its_choices():
    src = "dbutils.widgets.dropdown('w', 'dev', ['dev', 'prod'])"
    assert _error(src, "prod") is None
    assert _error(src, "test") == "Pick from the choices."


def test_a_dropdown_choice_with_a_comma_is_one_choice():
    src = "dbutils.widgets.dropdown('w', 'a,b', ['a,b', 'c'])"
    assert _error(src, "a,b") is None


def test_a_multiselect_value_is_each_of_its_comma_separated_picks():
    src = "dbutils.widgets.multiselect('w', 'East', ['East', 'West', 'North'])"
    assert _error(src, "East,North") is None
    assert _error(src, "East,South") == "Pick from the choices."


def test_a_combobox_takes_a_value_that_isnt_a_choice():
    assert _error("dbutils.widgets.combobox('w', 'dev', ['dev', 'prod'])", "anything") is None


def test_a_text_widget_takes_any_value():
    assert _error("dbutils.widgets.text('w', 'x')", "anything at all, even, with commas") is None


def test_a_dropdown_left_at_its_default_isnt_checked():
    (row,) = _widgets("dbutils.widgets.dropdown('w', 'dev', ['dev', 'prod'])")
    assert row["value"] == "dev" and row["error"] is None
