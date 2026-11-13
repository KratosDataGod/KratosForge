"""The offline TMDL reader (tmdl.py): extended properties, fences on one line, mixed indents, flags that
aren't TMSL, and a file nested too deeply. Plain strings in, a model.bim dict out; no Fabric call."""

from __future__ import annotations

import pytest

from fabric_desktop import tmdl

T = "\t"
MODEL = {"definition/model.tmdl": "model Model\n\tculture: en-US\n"}


def table(*lines):
    bim, report = tmdl.to_bim(MODEL | {"definition/tables/S.tmdl": "\n".join(["table S", *lines])}, "M")
    return bim["model"]["tables"][0], report


def test_an_extended_property_holding_json_is_read_as_json_and_other_text_is_kept():
    s, _ = table(f'{T}extendedProperty Json = {{"a": 1}}', f"{T}extendedProperty Note = not json")
    assert s["extendedProperties"] == [{"name": "Json", "value": {"a": 1}, "type": "json"},
                                       {"name": "Note", "value": "not json"}]


def test_a_property_named_with_a_leading_underscore_isnt_carried_into_the_model():
    s, _ = table(f"{T}_internal: x", f"{T}lineageTag: t-1")
    assert "_internal" not in s and s["lineageTag"] == "t-1"


def test_a_fence_closed_on_its_own_line_and_one_with_text_after_it():
    s, _ = table(f"{T}measure One = ```1```", f"{T}measure Two = ```SUM(x)", f"{T}{T}{T}+ 1", f"{T}{T}```")
    assert s["measures"][0]["expression"] == "1"
    assert s["measures"][1]["expression"].startswith("SUM(x)") and s["measures"][1]["expression"].endswith("+ 1")


def test_a_line_indented_with_spaces_before_tabs_is_kept_as_written():
    s, _ = table(f"{T}measure Mixed = ```", f"{T}{T}{T}VAR x = 1", f"  {T}{T}{T}RETURN x", f"{T}{T}```")
    assert s["measures"][0]["expression"] == f"VAR x = 1\n  {T}{T}{T}RETURN x"


def test_a_file_that_ends_in_blank_lines_reads_to_the_end():
    s, report = table(f"{T}measure M = 1", f"{T}{T}isHidden", "", "", "")
    assert s["measures"] == [{"name": "M", "expression": "1", "isHidden": True}] and report == []


def test_a_property_at_the_top_of_a_file_goes_onto_the_model():
    bim, _ = tmdl.to_bim(MODEL | {"definition/extra.tmdl": "defaultPowerBIDataSourceVersion: powerBI_V3\n"}, "M")
    assert bim["model"]["defaultPowerBIDataSourceVersion"] == "powerBI_V3"


def test_a_file_nested_too_deeply_is_refused_by_name(monkeypatch):
    def too_deep(self, level, into):
        raise RecursionError

    monkeypatch.setattr(tmdl._Reader, "block", too_deep)
    with pytest.raises(ValueError, match="model.tmdl is nested too deeply"):
        tmdl.to_bim(MODEL)
