"""The Parameters bar: the edges the bar tests don't reach. Pure: nothing runs a cell."""

import pytest

from fabric_desktop import notebook_inputs as ni


def cell(cell_id, source, tags=()):
    return {"cell_id": cell_id, "type": "code", "source": source, "meta": {"tags": list(tags)}}


def test_a_magic_cell_or_one_that_doesnt_parse_has_no_parameters():
    assert ni._parameters(cell("p", "%%sql\nSELECT 1")) == []
    assert ni._parameters(cell("p", "x = (")) == []
    assert ni._parameters(cell("p", "%pip install x\nday = 3")) == [{"name": "day", "type": "int", "default": 3}]


def test_a_float_that_isnt_a_number_says_so():
    shown = ni.inputs([cell("p", "rate = 0.5", ["parameters"])], {"parameters": {"rate": "half"}})
    assert shown["parameters"][0]["error"] == "Type a number."


def test_a_widget_named_twice_shows_once_and_a_markdown_cell_is_skipped():
    cells = [{"cell_id": "m", "type": "markdown", "source": "dbutils.widgets.text('day', '1')"},
             cell("a", "dbutils.widgets.text('day', '1')"), cell("b", "dbutils.widgets.text('day', '2')")]
    widgets = ni.inputs(cells)["widgets"]
    assert [(w["name"], w["cell"], w["default"]) for w in widgets] == [("day", "a", "1")]


def test_a_changed_float_goes_to_fabric_and_one_that_isnt_finite_is_refused():
    cells = [cell("p", "rate = 0.5", ["parameters"])]
    assert ni.fabric_parameters(cells, {"parameters": {"rate": "0.25"}}) == {"rate": {"value": 0.25, "type": "float"}}
    with pytest.raises(ValueError, match="rate is inf, which a run in Fabric can't be given"):
        ni.fabric_parameters(cells, {"parameters": {"rate": "inf"}})
