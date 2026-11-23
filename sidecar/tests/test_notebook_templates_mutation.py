"""notebook_templates: what mutation testing showed no test checked. The marker is read from the first line
only; identify needs a known template, platform and version in the metadata; a Parameters cell that doesn't
parse, one too many or too few, or one after the setup cell is named; setup runs before step-1; a repeated
standard cell is one change; a conforming handed-over notebook has no warning; the handover record carries a
time and the code's digest; accept names a parameter only the record has and needs who accepts; and
record_version writes standards.json sorted with one space of indent and refuses a cell of another version."""

from __future__ import annotations

import copy
import json
import re
import shutil

import pytest

from fabric_desktop import notebook_templates as nt

STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def code(nb):
    return [c for c in nb["cells"] if c["cell_type"] == "code"]


def cell(nb, role, cid=""):
    for c in code(nb):
        m = nt.marker(nt.source_of(c))
        if m and m["role"] == role and (not cid or m["cell"] == cid):
            return c
    raise AssertionError(f"no {role} cell {cid}")


def test_the_marker_is_the_first_line_only():
    m = nt.marker("# kratosforge:parameters template=silver-upsert platform=fabric version=1.0.0\ncell='x'")
    assert m == {"role": "parameters", "template": "silver-upsert", "platform": "fabric", "version": "1.0.0", "cell": ""}


@pytest.mark.parametrize("meta", [
    {"id": "silver-upsert", "platform": "oracle", "version": "1.0.0"},
    {"id": "silver-upsert", "platform": "fabric", "version": ""},
    {"id": "bronze", "platform": "fabric", "version": "1.0.0"},
])
def test_metadata_naming_an_unknown_template_falls_back_to_the_markers(meta):
    nb = nt.load("databricks", "silver")
    nb["metadata"]["kratosforge"]["template"] = meta
    assert nt.identify(nb) == {"template": "silver-upsert", "platform": "databricks", "version": nt.VERSION,
                               "kind": "silver"}


def test_a_parameters_cell_that_doesnt_parse_is_named():
    nb = nt.load("fabric", "silver")
    cell(nb, "parameters")["source"].append("\nx = (")
    changes = nt.check(nb)["changes"]
    assert changes[0].startswith("The Parameters cell doesn't parse (line ")


def test_exactly_one_parameters_cell():
    nb = nt.load("fabric", "silver")
    params = cell(nb, "parameters")
    nb["cells"].insert(nb["cells"].index(params), copy.deepcopy(params))
    assert "The notebook has more than one Parameters cell" in nt.check(nb)["changes"]
    nb["cells"] = [c for c in nb["cells"] if c is not params and c["cell_type"] != "code"
                   or (nt.marker(nt.source_of(c)) or {}).get("role") != "parameters"]
    assert "The notebook needs exactly one Parameters cell" in nt.check(nb)["changes"]


def test_parameters_after_the_setup_cell_are_out_of_order():
    nb = nt.load("fabric", "silver")
    params, setup = cell(nb, "parameters"), cell(nb, "standard", "setup")
    i, j = nb["cells"].index(params), nb["cells"].index(setup)
    nb["cells"][i], nb["cells"][j] = setup, params
    assert nt.check(nb)["changes"] == [(f"Cell {j + 1}, the Parameters cell, is out of order: it must come before "
                                        "the standard cells")]


def test_setup_runs_before_step_1():
    nb = nt.load("fabric", "silver")
    setup, step = cell(nb, "standard", "setup"), cell(nb, "standard", "step-1")
    i, j = nb["cells"].index(setup), nb["cells"].index(step)
    nb["cells"][i], nb["cells"][j] = step, setup
    assert nt.check(nb)["changes"] == [f"Cell {j + 1}, the standard cell setup, is out of order"]


def test_a_repeated_standard_cell_is_one_change():
    nb = nt.load("fabric", "silver")
    nb["cells"].append(copy.deepcopy(cell(nb, "standard", "step-1")))
    assert nt.check(nb)["changes"] == [f"Cell {len(nb['cells'])} repeats the standard cell step-1"]


def test_a_conforming_handed_over_notebook_has_no_warning():
    nb, _ = nt.handover(nt.load("fabric", "silver"), "Sales", "ana@contoso.com")
    result = nt.check(nb)
    assert result["status"] == "conforms" and "warning" not in result
    assert result["handover"] == {"state": "requested", "domain": "Sales", "owner": "ana@contoso.com"}


def test_the_handover_record_has_a_time_and_the_codes_digest():
    nb, record = nt.handover(nt.load("fabric", "silver"), " Sales ", " ana ")
    assert STAMP.fullmatch(record["requested_at"]) and nb["metadata"]["kratosforge"]["handover"]["requested_at"] == \
        record["requested_at"]
    assert re.fullmatch(r"[0-9a-f]{64}", record["code_sha256"])
    other = nt.load("fabric", "gold_fact")
    assert nt.code_digest(other) != record["code_sha256"]
    accepted = nt.accept(nb, "Platform", record)
    assert STAMP.fullmatch(accepted["metadata"]["kratosforge"]["handover"]["accepted_at"])


def test_accept_needs_who_and_names_a_parameter_only_the_record_has():
    nb, record = nt.handover(nt.load("fabric", "silver"), "Sales", "ana")
    with pytest.raises(ValueError, match="Say who in the enterprise accepts it"):
        nt.accept(nb, "  ", record)
    record = {**record, "parameters": {**record["parameters"], "extra": 1}}
    with pytest.raises(ValueError, match="parameter values changed: extra$"):
        nt.accept(nb, "Platform", record)


def test_template_parameters_are_empty_without_a_parameters_cell(monkeypatch):
    nb = nt.load("fabric", "silver")
    nb["cells"] = [c for c in nb["cells"] if c is not cell(nb, "parameters")]
    monkeypatch.setattr(nt, "load", lambda platform, kind: nb)
    assert nt._template_parameters("fabric", "silver") == {}


@pytest.fixture
def templates(tmp_path, monkeypatch):
    folder = tmp_path / "notebook_templates"
    shutil.copytree(nt.HERE, folder, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
    monkeypatch.setattr(nt, "HERE", folder)
    return folder


def test_record_version_writes_sorted_json_with_one_space_of_indent(templates):
    before = json.loads((templates / "standards.json").read_text(encoding="utf-8"))
    unsorted = dict(reversed(list(before.items())))  # a hand-edited file out of order is written sorted again
    (templates / "standards.json").write_text(json.dumps(unsorted), encoding="utf-8")
    nt.record_version()
    raw = (templates / "standards.json").read_bytes()
    assert raw == (json.dumps(before, indent=1, sort_keys=True) + "\n").encode("utf-8")


def test_record_version_refuses_a_cell_marked_with_another_version(templates):
    path = templates / "fabric" / "silver.ipynb"
    nb = json.loads(path.read_text(encoding="utf-8"))
    src = cell(nb, "standard", "step-2")["source"]
    src[0] = src[0].replace(f"version={nt.VERSION}", "version=0.9.0")
    path.write_text(json.dumps(nb), encoding="utf-8")
    before = (templates / "standards.json").read_bytes()
    with pytest.raises(ValueError, match=r"^fabric/silver cell step-2 is marked 0\.9\.0, not "):
        nt.record_version()
    assert (templates / "standards.json").read_bytes() == before
