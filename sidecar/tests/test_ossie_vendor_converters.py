"""Behavioural tests for the vendored Ossie converters (semantic model <-> Apache Ossie)."""

import copy
import json
import warnings

import pytest
import yaml

from fabric_desktop._vendor.ossie_microsoft import (
    OSSIE_VERSION,
    ConversionError,
    build_ossie_document,
    convert_ossie_to_semantic_model,
    convert_semantic_model_to_ossie,
)
from fabric_desktop._vendor.ossie_microsoft import _common as common
from fabric_desktop._vendor.ossie_microsoft import ossie_to_semantic_model as o2s
from fabric_desktop._vendor.ossie_microsoft import semantic_model_to_ossie as s2o


def _collect(fn, *args, **kwargs):
    """Run fn, returning (result, [warning messages])."""
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        result = fn(*args, **kwargs)
    return result, [str(w.message) for w in record]


def _stash(obj):
    return common.read_stash(obj)


def _by_name(items):
    return {item["name"]: item for item in items}


def _entity(name, schema="dbo"):
    return [
        {
            "name": name,
            "mode": "directLake",
            "source": {
                "type": "entity",
                "entityName": name,
                "schemaName": schema,
                "expressionSource": "DatabaseQuery",
            },
        }
    ]


def _retail_bim():
    """A realistic retail model touching most of the TMSL surface the converter maps."""
    return {
        "name": "Retail",
        "compatibilityLevel": 1604,
        "description": "document level note",
        "model": {
            "description": ["Retail sales", "model"],
            "culture": "en-GB",
            "annotations": [
                {"name": "OssieAIContext", "value": '{"purpose": "sales analytics"}'},
                {"name": "PBI_QueryOrder", "value": "[]"},
            ],
            "roles": [{"name": "EU", "modelPermission": "read"}],
            "tables": [
                {
                    "name": "Sales",
                    "description": "Fact table",
                    "lineageTag": "t-sales",
                    "annotations": [
                        {"name": "OssieAIContext", "value": "plain text context"}
                    ],
                    "columns": [
                        {
                            "name": "RowNumber-1",
                            "type": "rowNumber",
                            "dataType": "int64",
                            "isHidden": True,
                        },
                        {
                            "name": "SaleId",
                            "dataType": "int64",
                            "sourceColumn": "sale_id",
                            "isKey": True,
                        },
                        {
                            "name": "CustomerId",
                            "dataType": "int64",
                            "sourceColumn": "customer_id",
                        },
                        {
                            "name": "Amount",
                            "dataType": "decimal",
                            "sourceColumn": "amount",
                            "formatString": "#,0.00",
                        },
                        {
                            "name": "Ratio",
                            "dataType": "double",
                            "sourceColumn": "ratio",
                        },
                        {
                            "name": "OrderDate",
                            "dataType": "dateTime",
                            "sourceColumn": "order_date",
                            "formatString": "Short Date",
                        },
                        {
                            "name": "OrderTs",
                            "dataType": "dateTime",
                            "sourceColumn": "order_ts",
                        },
                        {
                            "name": "Flag",
                            "dataType": "boolean",
                            "sourceColumn": "flag",
                            "isUnique": False,
                        },
                        {"name": "Blob", "dataType": "binary", "sourceColumn": "blob"},
                        {
                            "name": "Odd Name",
                            "dataType": "string",
                            "sourceColumn": "Odd Name",
                        },
                        {
                            "name": "Hour",
                            "dataType": "int64",
                            "sourceColumn": "hour",
                            "dataCategory": "Time",
                        },
                        {
                            "name": "Double Amount",
                            "type": "calculated",
                            "dataType": "decimal",
                            "expression": ["'Sales'[Amount]", " * 2"],
                            "description": ["Twice", "the amount"],
                        },
                        "not-a-column",
                        {"description": "nameless"},
                    ],
                    "measures": [
                        {
                            "name": "Total Amount",
                            "expression": "SUM('Sales'[Amount])",
                            "formatString": "#,0",
                            "dataType": "decimal",
                            "description": "Sum of amount",
                            "annotations": [
                                {"name": "OssieAIContext", "value": "[1, 2]"}
                            ],
                        },
                        {"name": "Avg Amount", "expression": "AVERAGE(Sales[Amount])"},
                        {
                            "name": "Rows",
                            "expression": "COUNTROWS('Sales')",
                            "kpi": {"targetExpression": "100"},
                        },
                        {"name": "Empty", "expression": "   "},
                        {"name": "NoExpr"},
                        "junk",
                    ],
                    "partitions": _entity("sales"),
                },
                {
                    "name": "Customer",
                    "columns": [
                        {
                            "name": "CustomerId",
                            "dataType": "int64",
                            "sourceColumn": "customer_id",
                            "isKey": True,
                        },
                        {
                            "name": "Email",
                            "dataType": "string",
                            "sourceColumn": "email",
                            "isUnique": True,
                        },
                        {
                            "name": "Name",
                            "dataType": "string",
                            "sourceColumn": "name",
                            "sortByColumn": "Email",
                        },
                        {
                            "name": "Auto",
                            "dataType": "automatic",
                            "sourceColumn": "auto",
                        },
                        {
                            "name": "Weird",
                            "dataType": "mystery",
                            "sourceColumn": "weird",
                        },
                    ],
                    "measures": [
                        {"name": "Total Amount", "expression": "SUM(Sales[Amount])"}
                    ],
                    "hierarchies": [{"name": "H", "levels": []}],
                    "partitions": [
                        {
                            "name": "p",
                            "mode": "import",
                            "source": {
                                "type": "m",
                                "expression": [
                                    "let",
                                    '    Source = Sql.Database("srv", "warehouse"),',
                                    '    Nav = Source{[Schema="crm",Item="customer"]}[Data]',
                                    "in",
                                    "    Nav",
                                ],
                            },
                        }
                    ],
                },
                {
                    "name": "Measures",
                    "columns": [],
                    "measures": [
                        {"name": "Margin", "expression": "[Total Amount] * 0.1"}
                    ],
                    "partitions": [
                        {
                            "name": "Measures",
                            "source": {
                                "type": "calculated",
                                "expression": 'ROW("x", 1)',
                            },
                        }
                    ],
                },
                {
                    "name": "Secret",
                    "isPrivate": True,
                    "columns": [{"name": "c", "dataType": "string"}],
                },
                {"name": "LocalDateTable_123", "columns": []},
                {
                    "name": "TimeCalc",
                    "calculationGroup": {"calculationItems": []},
                    "columns": [],
                },
                {"nameless": True},
            ],
            "relationships": [
                {
                    "name": "rel1",
                    "fromTable": "Sales",
                    "fromColumn": "CustomerId",
                    "toTable": "Customer",
                    "toColumn": "CustomerId",
                    "crossFilteringBehavior": "bothDirections",
                },
                {
                    "name": "flipped",
                    "fromTable": "Customer",
                    "fromColumn": "CustomerId",
                    "toTable": "Sales",
                    "toColumn": "SaleId",
                    "fromCardinality": "One",
                    "toCardinality": "many",
                    "isActive": True,
                },
                {
                    "name": "inactive",
                    "fromTable": "Sales",
                    "fromColumn": "OrderDate",
                    "toTable": "Customer",
                    "toColumn": "Email",
                    "isActive": False,
                },
                {
                    "name": "m2m",
                    "fromTable": "Sales",
                    "fromColumn": "Amount",
                    "toTable": "Customer",
                    "toColumn": "Name",
                    "fromCardinality": "many",
                    "toCardinality": "many",
                },
                {
                    "name": "toPrivate",
                    "fromTable": "Sales",
                    "fromColumn": "SaleId",
                    "toTable": "Secret",
                    "toColumn": "c",
                },
                {"name": "half", "fromTable": "Sales"},
                {
                    "name": "dup",
                    "fromTable": "Sales",
                    "fromColumn": "CustomerId",
                    "toTable": "Customer",
                    "toColumn": "CustomerId",
                },
                "junk",
            ],
        },
    }


def test_retail_model_to_ossie_maps_core_concepts():
    bim = _retail_bim()
    original = copy.deepcopy(bim)
    doc, messages = _collect(build_ossie_document, bim)
    assert bim == original  # input is not mutated

    assert doc["version"] == OSSIE_VERSION
    assert doc["name"] == "Retail"
    assert doc["description"] == "Retail sales\nmodel"
    assert doc["ai_context"] == {"purpose": "sales analytics"}
    datasets = _by_name(doc["datasets"])
    assert list(datasets) == ["Sales", "Customer"]

    sales = datasets["Sales"]
    assert sales["source"] == "dbo.sales"
    assert sales["description"] == "Fact table"
    assert sales["ai_context"] == "plain text context"
    assert sales["primary_key"] == ["SaleId"]
    fields = _by_name(sales["fields"])
    assert "RowNumber-1" not in fields
    assert fields["SaleId"]["expression"] == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "sale_id"}]
    }
    assert fields["Amount"]["datatype"] == "Decimal"
    assert fields["Ratio"]["datatype"] == "Float"
    assert fields["OrderDate"]["datatype"] == "Date"
    assert fields["OrderDate"]["dimension"] == {"is_time": True}
    assert fields["OrderTs"]["datatype"] == "DateTime"
    assert fields["Hour"]["dimension"] == {"is_time": True}
    assert fields["Blob"]["datatype"] == "Opaque"
    assert _stash(fields["Blob"])["dataType"] == "binary"
    assert _stash(fields["Flag"])["isUnique"] is False
    assert _stash(fields["Odd Name"])["sourceColumn"] == "Odd Name"
    calc = fields["Double Amount"]
    assert calc["expression"] == {
        "dialects": [{"dialect": "DAX", "expression": "'Sales'[Amount]\n * 2"}]
    }
    assert calc["description"] == "Twice\nthe amount"
    assert "type" not in _stash(calc)
    assert _stash(sales)["excludedColumns"][0]["name"] == "RowNumber-1"

    customer = datasets["Customer"]
    assert customer["source"] == "warehouse.crm.customer"
    assert customer["unique_keys"] == [["Email"]]
    cfields = _by_name(customer["fields"])
    assert "datatype" not in cfields["Auto"]
    assert "datatype" not in cfields["Weird"]

    metrics = _by_name(doc["metrics"])
    total = metrics["Total Amount"]
    assert total["expression"] == {
        "dialects": [{"dialect": "DAX", "expression": "SUM('Sales'[Amount])"}]
    }
    assert total["description"] == "Sum of amount"
    assert total["datatype"] == "Decimal"
    assert total["ai_context"] == [1, 2]
    assert _stash(total) == {
        "table": "Sales",
        "formatString": "#,0",
        "dataType": "decimal",
    }
    dup = metrics["Customer.Total Amount"]
    assert _stash(dup) == {"table": "Customer", "name": "Total Amount"}
    assert _stash(metrics["Margin"])["table"] == "Measures"
    assert "Empty" not in metrics and "NoExpr" not in metrics

    rels = _by_name(doc["relationships"])
    assert set(rels) == {
        "Sales_CustomerId_to_Customer_CustomerId",
        "Sales_SaleId_to_Customer_CustomerId",
    }
    flipped = rels["Sales_SaleId_to_Customer_CustomerId"]
    assert flipped["from"] == "Sales" and flipped["to"] == "Customer"
    assert _stash(flipped) == {
        "name": "flipped",
        "fromCardinality": "One",
        "toCardinality": "many",
        "isActive": True,
        "flipped": True,
        "normalizedEndpoints": ["Sales", "SaleId", "Customer", "CustomerId"],
    }

    model_stash = _stash(doc)
    assert [t["name"] for t in model_stash["excludedTables"]] == [
        "Measures",
        "Secret",
        "LocalDateTable_123",
        "TimeCalc",
    ]
    # Converted measures of an excluded table are not stashed twice.
    assert "measures" not in model_stash["excludedTables"][0]
    assert {r["name"] for r in model_stash["excludedRelationships"]} == {
        "inactive",
        "m2m",
        "toPrivate",
        "half",
        "dup",
    }
    assert {m["measure"]["name"] for m in model_stash["excludedMeasures"]} == {
        "Empty",
        "NoExpr",
    }
    assert model_stash["document"] == {
        "compatibilityLevel": 1604,
        "description": "document level note",
    }
    assert model_stash["roles"] == [{"name": "EU", "modelPermission": "read"}]
    assert model_stash["annotations"] == [{"name": "PBI_QueryOrder", "value": "[]"}]

    joined = "\n".join(messages)
    for expected in [
        "row-level security roles ('roles')",
        "hierarchies ('hierarchies')",
        "a sort-by-column ('sortByColumn')",
        "a KPI ('kpi')",
        "data type 'automatic' carries no type information",
        "unrecognized TMSL data type 'mystery'",
        "'binary' has no portable equivalent; mapped to 'Opaque'",
        "duplicate measure name; renamed to 'Customer.Total Amount'",
        "measure has no expression; excluded",
        "relationship is missing an endpoint; skipped",
        "inactive relationships have no Apache Ossie counterpart",
        "many-to-many relationships have no Apache Ossie counterpart",
        "relationship references a table that is not exported",
        "duplicate relationship 'Sales_CustomerId_to_Customer_CustomerId'; skipped",
        "[table 'Secret'] table is private",
        "[table 'LocalDateTable_123'] table is an auto-generated date table",
        "[table 'TimeCalc'] calculation groups are not converted",
        (
            "[table 'Measures'] calculated tables are not converted to Apache Ossie; excluded from the "
            "Apache Ossie model as a dataset; its measures are exported"
        ),
    ]:
        assert expected in joined, expected


def test_retail_model_round_trips_back_to_power_bi():
    bim = _retail_bim()
    yaml_text, _ = _collect(convert_semantic_model_to_ossie, bim)
    assert yaml_text.startswith("version: ")
    assert "|" in yaml_text  # multi-line DAX is a literal block
    back, messages = _collect(convert_ossie_to_semantic_model, yaml_text)

    assert back["name"] == "Retail"
    assert back["compatibilityLevel"] == 1604
    assert back["description"] == "document level note"
    model = back["model"]
    assert model["description"] == "Retail sales\nmodel"
    assert model["culture"] == "en-GB"
    assert model["roles"] == [{"name": "EU", "modelPermission": "read"}]
    ai = [a for a in model["annotations"] if a["name"] == "OssieAIContext"]
    assert json.loads(ai[0]["value"]) == {"purpose": "sales analytics"}

    tables = _by_name(model["tables"])
    assert list(tables) == [
        "Sales",
        "Customer",
        "Measures",
        "Secret",
        "LocalDateTable_123",
        "TimeCalc",
    ]
    sales = tables["Sales"]
    assert sales["partitions"] == _entity("sales")
    cols = _by_name(sales["columns"])
    assert sales["columns"][0]["name"] == "RowNumber-1"
    assert cols["SaleId"] == {
        "name": "SaleId",
        "sourceColumn": "sale_id",
        "dataType": "int64",
        "isKey": True,
    }
    assert cols["OrderDate"]["formatString"] == "Short Date"
    assert cols["OrderDate"]["dataType"] == "dateTime"
    assert cols["Blob"]["dataType"] == "binary"
    assert cols["Odd Name"]["sourceColumn"] == "Odd Name"
    assert cols["Flag"]["isUnique"] is False
    assert cols["Double Amount"]["type"] == "calculated"
    assert cols["Double Amount"]["expression"] == ["'Sales'[Amount]", " * 2"]

    measures = _by_name(sales["measures"])
    assert measures["Total Amount"]["expression"] == "SUM('Sales'[Amount])"
    assert measures["Total Amount"]["formatString"] == "#,0"
    assert [m["name"] for m in sales["measures"]] == [
        "Total Amount",
        "Avg Amount",
        "Rows",
        "Empty",
        "NoExpr",
    ]
    assert (
        _by_name(tables["Customer"]["measures"])["Total Amount"]["expression"]
        == "SUM(Sales[Amount])"
    )
    assert tables["Customer"]["partitions"][0]["mode"] == "import"
    assert _by_name(tables["Customer"]["columns"])["Email"]["isUnique"] is True
    assert tables["Measures"]["measures"] == [
        {"name": "Margin", "expression": "[Total Amount] * 0.1"}
    ]

    rels = _by_name(model["relationships"])
    assert (
        rels["flipped"]["fromTable"] == "Customer"
        and rels["flipped"]["toTable"] == "Sales"
    )
    assert rels["flipped"]["fromCardinality"] == "One"
    assert rels["rel1"]["crossFilteringBehavior"] == "bothDirections"
    assert {"inactive", "m2m", "toPrivate", "half", "dup"} <= set(rels)
    assert "expressions" not in model  # no generated Direct Lake partitions


def test_translate_dax_measures_to_ossie_sql():
    bim = _retail_bim()
    doc, messages = _collect(build_ossie_document, bim, translate_dax=True)
    metrics = _by_name(doc["metrics"])
    assert metrics["Total Amount"]["expression"]["dialects"] == [
        {"dialect": "OSSIE_SQL_2026", "expression": "SUM(Sales.Amount)"},
        {"dialect": "DAX", "expression": "SUM('Sales'[Amount])"},
    ]
    assert (
        metrics["Avg Amount"]["expression"]["dialects"][0]["expression"]
        == "AVG(Sales.Amount)"
    )
    assert metrics["Rows"]["expression"] == {
        "dialects": [{"dialect": "DAX", "expression": "COUNTROWS('Sales')"}]
    }
    assert any(
        "DAX measure kept as-is; not translated to OSSIE_SQL_2026 (COUNTROWS" in m
        for m in messages
    )


def test_field_resolver_ignores_nameless_entries():
    resolve = s2o._build_field_resolver(
        [{"fields": [{"name": "x"}]}, {"name": "T", "fields": [{}, {"name": "c"}]}]
    )
    assert resolve("T", "c") == ("T", "c")
    assert resolve("T", "x") is None
    assert s2o._build_field_resolver(None)("a", "b") is None


def test_as_tmsl_input_forms():
    bim = {
        "model": {
            "tables": [{"name": "T", "columns": [{"name": "a", "dataType": "string"}]}]
        }
    }
    text_out = convert_semantic_model_to_ossie("﻿  " + json.dumps(bim))
    assert yaml.safe_load(text_out)["name"] == "semantic_model"
    with pytest.raises(TypeError, match="semantic_model must be"):
        convert_semantic_model_to_ossie(42)
    with pytest.raises(ValueError, match="TMDL input needs"):
        convert_semantic_model_to_ossie("table Sales\n  column a")


def test_build_ossie_document_rejects_bad_input():
    with pytest.raises(TypeError, match="bim_file must be"):
        build_ossie_document("x")
    with pytest.raises(ValueError, match="missing the required 'model'"):
        build_ossie_document({"model": []})
    with pytest.raises(ValueError, match="no tables that can be exported"):
        build_ossie_document({"model": {"tables": [{"name": "s", "isPrivate": True}]}})


def test_document_description_only_is_restored_on_the_document():
    bim = {
        "name": "M",
        "description": "from document",
        "model": {"tables": [{"name": "T", "columns": []}]},
    }
    doc = build_ossie_document(bim)
    assert doc["description"] == "from document"
    assert _stash(doc)["descriptionSource"] == "document"
    back, _ = _collect(convert_ossie_to_semantic_model, doc)
    assert back["description"] == "from document"
    assert "description" not in back["model"]
    assert "fields" not in doc["datasets"][0]


@pytest.mark.parametrize(
    ("partitions", "source"),
    [
        (
            [{"source": {"type": "query", "query": ["SELECT *", " FROM t "]}}],
            "SELECT *\n FROM t",
        ),
        ([{"source": {"type": "entity", "entityName": "e"}}], "e"),
        ([{"source": {"type": "m", "expression": 'Source{[Item="tbl"]}'}}], "tbl"),
        (
            [
                {
                    "source": {
                        "type": "m",
                        "expression": 'Source{[Schema="s",Item="tbl"]}',
                    }
                }
            ],
            "s.tbl",
        ),
        ([{"source": {"type": "m", "expression": "let x = 1 in x"}}], "T"),
        (["junk", {"source": "junk"}, {"source": {"type": "query"}}], "T"),
        (
            [
                {
                    "source": {
                        "type": "m",
                        "expression": 'GoogleBigQuery.Database(){[Name="proj"]}[Data]{[Name="ds",Kind="Schema"]}'
                        '[Data]{[Kind="View",Name="v"]}[Data]{[Other="x"]}',
                    }
                }
            ],
            "proj.ds.v",
        ),
        (
            [
                {
                    "source": {
                        "type": "m",
                        "expression": 'GoogleBigQuery.Database(){[Name="t",Kind="Table"]}',
                    }
                }
            ],
            "t",
        ),
        (
            [
                {
                    "source": {
                        "type": "m",
                        "expression": 'GoogleBigQuery.Database(){[Name="proj"]}',
                    }
                }
            ],
            "T",
        ),
    ],
)
def test_table_source_from_partitions(partitions, source):
    assert s2o._table_source({"name": "T", "partitions": partitions}) == source


def test_relationship_without_name_and_metadata_round_trip():
    bim = {
        "model": {
            "tables": [
                {"name": "A", "columns": [{"name": "k", "dataType": "int64"}]},
                {"name": "B", "columns": [{"name": "k", "dataType": "int64"}]},
            ],
            "relationships": [
                {"fromTable": "A", "fromColumn": "k", "toTable": "B", "toColumn": "k"}
            ],
        }
    }
    doc = build_ossie_document(bim)
    rel = doc["relationships"][0]
    assert rel["name"] == "A_k_to_B_k"
    assert "custom_extensions" not in rel
    back, _ = _collect(convert_ossie_to_semantic_model, doc)
    # TMSL requires a relationship name, so the generated Ossie name is kept.
    assert back["model"]["relationships"] == [
        {
            "name": "A_k_to_B_k",
            "fromTable": "A",
            "fromColumn": "k",
            "toTable": "B",
            "toColumn": "k",
        }
    ]


def test_decode_ai_context_and_split_edge_cases():
    assert s2o._decode_ai_context({"a": 1}) == {"a": 1}
    assert s2o._decode_ai_context("not json") == "not json"
    assert s2o._decode_ai_context("5") == "5"
    ai, stash = s2o._split_ai_context(
        {
            "annotations": [
                {"name": "OssieAIContext"},
                {"name": "OssieAIContext", "value": "x"},
            ]
        },
        set(),
    )
    assert ai == "x" and "annotations" not in stash
    ai, stash = s2o._split_ai_context({"annotations": "oops"}, set())
    assert ai is None and stash == {"annotations": "oops"}


def test_strip_converted_measures_keeps_non_list_and_partial_tables():
    tables = [
        {"name": "X", "measures": "oops"},
        {"name": "Y", "measures": [{"name": "keep"}, {"name": "gone"}, "junk"]},
        {"name": "Z", "measures": [{"name": "other"}]},
    ]
    metric = {}
    common.write_stash(metric, {"table": "Y", "name": "GONE"})
    metric["name"] = "renamed"
    result = s2o._strip_converted_measures(tables, [metric, {"name": "no-stash"}])
    assert result[0] == {"name": "X", "measures": "oops"}
    assert result[1]["measures"] == [{"name": "keep"}, "junk"]
    assert result[2] is tables[2]
    assert s2o._strip_converted_measures(tables, []) is tables


def test_dump_yaml_uses_literal_blocks_for_multiline():
    out = s2o.dump_yaml({"a": "one\ntwo", "b": "x"})
    assert out == "a: |-\n  one\n  two\nb: x\n"


# ---------------------------------------------------------------------------
# Ossie -> semantic model (documents written by other tools)
# ---------------------------------------------------------------------------


def _sql(expression, dialect="ANSI_SQL"):
    return {"dialects": [{"dialect": dialect, "expression": expression}]}


def _orders_doc():
    return {
        "version": OSSIE_VERSION,
        "name": "orders",
        "description": "Orders model",
        "ai_context": {"synonyms": ["sales"]},
        "label": "Orders",
        "custom_extensions": [{"vendor_name": "DBT", "data": "{}"}],
        "datasets": [
            {
                "name": "orders",
                "source": "analytics.dbo.orders",
                "description": "Order facts",
                "ai_context": "orders context",
                "primary_key": ["order_id"],
                "unique_keys": [["order_ref"], ["a", "b"], "junk"],
                "fields": [
                    {
                        "name": "order_id",
                        "expression": _sql("order_id"),
                        "datatype": "Integer",
                    },
                    {
                        "name": "order_ref",
                        "expression": _sql('"order_ref"'),
                        "datatype": "String",
                    },
                    {
                        "name": "amount",
                        "expression": _sql("amt"),
                        "datatype": "Decimal",
                        "description": "Amount",
                    },
                    {"name": "customer_id", "datatype": "Integer"},
                    {
                        "name": "ordered_on",
                        "expression": _sql("ordered_on"),
                        "datatype": "Date",
                    },
                    {
                        "name": "ordered_at",
                        "expression": _sql("ordered_at"),
                        "datatype": "Time",
                    },
                    {
                        "name": "ordered_tz",
                        "expression": _sql("ordered_tz"),
                        "datatype": "DateTimeTz",
                    },
                    {"name": "raw", "expression": _sql("raw"), "datatype": "Opaque"},
                    {
                        "name": "weird",
                        "expression": _sql("weird"),
                        "datatype": "Quantum",
                    },
                    {
                        "name": "first",
                        "expression": _sql("first_name"),
                        "datatype": "String",
                    },
                    {
                        "name": "last",
                        "expression": _sql("last_name"),
                        "datatype": "String",
                    },
                    {
                        "name": "full_name",
                        "expression": _sql("first_name || ' ' || last_name"),
                        "datatype": "String",
                        "ai_context": {"role": "display"},
                    },
                    {"name": "bad_concat", "expression": _sql("first_name || 1")},
                    {"name": "dax_col", "expression": _sql("[amount] * 2", "DAX")},
                    {
                        "name": "dax_multi",
                        "expression": _sql("VAR x = 1\nRETURN x", "DAX"),
                    },
                    {
                        "name": "mdx",
                        "expression": _sql("[Measures].[X]", "MDX"),
                        "label": "M",
                    },
                    {
                        "name": "snow",
                        "expression": _sql("UPPER(first_name)", "SNOWFLAKE"),
                    },
                    "junk",
                    {"description": "nameless"},
                ],
            },
            {
                "name": "customers",
                "source": "SELECT * FROM crm.customers;",
                "primary_key": ["customer_id", "region"],
                "fields": [
                    {
                        "name": "customer_id",
                        "expression": _sql("customer_id"),
                        "datatype": "Integer",
                    },
                    {
                        "name": "region",
                        "expression": _sql("region"),
                        "datatype": "String",
                    },
                ],
            },
            {
                "name": "regions",
                "source": "[geo].[region list]",
                "fields": [{"name": "region"}],
            },
        ],
        "relationships": [
            {
                "name": "orders_to_customers",
                "from": "orders",
                "to": "customers",
                "from_columns": ["customer_id"],
                "to_columns": ["customer_id"],
                "ai_context": "join",
            },
            {
                "name": "composite",
                "from": "orders",
                "to": "customers",
                "from_columns": ["a", "b"],
                "to_columns": ["c", "d"],
            },
            {
                "name": "ghost_table",
                "from": "nope",
                "to": "customers",
                "from_columns": ["x"],
                "to_columns": ["customer_id"],
            },
            {
                "name": "ghost_col",
                "from": "orders",
                "to": "customers",
                "from_columns": ["nope"],
                "to_columns": ["customer_id"],
            },
            "junk",
        ],
        "metrics": [
            {
                "name": "revenue",
                "expression": _sql("SUM(amt)"),
                "description": "Revenue",
                "datatype": "Decimal",
            },
            {"name": "order_count", "expression": _sql("COUNT(*)")},
            {
                "name": "avg_order",
                "expression": _sql("SUM(amt) / NULLIF(COUNT(order_id), 0)"),
            },
            {"name": "ambiguous", "expression": _sql("SUM(customer_id)")},
            {
                "name": "dax_metric",
                "expression": _sql("SUM(orders[amount])\n+ 0", "DAX"),
            },
            {"name": "no_expr"},
            "junk",
        ],
    }


def test_foreign_ossie_document_to_direct_lake_model():
    source = {"workspaceId": "ws-1", "itemId": "item-1"}
    bim, messages = _collect(
        convert_ossie_to_semantic_model, yaml.safe_dump(_orders_doc()), source
    )

    assert bim["name"] == "orders"
    assert bim["compatibilityLevel"] == 1702  # raised for Direct Lake
    model = bim["model"]
    assert model["description"] == "Orders model"
    assert model["culture"] == "en-US"
    assert json.loads(model["annotations"][0]["value"]) == {"synonyms": ["sales"]}
    assert model["expressions"] == [
        {
            "name": "DatabaseQuery",
            "kind": "m",
            "expression": [
                "let",
                '    Source = AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/ws-1/item-1")',
                "in",
                "    Source",
            ],
        }
    ]

    tables = _by_name(model["tables"])
    orders = tables["orders"]
    assert orders["description"] == "Order facts"
    assert orders["partitions"] == [
        {
            "name": "orders",
            "mode": "directLake",
            "source": {
                "type": "entity",
                "entityName": "orders",
                "schemaName": "dbo",
                "expressionSource": "DatabaseQuery",
            },
        }
    ]
    cols = _by_name(orders["columns"])
    assert cols["order_id"] == {
        "name": "order_id",
        "sourceColumn": "order_id",
        "dataType": "int64",
        "isKey": True,
    }
    assert cols["order_ref"]["sourceColumn"] == "order_ref"
    assert cols["order_ref"]["isUnique"] is True
    assert (
        cols["amount"]["sourceColumn"] == "amt"
        and cols["amount"]["description"] == "Amount"
    )
    assert cols["customer_id"]["sourceColumn"] == "customer_id"
    assert cols["ordered_on"]["formatString"] == "yyyy-mm-dd"
    assert cols["ordered_at"]["dataType"] == "dateTime"
    assert "dataType" not in cols["raw"] and "dataType" not in cols["weird"]
    full = cols["full_name"]
    assert full["type"] == "calculated"
    assert full["expression"] == "'orders'[first] & \" \" & 'orders'[last]"
    notes = {a["name"]: a["value"] for a in full["annotations"]}
    assert notes["OssieExpressionDialect"] == "ANSI_SQL"
    assert notes["OssieExpression"] == "first_name || ' ' || last_name"
    assert json.loads(notes["OssieAIContext"]) == {"role": "display"}
    assert cols["bad_concat"]["expression"] == "BLANK()"
    assert cols["dax_col"] == {
        "name": "dax_col",
        "type": "calculated",
        "expression": "[amount] * 2",
    }
    assert cols["dax_multi"]["expression"] == ["VAR x = 1", "RETURN x"]
    assert cols["mdx"]["expression"] == "BLANK()"
    assert cols["snow"]["expression"] == "BLANK()"
    assert {a["value"] for a in cols["snow"]["annotations"]} >= {"SNOWFLAKE"}

    customers = tables["customers"]
    assert customers["partitions"][0]["mode"] == "import"
    m = customers["partitions"][0]["source"]["expression"]
    assert (
        m[1]
        == '    Source = Sql.Database("localhost", "database", [Query="SELECT * FROM crm.customers"])'
    )
    assert not any(c.get("isKey") for c in customers["columns"])
    regions = tables["regions"]
    assert regions["partitions"][0]["source"]["entityName"] == "region list"
    assert regions["partitions"][0]["source"]["schemaName"] == "geo"

    measures = _by_name(orders["measures"])
    assert measures["revenue"]["expression"] == "SUM('orders'[amount])"
    assert measures["revenue"]["description"] == "Revenue"
    assert {a["name"]: a["value"] for a in measures["revenue"]["annotations"]} == {
        "OssieExpressionDialect": "ANSI_SQL",
        "OssieExpression": "SUM(amt)",
    }
    # COUNT(*) needs exactly one table; this model has three.
    assert measures["order_count"]["expression"] == "BLANK()"
    assert (
        measures["avg_order"]["expression"]
        == "DIVIDE(SUM('orders'[amount]), COUNTA('orders'[order_id]))"
    )
    assert measures["ambiguous"]["expression"] == "BLANK()"
    assert measures["dax_metric"]["expression"] == ["SUM(orders[amount])", "+ 0"]
    assert "no_expr" not in measures

    rels = model["relationships"]
    assert rels == [
        {
            "name": "orders_to_customers",
            "fromTable": "orders",
            "fromColumn": "customer_id",
            "toTable": "customers",
            "toColumn": "customer_id",
            "annotations": [{"name": "OssieAIContext", "value": "join"}],
        }
    ]

    joined = "\n".join(messages)
    for expected in [
        "custom_extensions for vendor 'DBT' have no Power BI equivalent",
        "[model] a display label ('label') has no Power BI counterpart",
        "Power BI has no composite key; primary key (customer_id, region)",
        "Power BI has no composite unique constraint; unique key (a, b)",
        "'Opaque' has no Power BI equivalent",
        "unrecognized Apache Ossie data type 'Quantum'",
        "Power BI has no time-only data type",
        "the UTC offset is lost",
        "Direct Lake cannot read a query source; using an import partition",
        "a 'MDX' expression could not be translated to DAX ('MDX' is not a SQL dialect",
        "metric has no expression; skipped",
        "no home table recorded; the measure is placed on 'orders'",
        "datatype 'Decimal' is not applied",
        "composite relationships have no equivalent",
        "table 'nope' is not in the model",
        "column 'orders'[nope] is not in the model",
    ]:
        assert expected in joined, expected


def test_single_dataset_count_star_and_placeholder_source():
    doc = {
        "name": "one",
        "version": "9.9.9",
        "datasets": [
            {
                "name": "t",
                "source": "t",
                "fields": [{"name": "x", "expression": _sql("x")}],
            }
        ],
        "metrics": [{"name": "rows", "expression": _sql("COUNT(*)", "OSSIE_SQL_2026")}],
    }
    bim, messages = _collect(convert_ossie_to_semantic_model, doc)
    assert bim["model"]["tables"][0]["measures"][0]["expression"] == "COUNTROWS('t')"
    expr = bim["model"]["expressions"][0]["expression"][1]
    assert (
        "00000000-0000-0000-0000-000000000000/00000000-0000-0000-0000-000000000000"
        in expr
    )
    joined = "\n".join(messages)
    assert "document targets Apache Ossie spec 9.9.9" in joined
    assert (
        "no workspaceId/itemId given; the Direct Lake source uses placeholder ids"
        in joined
    )


@pytest.mark.parametrize(
    ("document", "error", "match"),
    [
        ("- a list", TypeError, "input must be Apache Ossie YAML"),
        ({"semantic_model": {}}, ValueError, "Legacy 'semantic_model'"),
        ({"name": "x", "dialects": []}, ValueError, "Root dialects and vendors"),
        ({"datasets": [{"name": "t"}]}, ValueError, "requires 'name' and 'datasets'"),
        ({"name": "x", "datasets": []}, ValueError, "must be a non-empty list"),
        (
            {"name": "x", "datasets": [{"source": "t"}]},
            ValueError,
            "must be a non-empty list",
        ),
    ],
)
def test_convert_ossie_rejects_malformed_documents(document, error, match):
    with pytest.raises(error, match=match):
        convert_ossie_to_semantic_model(document)


def test_output_format_validation():
    doc = {"name": "x", "datasets": [{"name": "t", "source": "t"}]}
    with pytest.raises(ValueError, match="output_format must be"):
        convert_ossie_to_semantic_model(doc, output_format="xml")
    with pytest.warns(UserWarning):
        with pytest.raises(ValueError, match="TMDL output needs"):
            convert_ossie_to_semantic_model(doc, output_format="tmdl")


def test_direct_lake_requires_integer_compatibility_level():
    doc = {"name": "x", "datasets": [{"name": "t", "source": "t"}]}
    common.write_stash(doc, {"document": {"compatibilityLevel": "1600"}})
    with pytest.warns(UserWarning):
        with pytest.raises(
            ConversionError, match="'compatibilityLevel' must be an integer"
        ):
            convert_ossie_to_semantic_model(doc)


def test_direct_lake_source_must_be_mapping():
    doc = {"name": "x", "datasets": [{"name": "t", "source": "t"}]}
    with pytest.raises(TypeError, match="source must be a mapping"):
        convert_ossie_to_semantic_model(doc, source=["ws"])
    with pytest.raises(TypeError, match="source must be a mapping"):
        o2s._direct_lake_expression("ws")


def test_preserved_direct_lake_expression_reuse_and_rename():
    preserved = [
        {
            "name": "databasequery",
            "kind": "m",
            "expression": ["let", "    Source = X", "in", "    Source"],
        },
        "junk",
    ]
    exprs, name = o2s._merge_direct_lake_expressions(preserved, None)
    assert name == "databasequery" and exprs[0] is preserved[0]

    # An explicit source that differs gets a fresh, uniquely named expression.
    exprs, name = _collect(
        o2s._merge_direct_lake_expressions,
        preserved,
        {"workspaceId": "w", "itemId": "i"},
    )[0]
    assert name == "DatabaseQuery_1"
    assert exprs[-1]["name"] == "DatabaseQuery_1"
    assert "/w/i" in exprs[-1]["expression"][1]

    # The same explicit source matches and is reused.
    same = o2s._direct_lake_expression({"workspaceId": "w", "itemId": "i"})
    exprs, name = o2s._merge_direct_lake_expressions(
        [same], {"workspaceId": "w", "itemId": "i"}
    )
    assert name == "DatabaseQuery" and len(exprs) == 1

    # A non-M expression with that name is not reused.
    exprs, name = _collect(
        o2s._merge_direct_lake_expressions,
        [{"name": "DatabaseQuery", "kind": "calc"}],
        {},
    )[0]
    assert name == "DatabaseQuery_1"
    assert o2s._tmsl_expression_text("a") == "a"


@pytest.mark.parametrize(
    ("source", "parts"),
    [
        ("dbo.t", ["dbo", "t"]),
        ("db.dbo.t", ["db", "dbo", "t"]),
        ('"my schema"."my.table"', ["my schema", "my.table"]),
        ("`a`.b", ["a", "b"]),
        ("a.b.c.d", None),
        ("with x as (select 1) select * from x", None),
        ("1bad", None),
        ("a..b", None),
        ('""', None),
    ],
)
def test_table_reference_parts(source, parts):
    assert o2s._table_reference_parts(source) == parts


def test_m_expression_for_table_reference_pads_parts():
    m = o2s._m_expression("orders")
    assert 'Sql.Database("localhost", "database")' in m
    assert 'Source{[Schema="dbo", Item="orders"]}[Data]' in m
    assert o2s._unquote(" [x] ") == "x"
    assert o2s._m_string('a"b') == '"a""b"'


def test_excluded_measure_restoration_edge_cases():
    tables = [{"name": "T", "measures": [{"name": "Existing"}]}]
    excluded = [
        "junk",
        {"table": "Missing", "measure": {"name": "m"}},
        {"table": "T", "measure": {}},
        {"table": "T", "measure": {"name": "existing"}},
        {"table": "T", "measure": {"name": "AtStart"}, "index": 0},
        {"table": "T", "measure": {"name": "BadIndex"}, "index": True},
        {"table": "T", "measure": {"name": "Negative"}, "index": -1},
    ]
    _, messages = _collect(o2s._restore_excluded_measures, tables, excluded)
    assert [m["name"] for m in tables[0]["measures"]] == [
        "AtStart",
        "Existing",
        "BadIndex",
        "Negative",
    ]
    joined = "\n".join(messages)
    assert "home table 'Missing' is missing" in joined
    assert "the preserved measure has no name" in joined


def test_edited_relationship_drops_stale_endpoint_metadata():
    bim = _retail_bim()
    doc, _ = _collect(build_ossie_document, bim)
    rels = _by_name(doc["relationships"])
    flipped = rels["Sales_SaleId_to_Customer_CustomerId"]
    flipped["from_columns"] = ["CustomerId"]  # someone edited the endpoints
    flipped["name"] = "edited"
    back, _ = _collect(convert_ossie_to_semantic_model, doc)
    out = _by_name(back["model"]["relationships"])["edited"]
    # Orientation and cardinalities belonged to the old endpoints, so they are not replayed.
    assert out["fromTable"] == "Sales" and out["fromColumn"] == "CustomerId"
    assert "fromCardinality" not in out and "isActive" not in out


def test_legacy_relationship_stash_checked_against_generated_name():
    doc = {
        "name": "x",
        "datasets": [
            {"name": "A", "source": "A", "fields": [{"name": "k"}]},
            {"name": "B", "source": "B", "fields": [{"name": "k"}]},
        ],
        "relationships": [
            {
                "name": "A_k_to_B_k",
                "from": "A",
                "to": "B",
                "from_columns": ["k"],
                "to_columns": ["k"],
            },
            {
                "name": "custom",
                "from": "A",
                "to": "B",
                "from_columns": ["k"],
                "to_columns": ["k"],
            },
        ],
    }
    for rel in doc["relationships"]:
        common.write_stash(
            rel, {"name": "orig", "toCardinality": "one", "isActive": True}
        )
    bim, _ = _collect(convert_ossie_to_semantic_model, doc)
    first, second = bim["model"]["relationships"]
    assert first["name"] == "orig" and first["toCardinality"] == "one"
    # A renamed legacy relationship keeps its name, and cardinality is guarded as stale.
    assert second["name"] == "custom"
    assert "toCardinality" not in second and second["isActive"] is True


def test_column_datatype_prefers_edited_core_type():
    field = {"name": "c", "datatype": "String"}
    assert o2s._column_datatype(field, {"dataType": "binary"}, "s") == "string"
    assert (
        o2s._column_datatype(
            {"name": "c", "datatype": "Opaque"}, {"dataType": "variant"}, "s"
        )
        == "variant"
    )


def test_preferred_expression_falls_back_to_sorted_dialects():
    assert o2s._preferred_expression({"TABLEAU": "x", "SNOWFLAKE": "y"}) == (
        "SNOWFLAKE",
        "y",
    )
    assert o2s._preferred_expression({"TABLEAU": "x", "MAQL": "y"}) == ("MAQL", "y")


def test_dataset_column_index_drops_ambiguous_aliases():
    dataset = {
        "name": "T",
        "fields": [
            {"name": "a", "expression": _sql("shared")},
            {"name": "b", "expression": _sql("shared")},
            {"name": "c", "expression": _sql("x + 1")},
            {"name": "d", "expression": _sql("c", "DAX")},
            "junk",
            {"name": "e"},
        ],
    }
    index = o2s._dataset_column_index(dataset)
    assert "shared" not in index and "t.shared" not in index
    assert index["a"] == ("T", "a")
    assert index["t.e"] == ("T", "e")
    assert "x + 1" not in index


def test_set_annotation_replaces_existing_value():
    target = {"annotations": ["junk", {"name": "OssieExpression", "value": "old"}]}
    o2s._set_annotation(target, "OssieExpression", 5)
    assert target["annotations"][1] == {"name": "OssieExpression", "value": "5"}
    o2s._apply_ai_context(target, None)
    assert len(target["annotations"]) == 2
