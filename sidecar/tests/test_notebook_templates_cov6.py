"""Enterprise notebook templates (notebook_templates), the paths the other tests leave: a Parameters cell that
doesn't parse, an empty code cell, a cell from another template, a standard cell repeated, a template without a
Parameters cell, an accept with no one named, and recording a version whose cells are marked with another one.
On copies of the shipped templates; standards.json is never written."""

from __future__ import annotations

import copy

import pytest

from fabric_desktop import notebook_templates as nt


def _template(platform="fabric", kind="silver"):
    return copy.deepcopy(nt.load(platform, kind))


def _code_cell(source):
    return {"cell_type": "code", "metadata": {}, "outputs": [], "execution_count": None, "source": source}


def _index(nb, role, cell=""):
    for i, c in enumerate(nb["cells"]):
        m = nt.marker(nt.source_of(c)) if c["cell_type"] == "code" else None
        if m and m["role"] == role and (not cell or m["cell"] == cell):
            return i
    raise AssertionError(f"no {role} cell {cell}")


def test_a_template_as_shipped_conforms_and_an_empty_code_cell_changes_nothing():
    nb = _template()
    nb["cells"].append(_code_cell("   \n"))
    assert nt.check(nb)["status"] == "conforms"


def test_a_parameters_cell_that_doesnt_parse_is_named_with_its_line():
    nb = _template()
    i = _index(nb, "parameters")
    first = nt.source_of(nb["cells"][i]).split("\n", 1)[0]
    nb["cells"][i]["source"] = first + "\nsource_table = 'x'\nkeys = [\n"
    result = nt.check(nb)
    assert result["status"] == "domain-owned"
    assert any(c.startswith("The Parameters cell doesn't parse (line ") for c in result["changes"])
    assert "The Parameters cell is missing " in " ".join(result["changes"]), "no values were read from it"


def test_a_cell_from_another_template_and_a_repeated_standard_cell_are_changes():
    nb = _template()
    i = _index(nb, "standard")
    cid = nt.marker(nt.source_of(nb["cells"][i]))["cell"]
    nb["cells"].append(copy.deepcopy(nb["cells"][i]))
    other = nt.source_of(_template("databricks", "gold_fact")["cells"][_index(_template("databricks", "gold_fact"),
                                                                                  "standard")])
    nb["cells"].append(_code_cell(other))
    n = len(nb["cells"])
    changes = nt.check(nb)["changes"]
    assert f"Cell {n - 1} repeats the standard cell {cid}" in changes
    assert f"Cell {n} comes from another template (gold-fact for databricks)" in changes


def test_a_template_without_a_parameters_cell_asks_for_no_values(monkeypatch):
    nb = _template()
    real = nt.load
    monkeypatch.setattr(nt, "load", lambda platform, kind: {"cells": [c for c in real(platform, kind)["cells"]
                                                                      if "kratosforge:parameters" not in nt.source_of(c)]})
    assert nt._template_parameters("fabric", "silver") == {}
    nb["cells"][_index(nb, "parameters")]["source"] = nt.source_of(nb["cells"][_index(nb, "parameters")]).split("\n")[0]
    result = nt.check(nb)
    assert result["status"] == "conforms" and result["parameters"] == {}


def test_accepting_needs_someone_named():
    with pytest.raises(ValueError, match="^Say who in the enterprise accepts it$"):
        nt.accept(_template(), "  ", {})


def test_a_standard_cell_marked_with_another_version_stops_recording(monkeypatch):
    nb = _template()
    i = _index(nb, "standard")
    m = nt.marker(nt.source_of(nb["cells"][i]))
    nb["cells"][i]["source"] = nt.source_of(nb["cells"][i]).replace(f"version={nt.VERSION}", "version=0.9.0", 1)
    monkeypatch.setattr(nt, "load", lambda platform, kind: nb)
    writes = []
    monkeypatch.setattr(nt.jsonfile, "write_text", lambda *a, **kw: writes.append(a))
    with pytest.raises(ValueError, match=f"^fabric/silver cell {m['cell']} is marked 0.9.0, not {nt.VERSION}$"):
        nt.record_version()
    assert writes == []


def test_a_notebook_without_its_parameters_cell_needs_one():
    nb = _template()
    del nb["cells"][_index(nb, "parameters")]
    result = nt.check(nb)
    assert result["status"] == "domain-owned"
    assert "The notebook needs exactly one Parameters cell" in result["changes"]
