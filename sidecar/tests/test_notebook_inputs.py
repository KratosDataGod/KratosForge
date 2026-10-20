"""The Parameters and Widgets bar: what it shows, the values it keeps, and the code it adds."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.notebook import Kernel
from fabric_desktop.notebook_inputs import check_values, inputs
from fabric_desktop.server import handle
from fabric_desktop.service import Service

PARAMS = {"cell_id": "p", "type": "code", "meta": {"tags": ["parameters"]},
          "source": 'run_date = "2026-01-01"\nregion: str = "West"\nmax_rows = 100000\nfull_load = False\nratio = 0.5\n'
                    "paths = ['a']\nregion = 'East'"}
WIDGETS = {"cell_id": "w", "type": "code", "source": 'name_var = "dyn"\n'
           'dbutils.widgets.text("start", "2026-10-01", "Start date")\n'
           'dbutils.widgets.dropdown("env", "dev", ["dev", "test", "prod"])\n'
           'dbutils.widgets.multiselect(name="regions", defaultValue="East,West", choices=["East", "West", "North"])\n'
           'dbutils.widgets.text(name_var, "x")'}
USE = {"cell_id": "u", "type": "code", "source": "result = (run_date, region, max_rows, full_load, dbutils.widgets.get('env'), "
                                             "dbutils.widgets.get('regions'))"}


def test_the_bar_lists_literal_parameters_and_widgets():
    got = inputs([PARAMS, WIDGETS, USE])
    assert [(p["name"], p["type"], p["value"]) for p in got["parameters"]] == [
        ("run_date", "str", "2026-01-01"), ("max_rows", "int", "100000"), ("full_load", "bool", "False"),
        ("ratio", "float", "0.5"), ("region", "str", "East")]
    assert [(w["name"], w["kind"], w["value"], w["choices"]) for w in got["widgets"]] == [
        ("start", "text", "2026-10-01", None), ("env", "dropdown", "dev", ["dev", "test", "prod"]),
        ("regions", "multiselect", "East,West", ["East", "West", "North"])]
    assert got["widgets"][0]["label"] == "Start date" and got["parameter_cell"] == "p"
    assert got["suffix"] == {}


def test_changed_values_run_after_their_cell_and_the_code_stays():
    values = {"parameters": {"run_date": "2026-10-19", "max_rows": "5", "full_load": "True", "region": "East"},
              "widgets": {"env": "prod", "regions": "North"}}
    got = inputs([PARAMS, WIDGETS, USE], values)
    assert got["suffix"]["p"].splitlines()[1:] == ["run_date = '2026-10-19'", "max_rows = 5", "full_load = True"]
    k = Kernel()
    for cell in (PARAMS, WIDGETS, USE):
        out = k.run(cell["source"] + ("\n" + got["suffix"][cell["cell_id"]] if cell["cell_id"] in got["suffix"] else ""))
        assert out["status"] == "ok", out
    assert k.namespace["result"] == ("2026-10-19", "East", 5, True, "prod", "North")
    assert PARAMS["source"].startswith('run_date = "2026-01-01"')


@pytest.mark.parametrize("values, error", [
    ({"parameters": {"max_rows": "lots"}}, "whole number"),
    ({"parameters": {"full_load": "yes"}}, "True or False"),
    ({"widgets": {"env": "qa"}}, "Pick from the choices"),
    ({"widgets": {"regions": "East,South"}}, "Pick from the choices"),
])
def test_a_bad_value_is_shown_and_not_run(values, error):
    got = inputs([PARAMS, WIDGETS], values)
    rows = got["parameters"] + got["widgets"]
    assert any(r["error"] and error in r["error"] for r in rows)
    assert got["suffix"] == {}


def test_a_value_with_quotes_is_a_string_not_code():
    got = inputs([PARAMS], {"parameters": {"run_date": "x'); import os; ('"}})
    k = Kernel()
    k.run(PARAMS["source"] + "\n" + got["suffix"]["p"])
    assert k.namespace["run_date"] == "x'); import os; ('"


@pytest.mark.parametrize("bad", [[], {"other": {}}, {"parameters": {"1x": "a"}}, {"widgets": {"a": 1}},
                                 {"widgets": {"a": "x" * 10_001}}])
def test_values_are_checked(bad):
    with pytest.raises(ValueError):
        check_values(bad)


def call(s, method, **params):
    out = handle(s, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" not in out, out
    return out["result"]


def test_values_are_kept_with_the_notebook(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = Service(root=tmp_path / "app")
    call(s, "save_notebook", name="nb", cells=[PARAMS])
    assert call(s, "notebook_inputs", name="nb", cells=[PARAMS])["suffix"] == {}
    got = call(s, "notebook_inputs_set", name="nb", cells=[PARAMS], values={"parameters": {"max_rows": "7"}})
    assert got["suffix"]["p"].endswith("max_rows = 7")
    assert call(s, "notebook_inputs", name="nb", cells=[PARAMS])["parameters"][1]["value"] == "7"
    call(s, "save_notebook", name="nb", cells=[PARAMS])  # a save keeps the values
    assert call(s, "notebook_inputs", name="nb", cells=[PARAMS])["parameters"][1]["value"] == "7"
    call(s, "notebook_inputs_set", name="nb", cells=[PARAMS], values={"parameters": {}, "widgets": {}})
    assert s.notebooks.extra("nb", "inputs") is None
