"""notebook_templates: check() on notebooks changed in the ways the main tests don't try, and the guards of
accept() and record_version()."""

from __future__ import annotations

import copy
import json
import shutil

import pytest

from fabric_desktop import notebook_templates as nt


def _code(nb):
    return [c for c in nb["cells"] if c["cell_type"] == "code"]


def _cell(nb, role, cell=""):
    for c in _code(nb):
        m = nt.marker(nt.source_of(c))
        if m and m["role"] == role and (not cell or m["cell"] == cell):
            return c
    raise AssertionError(f"no {role} cell {cell}")


@pytest.fixture()
def nb():
    return nt.load("fabric", "silver")


def test_the_template_itself_conforms(nb):
    assert nt.check(nb)["status"] == "conforms"


def test_an_empty_unmarked_code_cell_doesnt_count(nb):
    nb["cells"].append({"cell_type": "code", "source": ["   \n"], "metadata": {}, "outputs": []})
    assert nt.check(nb)["status"] == "conforms"


def test_a_cell_from_another_template_is_named(nb):
    other = _cell(nt.load("databricks", "gold_fact"), "standard")
    nb["cells"].append(copy.deepcopy(other))
    out = nt.check(nb)
    n = len(nb["cells"])
    assert out["status"] == "domain-owned"
    assert f"Cell {n} comes from another template (gold-fact for databricks)" in out["changes"]


def test_a_repeated_standard_cell_is_named(nb):
    step = _cell(nb, "standard", "step-1")
    nb["cells"].append(copy.deepcopy(step))
    out = nt.check(nb)
    assert f"Cell {len(nb['cells'])} repeats the standard cell step-1" in out["changes"]


def test_a_parameters_cell_that_does_not_parse(nb):
    params = _cell(nb, "parameters")
    params["source"] = nt.source_of(params) + "\nx = (\n"
    out = nt.check(nb)
    assert any(c.startswith("The Parameters cell doesn't parse (line ") for c in out["changes"])
    assert out["parameters"] == {}


def test_two_parameters_cells(nb):
    nb["cells"].insert(0, copy.deepcopy(_cell(nb, "parameters")))
    out = nt.check(nb)
    assert "The notebook has more than one Parameters cell" in out["changes"]


def test_a_template_without_a_parameters_cell_has_no_parameters(monkeypatch):
    real = nt.load
    def load(platform, kind):
        data = real(platform, kind)
        data["cells"] = [c for c in data["cells"] if (nt.marker(nt.source_of(c)) or {}).get("role") != "parameters"]
        return data
    monkeypatch.setattr(nt, "load", load)
    assert nt._template_parameters("fabric", "silver") == {}


def test_accept_needs_who_accepts(nb):
    with pytest.raises(ValueError, match="Say who in the enterprise accepts it"):
        nt.accept(nb, "  ", {})


def test_record_version_refuses_a_cell_marked_with_another_version(tmp_path, monkeypatch):
    folder = tmp_path / "notebook_templates"
    shutil.copytree(nt.HERE, folder, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
    monkeypatch.setattr(nt, "HERE", folder)
    path = folder / "fabric" / "silver.ipynb"
    data = json.loads(path.read_text(encoding="utf-8"))
    step = _cell(data, "standard", "step-1")
    source = nt.source_of(step).replace(f"version={nt.VERSION}", "version=0.9.0", 1)
    step["source"] = source
    path.write_text(json.dumps(data), encoding="utf-8")
    before = (folder / "standards.json").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=f"fabric/silver cell step-1 is marked 0.9.0, not {nt.VERSION}"):
        nt.record_version()
    assert (folder / "standards.json").read_text(encoding="utf-8") == before  # nothing written
