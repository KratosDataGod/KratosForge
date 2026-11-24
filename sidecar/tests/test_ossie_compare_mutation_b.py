"""Mutation-testing follow-ups for ossie_compare.py (batch 464): a JSON Ossie text over its size cap,
a dynamic format string, a removed description, a dialect only one version writes, a relationship
matched by name when its ends change, a table that became a calculation group, two measures of one
name in different tables, the side an expression couldn't be compared on, the side info and notes of
a report, and the redacted copy's error and warnings."""
from __future__ import annotations

import copy
import json
import logging

import pytest
import yaml

from fabric_desktop import model_ossie, ossie_compare as oc, tmdl
from test_model_ossie import _bim
from test_tmdl import FILES


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _export(bim, translate_dax=True):
    return yaml.safe_load(model_ossie.export(bim, translate_dax=translate_dax)["yaml"])


def _table(bim, name):
    return next(t for t in bim["model"]["tables"] if t["name"] == name)


def _row(report, ident):
    hits = [o for o in report["objects"] if o["id"] == ident]
    assert len(hits) == 1, (ident, [o["id"] for o in report["objects"]])
    return hits[0]


# ---- reading


def test_a_json_ossie_text_over_the_cap_is_refused_in_whole_mb(monkeypatch):
    monkeypatch.setattr(oc.mo, "MAX_CHARS", 1_000_000)
    monkeypatch.setattr(oc.sp, "MAX_TMSL_CHARS", 10_000_000)
    text = json.dumps(_export(_bim()))
    text = text[:-1] + " " * (2_000_001 - len(text)) + "}"
    assert len(text) == 2_000_001
    report = oc.compare_versions(text, _bim())
    assert report["status"] == "failed"
    assert "This file is 2 MB, but an Ossie model is at most 1 MB" in report["error"]


def test_a_json_ossie_text_under_the_cap_and_a_large_model_bim_are_read(monkeypatch):
    small = json.dumps(_export(_bim()))
    report = oc.compare_versions(small, _bim())
    assert report["status"] == "compared" and report["objects"] == []
    monkeypatch.setattr(oc.mo, "MAX_CHARS", 10)
    report = oc.compare_versions(json.dumps(_bim()), _bim())
    assert report["status"] == "compared" and report["objects"] == []


# ---- what changed


def test_a_dynamic_format_string_is_shown_with_its_definition():
    new = _bim()
    measure = _table(new, "fact_sales")["measures"][1]
    measure["formatStringDefinition"] = {"expression": "\"0.0\""}
    change = _row(oc.compare_versions(_bim(), new), "table 'fact_sales' measure 'fact_sales total amount'")["changes"]
    assert [c["what"] for c in change] == ["format"]
    shown = " | dynamic " + model_ossie._canon(measure["formatStringDefinition"])
    assert change[0]["detail_redacted"] == f"format string changed (0 to {len(shown)} characters)"
    assert "dynamic" in change[0]["detail"]


def test_a_removed_and_a_changed_description_say_so():
    old = _bim()
    _table(old, "dim_customer")["description"] = "Customers"
    removed = _row(oc.compare_versions(old, _bim()), "table 'dim_customer'")["changes"]
    assert removed[0]["detail"] == "description removed"
    new = _bim()
    _table(new, "dim_customer")["description"] = "Clients"
    changed = _row(oc.compare_versions(old, new), "table 'dim_customer'")["changes"]
    assert changed[0]["detail"] == "description changed"


def test_a_dialect_only_one_version_writes_is_no_change_when_the_forms_match():
    report = oc.compare_versions(_bim(), _export(_bim()))
    assert report["status"] == "compared" and report["error"] == "" and report["objects"] == []


def test_a_dialect_only_one_version_writes_is_named_when_the_forms_differ():
    new = _export(_bim())
    old = _bim()
    _table(old, "fact_sales")["measures"][1]["expression"] = "MAX('fact_sales'[amount])"
    report = oc.compare_versions(old, new)
    assert report["status"] == "compared"
    row = _row(report, "table 'fact_sales' measure 'fact_sales total amount'")
    assert any("dialects changed: " in c["detail"] or "text changed" in c["detail"] for c in row["changes"])


def test_a_relationship_whose_ends_change_is_matched_by_its_name():
    new = _bim()
    _table(new, "fact_sales")["columns"].append({"name": "country", "dataType": "string"})
    rel = new["model"]["relationships"][0]
    rel["fromColumn"], rel["toColumn"] = "country", "country"
    report = oc.compare_versions(_bim(), new)
    row = _row(report, "relationship 'r1'")
    assert row["status"] == oc.CHANGED
    assert report["summary"]["by_kind"]["relationship"] == {"added": 0, "removed": 0, "changed": 1, "unchanged": 0}


def test_a_table_that_becomes_a_calculation_group_is_a_kind_change():
    group = tmdl.to_bim(FILES)[0]
    plain = copy.deepcopy(group)
    calc = _table(plain, "Calc")
    calc.pop("calculationGroup", None)
    report = oc.compare_versions(plain, group)
    row = _row(report, "table 'Calc'")
    assert row["status"] == oc.CHANGED and row["kind"] == "calculation_group" and row["security"] is True
    assert [c["what"] for c in row["changes"]] == ["kind"]


def test_two_measures_of_one_name_are_each_read_from_their_own_table():
    def model(fmt):
        bim = _bim()
        _table(bim, "fact_sales")["measures"].append({"name": "m", "expression": "1", "formatString": "0"})
        _table(bim, "dim_customer")["measures"] = [{"name": "m", "expression": "1", "formatString": fmt}]
        return bim
    report = oc.compare_versions(model("0"), model("0.0"))
    assert report["status"] == "compared"
    formats = [c for o in report["objects"] for c in o["changes"] if c["what"] == "format"]
    assert len(formats) == 1 and "0.0" in formats[0]["detail"]


def test_the_reason_an_expression_wasnt_compared_names_only_the_side_outside_the_subset():
    new = _bim()
    _table(new, "fact_sales")["measures"][0]["expression"] = "CALCULATE(COUNTROWS('fact_sales'))"
    detail = _row(oc.compare_versions(_bim(), new), "table 'fact_sales' measure 'fact_sales rows'")["changes"][0]
    assert "new version: " in detail["detail"] and "old version: " not in detail["detail"]


def test_a_measure_form_reads_the_measure_names_of_its_own_version():
    old, new = _bim(), _bim()
    _table(old, "fact_sales")["measures"].append({"name": "base", "expression": "SUM('fact_sales'[amount])"})
    _table(old, "fact_sales")["measures"].append({"name": "ratio", "expression": "[base] / [fact_sales rows]"})
    _table(new, "fact_sales")["measures"].append({"name": "ratio", "expression": "[base]/[fact_sales rows]"})
    detail = _row(oc.compare_versions(old, new), "table 'fact_sales' measure 'ratio'")["changes"][0]["detail"]
    assert "new version: " in detail and "old version: " not in detail


# ---- the report


def test_a_compared_report_reads_both_sides_with_their_labels_and_no_notes():
    report = oc.compare_versions(_bim(), _bim())
    assert report["notes"] == []
    for side in report["sides"].values():
        assert side["read"] is True and side["label"]


def test_a_refused_side_is_not_read():
    doc = _export(_bim())
    report = oc.compare_versions(dict(doc, version="0.3.0.dev0"), _bim())
    assert report["status"] == "refused" and report["sides"]["old"]["error"] == ""
    assert report["sides"]["old"]["read"] is False and report["sides"]["new"]["read"] is True


def test_a_failed_report_has_no_notes():
    report = oc.compare_versions("{{{", _bim())
    assert report["status"] == "failed" and report["notes"] == []


def test_a_comparison_that_trips_on_a_shape_is_a_failed_report(monkeypatch):
    def trip(left, right):
        raise KeyError("shape")
    monkeypatch.setattr(oc, "_compare", trip)
    report = oc.compare_versions(_bim(), _bim())
    assert report["status"] == "failed" and "the comparison couldn't be completed: KeyError" in report["error"]


# ---- redacted


def test_a_redacted_read_error_keeps_only_its_type():
    safe = oc.redacted(oc.compare_versions("{{{", _bim()))
    assert safe["sides"]["old"]["error"] == "ValueError"
    assert safe["error"] == "the old version could not be read: ValueError"


def test_a_redacted_report_keeps_a_warning_that_isnt_about_a_role():
    files = dict(FILES)
    table = next(k for k in files if "tables/" in k)
    new = {**files, table: files[table].rstrip("\n") + "\n\tfrobnicate the widget\n"}
    report = oc.compare_versions(files, new)
    assert report["sides"]["new"]["warnings"]
    assert oc.redacted(report)["sides"]["new"]["warnings"] == report["sides"]["new"]["warnings"]


def test_a_json_ossie_text_exactly_at_the_cap_is_read(monkeypatch):
    text = json.dumps(_export(_bim()))
    monkeypatch.setattr(oc.mo, "MAX_CHARS", len(text))
    report = oc.compare_versions(text, _bim())
    assert report["status"] == "compared" and report["objects"] == []


def test_a_dialect_swapped_for_another_names_which_went_and_which_came():
    old, new = _export(_bim()), _export(_bim())
    old["metrics"][1]["expression"]["dialects"] = [{"dialect": "DAX", "expression": "SUM('fact_sales'[amount])"}]
    new["metrics"][1]["expression"]["dialects"] = [{"dialect": "OSSIE_SQL_2026", "expression": "MAX(fact_sales.amount)"}]
    row = _row(oc.compare_versions(old, new), "table 'fact_sales' measure 'fact_sales total amount'")
    assert row["changes"][0]["detail"].startswith("dialects changed: DAX removed, OSSIE_SQL_2026 added: ")


def test_a_model_level_metric_is_read_by_its_name():
    old, new = _export(_bim()), _export(_bim())
    for doc in (old, new):
        doc["metrics"].append({"name": "lonely", "expression": {"dialects": [
            {"dialect": "OSSIE_SQL_2026", "expression": "SUM(fact_sales.amount)"}]}})
    new["metrics"][-1]["description"] = "now described"
    row = _row(oc.compare_versions(old, new), "table 'fact_sales' measure 'lonely'")
    assert [c["detail"] for c in row["changes"]] == ["description added"]


def test_a_report_notes_both_what_was_compared_by_name_and_what_was_left_out():
    doc = _export(_bim())
    composite = copy.deepcopy(doc["relationships"][0])
    composite["name"] = "composite"
    composite["from_columns"], composite["to_columns"] = composite["from_columns"] * 2, composite["to_columns"] * 2
    doc["relationships"].append(composite)
    doc["metrics"].append({"name": 5})
    report = oc.compare_versions(doc, copy.deepcopy(doc))
    assert report["status"] == "compared" and len(report["notes"]) == 2
    assert "compared by name only" in report["notes"][0] and "left out" in report["notes"][1]
