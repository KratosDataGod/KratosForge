"""Mutation batch 65: the Direct Lake check's pure parts. Every SKU's guardrails, which partitions and
relationships count, where a table is, calculated columns on SQL and OneLake, a column read by its name,
the exact edges of a limit and the model size, the order of findings, and singular counts."""

from __future__ import annotations

import copy

from fabric_desktop import model_directlake as dl
from tests.test_model_directlake import _bim, _by, _stats

GB = 1024 ** 3
ONELAKE = ('AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/'
           '11111111-1111-1111-1111-111111111111/22222222-2222-2222-2222-222222222222")')
FACT_COLS = [("customer_sk", "long"), ("amount", "double"), ("discount_pct", "double")]


def test_every_skus_guardrails_are_microsofts():
    small = (1_000, 1_000, 300_000_000)
    assert dl.GUARDRAILS == {
        "F2": (*small, 10 * GB), "F4": (*small, 10 * GB), "F8": (*small, 10 * GB),
        "F16": (*small, 20 * GB), "F32": (*small, 40 * GB),
        "F64": (5_000, 5_000, 1_500_000_000, None), "F128": (5_000, 5_000, 3_000_000_000, None),
        "F256": (5_000, 5_000, 6_000_000_000, None), "F512": (10_000, 10_000, 12_000_000_000, None),
        "F1024": (10_000, 10_000, 24_000_000_000, None), "F2048": (10_000, 10_000, 24_000_000_000, None),
        "F4096": (10_000, 10_000, 24_000_000_000, None), "F8192": (10_000, 10_000, 24_000_000_000, None)}
    assert dl.limits(" p5 ") == dl.limits("F1024") | {} and dl.limits("FT1")["sku"] == "F64"


def test_table_stats_and_guardrails_dont_say_about_unless_estimated():
    assert dl.TableStats([], rows=1, files=1, bytes=1, row_groups=1).row_groups_estimated is False
    t = dl.ModelTable("T", "", "t", "E")
    f = dl._guardrail(t, "Parquet files", "Parquet files", 2_000, 1_000, "F2", "Past.")
    assert f["message"] == "2,000 Parquet files, but F2 allows 1,000 a table. Past."


def test_files_and_rows_are_never_shown_as_estimates():
    stats = _stats(FactSales=dl.TableStats(FACT_COLS, rows=400_000_000, files=2_000, bytes=1, row_groups=1,
                                           row_groups_estimated=True))
    out = dl.check(_bim(), stats, dl.limits("F32"))
    assert _by(out, "Parquet files")[0]["message"].startswith("2,000 Parquet files,")
    assert _by(out, "Rows")[0]["message"].startswith("400,000,000 rows,")


def test_a_partition_that_isnt_an_entity_or_has_no_entity_name_isnt_read():
    bim = _bim()
    bim["model"]["tables"][0]["partitions"][0]["source"]["type"] = "query"
    bim["model"]["tables"][1]["partitions"][0]["source"]["entityName"] = ""
    assert dl.direct_lake_tables(bim) == []


def test_a_relationship_needs_both_tables():
    bim = _bim()
    bim["model"]["relationships"] += [{"fromTable": "FactSales", "fromColumn": "a", "toColumn": "b"},
                                      {"fromColumn": "a", "toTable": "DimCustomer", "toColumn": "b"}]
    assert dl.relationships(bim) == [{"from_table": "FactSales", "from_column": "customer_sk",
                                      "to_table": "DimCustomer", "to_column": "customer_sk"}]


def test_a_table_is_named_with_its_schema_only_when_it_has_one():
    bim = _bim()
    del bim["model"]["tables"][1]["partitions"][0]["source"]["schemaName"]
    out = dl.check(bim, _stats(), None)
    assert [(t["table"], t["delta_table"], t["exists"]) for t in out["tables"]] == [
        ("FactSales", "gold.fact_sales", True), ("DimCustomer", "dim_customer", True)]
    assert dl.check(bim, {"DimCustomer": None}, None)["tables"] == [
        {"table": "DimCustomer", "delta_table": "dim_customer", "exists": False}]


def test_a_calculated_column_fails_on_sql_but_not_on_onelake():
    bim = _bim()
    bim["model"]["tables"][1]["columns"].append({"name": "Label", "dataType": "string", "type": "calculated"})
    assert [f["column"] for f in _by(dl.check(bim, _stats(), None), "Calculated column")] == ["Label"]
    lake = _bim(source=ONELAKE)
    lake["model"]["tables"][1]["columns"].append({"name": "Label", "dataType": "string", "type": "calculated"})
    out = dl.check(lake, _stats(), None)
    assert not _by(out, "Calculated column") and out["storage"] == "onelake"
    assert dl.check(_bim(), _stats(), None)["storage"] == "sql"


def test_a_column_with_no_source_is_read_by_its_name_and_one_with_a_source_by_that():
    bim = _bim()
    cols = bim["model"]["tables"][1]["columns"]
    cols[1] = {"name": "country", "dataType": "string"}          # no sourceColumn: read as "country"
    cols.append({"name": "Region", "dataType": "string", "sourceColumn": "region"})
    stats = _stats(DimCustomer=dl.TableStats([("customer_sk", "long"), ("country", "string"), ("region", "string"),
                                              ("Region", "string")], rows=1, files=1, bytes=1, row_groups=1))
    out = dl.check(bim, stats, None)
    assert not [f for f in _by(out, "Column missing") if f["table"] == "DimCustomer"]
    assert [f["column"] for f in _by(out, "New column") if f["table"] == "DimCustomer"] == ["Region"]


def test_at_a_limit_is_near_it_and_at_ninety_percent_is_near_too():
    stats = _stats(FactSales=dl.TableStats(FACT_COLS, rows=1, files=1_000, bytes=1, row_groups=900))
    out = dl.check(_bim(), stats, dl.limits("F2"))
    assert "a few more loads could pass it" in _by(out, "Parquet files")[0]["message"]
    assert "a few more loads could pass it" in _by(out, "Row groups")[0]["message"]
    below = _stats(FactSales=dl.TableStats(FACT_COLS, rows=1, files=899, bytes=1, row_groups=1))
    assert not _by(dl.check(_bim(), below, dl.limits("F2")), "Parquet files")


def test_model_size_at_the_limit_is_fine_and_its_bytes_are_the_tables_sum():
    stats = _stats(FactSales=dl.TableStats(FACT_COLS, rows=1, files=1, bytes=10 * GB - 500, row_groups=1))
    out = dl.check(_bim(), stats, dl.limits("F2"))
    assert out["model_bytes"] == 10 * GB and not _by(out, "Model size")
    over = _stats(FactSales=dl.TableStats(FACT_COLS, rows=1, files=1, bytes=10 * GB, row_groups=1))
    f = _by(dl.check(_bim(), over, dl.limits("F2")), "Model size")[0]
    assert f["message"].startswith("The model's tables take 10.0 GB on OneLake; F2 allows 10 GB. ")


def test_fails_come_before_warnings_whatever_the_table():
    bim = _bim()
    bim["model"]["tables"][0]["name"] = "ZFact"
    bim["model"]["relationships"][0]["fromTable"] = "ZFact"
    stats = {"ZFact": dl.TableStats(FACT_COLS, rows=1, files=1, bytes=1, row_groups=1),
             "DimCustomer": dl.TableStats([("customer_sk", "long"), ("country", "string")], rows=1, files=2_000,
                                          bytes=1, row_groups=1)}
    out = dl.check(bim, stats, dl.limits("F2"), duplicates={"DimCustomer.customer_sk": 1})
    assert [(f["severity"], f["table"]) for f in out["findings"]] == [("fail", "DimCustomer"), ("warn", "DimCustomer")]
    bim2 = copy.deepcopy(bim)
    bim2["model"]["tables"][0]["columns"].append({"name": "gone", "dataType": "string", "sourceColumn": "gone"})
    out = dl.check(bim2, stats, dl.limits("F2"))
    assert [(f["severity"], f["table"]) for f in out["findings"]] == [("fail", "ZFact"), ("warn", "DimCustomer")]
    assert out["counts"] == {"fail": 1, "warn": 1, "info": 0}


def test_one_duplicate_value_is_singular():
    out = dl.check(_bim(), _stats(), None, duplicates={"DimCustomer.customer_sk": 1})
    assert "has 1 value that appears" not in _by(out, "Key not unique")[0]["message"]
    assert "customer_sk has 1 value that appear more than once" in _by(out, "Key not unique")[0]["message"]
