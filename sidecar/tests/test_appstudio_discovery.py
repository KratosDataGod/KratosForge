"""App Studio: what may be told to a model about a semantic model (AS-09). Names, types and descriptions only."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.appstudio import discovery

PASSWORD = "pass" + "word=Hunter2!"


def bim():
    return {"model": {
        "dataSources": [{"name": "SQL", "connectionString": "Server=prod;User Id=sa;" + PASSWORD}],
        "expressions": [{"name": "DatabaseQuery", "expression": 'Sql.Database("prod.contoso.com", "Sales")'}],
        "roles": [{"name": "Readers", "members": [{"memberName": "someone@contoso.com"}]}],
        "tables": [
            {"name": "Sales", "description": "One row per order line. " + PASSWORD,
             "partitions": [{"name": "p", "source": {"type": "m", "expression": 'Sql.Database("prod.contoso.com")'}}],
             "annotations": [{"name": "secret", "value": "x"}],
             "columns": [{"name": "RowNumber-2662979B", "type": "rowNumber", "dataType": "int64"},
                         {"name": "Amount", "dataType": "decimal", "description": "Net of discounts"},
                         {"name": "CustomerKey", "dataType": "int64", "isHidden": True}],
             "measures": [{"name": "Revenue", "expression": ["SUM(", "  Sales[Amount])"], "formatString": "#,0",
                           "description": "Net revenue"},
                          {"name": "Secret", "expression": "\"token=abcdef123456\""}]},
            {"name": "Customer Region", "columns": [{"name": "Name", "dataType": "string"}], "measures": []}],
        "relationships": [{"fromTable": "Sales", "fromColumn": "CustomerKey", "toTable": "Customer Region",
                           "toColumn": "Name"},
                          {"fromTable": "Sales", "fromColumn": "X", "toTable": "Customer Region", "toColumn": "Y",
                           "isActive": False}]}}


def test_only_names_types_descriptions_and_relationships():
    meta = discovery.from_bim(bim())
    text = json.dumps(meta)
    for leak in ("prod.contoso.com", "Hunter2", "someone@contoso.com", "Sql.Database", "annotations", "RowNumber",
                 "partitions", "SUM("):
        assert leak not in text, leak
    sales = meta["tables"][0]
    assert sales["columns"] == [{"name": "Amount", "type": "decimal", "description": "Net of discounts"},
                                {"name": "CustomerKey", "type": "int64", "hidden": True}]
    assert sales["measures"][0] == {"name": "Revenue", "description": "Net revenue", "format": "#,0"}
    assert meta["relationships"] == [{"from": "Sales[CustomerKey]", "to": "Customer Region[Name]"},
                                     {"from": "Sales[X]", "to": "Customer Region[Y]", "active": False}]
    assert meta["truncated"] is False


def test_expressions_only_when_asked_and_redacted():
    meta = discovery.from_bim(bim(), include_expressions=True)
    measures = meta["tables"][0]["measures"]
    assert measures[0]["expression"] == "SUM(\n  Sales[Amount])"
    assert "abcdef123456" not in measures[1]["expression"]


def test_measure_tables_and_columns():
    meta = discovery.from_bim(bim())
    assert discovery.measure_tables(meta) == {"Revenue": "Sales", "Secret": "Sales"}
    assert ("Customer Region", "Name") in discovery.columns(meta)


def test_caps(monkeypatch):
    monkeypatch.setattr(discovery, "MAX_TABLES", 1)
    assert discovery.from_bim(bim())["truncated"] is True
    big = {"model": {"tables": [{"name": "T", "description": "x" * 5000, "columns": [], "measures": []}]}}
    assert len(discovery.from_bim(big)["tables"][0]["description"]) == discovery.DESCRIPTION_LIMIT


@pytest.mark.parametrize("bad", [{}, {"model": "x"}, []])
def test_not_a_model(bad):
    with pytest.raises(ValueError):
        discovery.from_bim(bad)


def test_junk_entries_are_skipped():
    meta = discovery.from_bim({"model": {"tables": [None, {"name": ""}, {"name": "T", "columns": [1, {"name": "c"}],
                                                                          "measures": ["m"]}],
                                         "relationships": [None, {"fromTable": "T"}]}})
    assert meta["tables"] == [{"name": "T", "columns": [{"name": "c", "type": "string"}], "measures": []}]
    assert meta["relationships"] == []
