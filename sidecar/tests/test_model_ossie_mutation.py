"""Ossie conversion (model_ossie.py): what mutation batch 99 found the other tests let through. The missing
package's name, the alias and size messages, the converter's report, dataset metrics lifted to the model,
the merge of a new table's Direct Lake source, and the plan's details. Pure conversions on dicts and YAML
text; nothing is read or written, and nothing reaches Fabric."""

from __future__ import annotations

import json
import sys
import warnings

import pytest

from fabric_desktop import model_ossie as mo
from fabric_desktop import tmdl
from tests.test_model_ossie import _bim


# ---- loading the converter

def _missing_after(monkeypatch, module):
    mo.unavailable()  # loaded once, so MISSING is there to take away
    monkeypatch.delattr(mo, "MISSING")
    monkeypatch.setitem(sys.modules, module, None)  # importing it fails as it would without the package
    mo.unavailable()
    return mo.MISSING


def test_a_missing_yaml_is_named_as_the_package_people_install(monkeypatch):
    assert _missing_after(monkeypatch, "yaml") == "PyYAML"


def test_a_missing_vendored_module_is_named_in_full(monkeypatch):
    name = "fabric_desktop._vendor.ossie_microsoft"
    assert _missing_after(monkeypatch, name) == name


def test_a_loaded_name_asked_for_directly_is_not_answered_by_the_hook():
    mo.unavailable()
    with pytest.raises(AttributeError, match="no attribute 'MISSING'"):
        mo.__getattr__("MISSING")


# ---- reading a file

def test_an_alias_is_refused_with_its_own_line_number():
    with pytest.raises(ValueError, match=r"YAML aliases \(line 3\); write them out in full"):
        mo.parse("a: 1\nb: &x [1]\nc: *x\n")


def test_the_largest_file_is_twenty_million_characters():
    assert mo.MAX_CHARS == 20_000_000


def test_a_file_of_exactly_the_limit_is_read(monkeypatch):
    text = "version: '0'\n"
    monkeypatch.setattr(mo, "MAX_CHARS", len(text))
    with pytest.raises(ValueError, match="which KratosForge doesn.t know"):  # past the size check
        mo.parse(text)


def test_the_size_message_gives_whole_megabytes(monkeypatch):
    monkeypatch.setattr(mo, "MAX_CHARS", 2_000_000)
    with pytest.raises(ValueError) as caught:
        mo.parse("#" * 3_000_000)
    assert str(caught.value) == "This file is 3 MB; an Ossie model is at most 2 MB"


# ---- the converter's report

def test_the_report_splits_each_warning_into_its_object_and_message():
    def convert():
        warnings.warn("[table 't'] format strings preserved in the Power BI stash", stacklevel=1)
        warnings.warn("[nothing closes this", stacklevel=1)
        warnings.warn("a plain warning", stacklevel=1)
        return "done"

    result, report = mo._convert(convert)
    assert result == "done"
    assert report == [
        {"object": "table 't'", "message": "format strings preserved in the Power BI stash", "kind": "kept"},
        {"object": "nothing closes this", "message": "[nothing closes this", "kind": "lost"},
        {"object": "model", "message": "a plain warning", "kind": "lost"}]


# ---- dataset metrics lifted to the model

def _doc(*metrics):
    return {"datasets": [{"name": "fact_sales", "metrics": list(metrics)}]}


def test_only_sql_dialects_are_qualified_with_the_dataset():
    out = mo._lift_dataset_metrics(_doc({"name": "m", "expression": {"dialects": [
        {"dialect": "ANSI_SQL", "expression": "SUM(amount)"},
        {"dialect": "DAX", "expression": "SUM(amount)"}]}}), [])
    assert out["metrics"][0]["expression"]["dialects"] == [
        {"dialect": "ANSI_SQL", "expression": "SUM(fact_sales.amount)"},
        {"dialect": "DAX", "expression": "SUM(amount)"}]


def test_a_metric_without_an_expression_or_extensions_is_still_lifted():
    out = mo._lift_dataset_metrics(_doc({"name": "bare"}, {"name": "empty", "expression": {}}), [])
    assert [m["name"] for m in out["metrics"]] == ["bare", "empty"]
    for m in out["metrics"]:
        assert m["custom_extensions"] == [{"vendor_name": "POWER_BI", "data": '{"_v": 2, "table": "fact_sales"}'}]


def test_a_sql_entry_without_text_is_left_without_it():
    out = mo._lift_dataset_metrics(_doc({"name": "m", "expression": {"dialects": [{"dialect": "ANSI_SQL"}]}}), [])
    assert out["metrics"][0]["expression"]["dialects"] == [{"dialect": "ANSI_SQL"}]


def test_the_power_bi_stash_gains_its_table_and_other_vendors_stay():
    other = {"vendor_name": "OTHER", "data": "not json"}
    stash = {"vendor_name": "POWER_BI", "data": json.dumps({"z": 1, "_v": 2})}
    out = mo._lift_dataset_metrics(_doc({"name": "m", "custom_extensions": [other, stash]}), [])
    assert out["metrics"][0]["custom_extensions"] == [
        other, {"vendor_name": "POWER_BI", "data": '{"_v": 2, "table": "fact_sales", "z": 1}'}]


# ---- small helpers

def test_by_name_skips_what_has_no_name():
    a = {"name": "a"}
    assert mo._by_name([a, {"x": 1}, {"name": ""}, "s", None]) == {"a": a}
    assert mo._by_name(None) == {}


def test_a_placeholder_is_blank_and_annotated():
    note = [{"name": mo.PLACEHOLDER_ANNOTATION, "value": "SUM(x)"}]
    assert mo._is_placeholder({"expression": " blank() ", "annotations": note})
    assert not mo._is_placeholder({"expression": "BLANK()"})
    assert not mo._is_placeholder({"expression": "BLANK()", "annotations": [{"name": "other"}, "s"]})
    assert not mo._is_placeholder({"expression": "SUM(x)", "annotations": note})


def test_the_shared_source_is_the_first_direct_lake_partition_that_names_one():
    model = {"tables": [{"name": "t", "partitions": [
        {"mode": "import", "source": {"expressionSource": "Import"}},
        {"mode": "directLake", "source": {}},
        {"mode": "directLake"},
        {"mode": "directLake", "source": {"expressionSource": "Lake"}}]}]}
    assert mo._direct_lake_expression(model) == "Lake"


# ---- merge

def _dl(name, source):
    return {"name": name, "mode": "directLake", "source": {"type": "entity", "entityName": name, "expressionSource": source}}


def _model(*tables, expressions=None):
    model = {"tables": list(tables)}
    if expressions is not None:
        model["expressions"] = expressions
    return {"model": model}


def test_a_new_tables_partitions_read_through_the_models_own_source():
    current = _model({"name": "old", "partitions": [_dl("old", "DatabaseQuery")]},
                     expressions=[{"name": "DatabaseQuery", "kind": "m", "expression": "mine"}])
    incoming = _model({"name": "new", "partitions": [_dl("new", "DirectLake"), _dl("side", "Elsewhere")]},
                      {"name": "bare"})
    out = mo.merge(current, incoming)["model"]
    new = next(t for t in out["tables"] if t["name"] == "new")
    assert [p["source"]["expressionSource"] for p in new["partitions"]] == ["DatabaseQuery", "Elsewhere"]
    assert out["expressions"] == [{"name": "DatabaseQuery", "kind": "m", "expression": "mine"}]


def test_without_a_source_of_its_own_the_model_keeps_the_files():
    current = _model({"name": "old", "partitions": [{"name": "old", "mode": "import"}]})
    incoming = _model({"name": "new", "partitions": [_dl("new", "DirectLake")]})
    out = mo.merge(current, incoming)["model"]
    new = next(t for t in out["tables"] if t["name"] == "new")
    assert new["partitions"][0]["source"]["expressionSource"] == "DirectLake"


def test_a_table_in_both_keeps_the_models_partitions_or_the_files_when_it_has_none():
    current = _model({"name": "a", "partitions": [_dl("a", "DatabaseQuery")]}, {"name": "b"})
    incoming = _model({"name": "a", "partitions": [_dl("a", "DirectLake")]},
                      {"name": "b", "partitions": [_dl("b", "DirectLake")]})
    out = {t["name"]: t for t in mo.merge(current, incoming)["model"]["tables"]}
    assert out["a"]["partitions"] == [_dl("a", "DatabaseQuery")]
    assert out["b"]["partitions"] == [_dl("b", "DirectLake")]


# ---- plan

def test_a_new_tables_detail_counts_what_it_has():
    final = _model({"name": "t", "columns": [{"name": "a"}, {"name": "b"}]})
    assert mo.plan(None, final) == [{"change": "add", "kind": "table", "object": "t", "detail": "2 columns, 0 measures"}]


def test_a_changed_column_without_an_expression_has_no_detail():
    current = _model({"name": "t", "columns": [{"name": "a", "dataType": "int64"}]})
    final = _model({"name": "t", "columns": [{"name": "a", "dataType": "string"}]})
    assert mo.plan(current, final) == [{"change": "change", "kind": "column", "object": "t[a]", "detail": ""}]


# ---- round trips

def test_the_round_trip_sends_the_dax_as_written(monkeypatch):
    seen = []
    real = mo.export

    def export(bim, translate_dax=True):
        seen.append(translate_dax)
        return real(bim, translate_dax=translate_dax)

    monkeypatch.setattr(mo, "export", export)
    mo.round_trip(_bim())
    assert seen == [False]


def test_tmdl_to_ossie_writes_simple_measures_as_sql_unless_asked_not_to():
    files, _ = tmdl.from_bim(_bim())
    assert mo.tmdl_to_ossie(files, "SM_Gold")["counts"]["portable_metrics"] == 1
    assert mo.tmdl_to_ossie(files, "SM_Gold", translate_dax=False)["counts"]["portable_metrics"] == 0
