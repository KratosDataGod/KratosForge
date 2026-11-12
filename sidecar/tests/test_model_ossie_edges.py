"""Ossie conversion: a missing package, dataset metrics that can't be lifted, and the merge edges the
main tests don't reach. Pure conversions on dicts and YAML text; nothing is read or written."""

import sys

import pytest
import yaml

from fabric_desktop import model_ossie as mo
from tests.test_model_ossie import SOURCE, _bim, _ossie


def test_a_missing_yaml_package_is_named_and_stops_conversion(monkeypatch):
    monkeypatch.delattr(mo, "MISSING")
    monkeypatch.setitem(sys.modules, "yaml", None)  # import yaml fails as it would without PyYAML
    assert "need the Python package PyYAML" in mo.unavailable()
    with pytest.raises(RuntimeError, match="PyYAML"):
        mo.require()


def test_an_unknown_attribute_is_an_attribute_error():
    with pytest.raises(AttributeError, match="no attribute 'nothing_here'"):
        mo.nothing_here  # noqa: B018


def test_dataset_metrics_without_a_name_or_clashing_with_a_model_metric_are_left_out():
    doc = yaml.safe_load(_ossie())
    doc["metrics"] = [{"name": "net revenue", "expression": {"dialects": [{"dialect": "DAX", "expression": "1"}]}}]
    doc["datasets"][0]["metrics"].append({"expression": {}})  # no name
    doc["datasets"][0]["metrics"].append("not a metric")
    report = []
    out = mo._lift_dataset_metrics(doc, report)
    assert [m["name"] for m in out["metrics"]] == ["net revenue"]
    assert report == [{"object": "dataset 'fact_sales' metric 'net revenue'", "kind": "lost",
                       "message": "a model-level metric has the same name, so this one was left out"}]


def test_sql_that_cant_be_parsed_is_left_as_written_and_no_metrics_leaves_none():
    doc = yaml.safe_load(_ossie())
    doc["datasets"][0]["metrics"] = [{"name": "odd", "expression": {"dialects": [
        {"dialect": "ANSI_SQL", "expression": "SUM(amount"}]}}]
    out = mo._lift_dataset_metrics(doc, [])
    assert out["metrics"][0]["expression"]["dialects"][0]["expression"] == "SUM(amount"
    doc["datasets"][0]["metrics"] = []
    assert "metrics" not in mo._lift_dataset_metrics(doc, [])


def test_a_converter_refusal_is_a_plain_value_error(monkeypatch):
    def refuse(*a, **k):
        raise mo.ConversionError("dataset fact_sales has no fields")

    monkeypatch.setattr(mo, "convert_ossie_to_semantic_model", refuse)
    with pytest.raises(ValueError, match="couldn't be converted: dataset fact_sales has no fields"):
        mo.to_tmsl(_ossie(), SOURCE)


def test_a_model_without_direct_lake_has_no_shared_source():
    assert mo._direct_lake_expression({"tables": [{"name": "t", "partitions": [{"mode": "import"}]}]}) is None


def test_merge_keeps_hierarchies_drops_empty_lists_and_relationships_to_removed_tables():
    current = _bim()
    incoming = {"model": {"tables": [{"name": "dim_customer", "columns": []}]}}
    out = mo.merge(current, incoming, remove=["dim_customer[customer_sk]", "dim_customer[country]",
                                              "dim_customer[city]", "fact_sales"])
    t = out["model"]["tables"]
    assert [x["name"] for x in t] == ["dim_customer"]
    assert "columns" not in t[0] and t[0]["hierarchies"] == current["model"]["tables"][1]["hierarchies"]
    assert "relationships" not in out["model"]
    assert out["name"] == "SM_Gold" and out["compatibilityLevel"] == 1604


def test_merge_adds_named_items_the_file_lacks_to_lists_it_has():
    current = _bim()
    incoming = {"model": {"tables": [], "roles": [{"name": "Everyone", "modelPermission": "read"}]}}
    roles = mo.merge(current, incoming)["model"]["roles"]
    assert [r["name"] for r in roles] == ["Everyone", "EU only"]


def test_nothing_is_removable_from_a_new_model():
    assert mo.removable(None, _bim()) == []
