"""The offline TMDL reader (fabric_desktop/tmdl.py): TMDL as Fabric and Power BI projects keep it,
read into TMSL model.bim with no Fabric call, then exported to Apache Ossie."""

import pytest

from fabric_desktop import model_ossie, semantic, tmdl

T = "\t"
SALES = "\n".join([
    "/// Every order line",
    "table 'Sales ''EU'''",
    f"{T}lineageTag: t-1",
    "",
    f"{T}/// Revenue after discounts",
    f"{T}measure 'Net revenue' =",
    f"{T}{T}{T}CALCULATE(",
    f"{T}{T}{T}    SUM('Sales ''EU'''[amount]),",
    f"{T}{T}{T}    'Sales ''EU'''[status] = \"done\")",
    f"{T}{T}formatString: \"#,0.00\"",
    f"{T}{T}displayFolder: Money",
    f"{T}{T}lineageTag: m-1",
    f"{T}{T}formatStringDefinition = IF(1 > 0, \"#,0\")",
    "",
    f"{T}measure Orders = COUNTROWS('Sales ''EU''')",
    f"{T}{T}isHidden",
    f"{T}{T}changedProperty = IsHidden",
    "",
    f"{T}measure Fenced = ```",
    f"{T}{T}{T}VAR x = 1",
    f"{T}{T}{T}RETURN x",
    f"{T}{T}{T}```",
    "",
    f"{T}column amount",
    f"{T}{T}dataType: double",
    f"{T}{T}sourceColumn: amount",
    f"{T}{T}summarizeBy: sum",
    f"{T}{T}annotation SummarizationSetBy = Automatic",
    "",
    f"{T}column 'Big order' = [amount] > 1000",
    f"{T}{T}dataType: boolean",
    "",
    f"{T}hierarchy 'By date'",
    f"{T}{T}level Year",
    f"{T}{T}{T}column: year",
    f"{T}{T}level Month",
    f"{T}{T}{T}column: month",
    "",
    f"{T}partition Sales = m",
    f"{T}{T}mode: import",
    f"{T}{T}source =",
    f"{T}{T}{T}{T}let",
    f"{T}{T}{T}{T}    Source = Sql.Database(\"s\", \"d\")",
    f"{T}{T}{T}{T}in",
    f"{T}{T}{T}{T}    Source",
    "",
])
DATE = "\n".join([
    "table Calc",
    f"{T}calculationGroup",
    f"{T}{T}precedence: 2",
    f"{T}{T}calculationItem YTD = TOTALYTD(SELECTEDMEASURE(), 'Date'[Date])",
    f"{T}{T}{T}ordinal: 0",
    "",
    f"{T}column Name",
    f"{T}{T}dataType: string",
    f"{T}{T}sourceColumn: Name",
])
FILES = {
    "definition/database.tmdl": "database\n\tcompatibilityLevel: 1605\n",
    "definition/model.tmdl": "model Model\n\tculture: en-US\n\nref table 'Sales ''EU'''\n\nannotation PBI_QueryOrder = [\"Sales\"]\n",
    "definition/tables/Sales.tmdl": SALES,
    "definition/tables/Calc.tmdl": DATE,
    "definition/relationships.tmdl": "\n".join([
        "relationship r-1", f"{T}isActive: false", f"{T}crossFilteringBehavior: bothDirections",
        f"{T}fromColumn: 'Sales ''EU'''.amount", f"{T}toColumn: Calc.Name", ""]),
    "definition/roles/EU.tmdl": "\n".join([
        "role 'EU only'", f"{T}modelPermission: read", "",
        f"{T}tablePermission 'Sales ''EU''' =", f"{T}{T}{T}[status] <> \"void\"", f"{T}{T}{T}&& TRUE()", "",
        f"{T}member 'ana@contoso.com'", f"{T}{T}identityProvider: AzureAD", ""]),
    "definition/perspectives/Finance.tmdl": "\n".join([
        "perspective Finance", f"{T}perspectiveTable 'Sales ''EU'''", f"{T}{T}perspectiveMeasure 'Net revenue'", ""]),
    "definition/cultures/fr-FR.tmdl": "culture fr-FR\n\tlinguisticMetadata =\n\t\t\t{\"Version\": \"1.0\"}\n\t\tcontentType: json\n",
    "definition.pbism": "{}",
}


def _table(bim, name):
    return next(t for t in bim["model"]["tables"] if t["name"] == name)


def test_reads_a_whole_model():
    bim, report = tmdl.to_bim(FILES, "Sales model")
    assert bim["name"] == "Sales model" and bim["compatibilityLevel"] == 1605
    model = bim["model"]
    assert model["culture"] == "en-US"
    assert model["annotations"] == [{"name": "PBI_QueryOrder", "value": '["Sales"]'}]
    sales = _table(bim, "Sales 'EU'")
    assert sales["description"] == "Every order line" and sales["lineageTag"] == "t-1"
    net = sales["measures"][0]
    assert net["name"] == "Net revenue" and net["description"] == "Revenue after discounts"
    assert net["expression"] == ("CALCULATE(\n    SUM('Sales ''EU'''[amount]),\n    'Sales ''EU'''[status] = \"done\")")
    assert net["formatString"] == "#,0.00" and net["displayFolder"] == "Money"
    assert net["formatStringDefinition"] == {"expression": 'IF(1 > 0, "#,0")'}
    orders = sales["measures"][1]
    assert orders["isHidden"] is True and orders["changedProperties"] == [{"property": "IsHidden"}]
    assert sales["measures"][2]["expression"] == "VAR x = 1\nRETURN x"
    amount, big = sales["columns"]
    assert amount["annotations"] == [{"name": "SummarizationSetBy", "value": "Automatic"}]
    assert big == {"name": "Big order", "expression": "[amount] > 1000", "dataType": "boolean", "type": "calculated"}
    assert [(lv["name"], lv["column"], lv["ordinal"]) for lv in sales["hierarchies"][0]["levels"]] == [
        ("Year", "year", 0), ("Month", "month", 1)]
    part = sales["partitions"][0]
    assert part["mode"] == "import" and part["source"]["type"] == "m"
    assert part["source"]["expression"].splitlines() == ["let", '    Source = Sql.Database("s", "d")', "in", "    Source"]
    calc = _table(bim, "Calc")
    assert calc["calculationGroup"]["precedence"] == 2
    assert calc["calculationGroup"]["calculationItems"] == [
        {"name": "YTD", "expression": "TOTALYTD(SELECTEDMEASURE(), 'Date'[Date])", "ordinal": 0}]
    rel = model["relationships"][0]
    assert rel == {"name": "r-1", "isActive": False, "crossFilteringBehavior": "bothDirections",
                   "fromTable": "Sales 'EU'", "fromColumn": "amount", "toTable": "Calc", "toColumn": "Name"}
    role = model["roles"][0]
    assert role["modelPermission"] == "read"
    assert role["tablePermissions"] == [{"name": "Sales 'EU'", "filterExpression": "[status] <> \"void\"\n&& TRUE()"}]
    assert role["members"] == [{"identityProvider": "AzureAD", "memberName": "ana@contoso.com"}]
    assert model["perspectives"] == [{"name": "Finance", "tables": [{"name": "Sales 'EU'", "measures": [{"name": "Net revenue"}]}]}]
    assert "cultures" not in model
    assert [r["kind"] for r in report] == ["lost"] and "translations" in report[0]["message"]


def test_the_model_kratosforge_writes_reads_back_and_exports():
    fact = semantic.ModelTable("fact_sales", "gold.fact_sales", "dbo", "fact_sales",
                               [("customer_sk", "long"), ("amount", "double")], keys=["customer_sk"])
    dim = semantic.ModelTable("dim_customer", "gold.dim_customer", "dbo", "dim_customer",
                              [("customer_sk", "long"), ("name", "string")], surrogate_key="customer_sk", is_dimension=True)
    parts = semantic.build([fact, dim], [semantic.Relationship("fact_sales", "customer_sk", "dim_customer", "customer_sk")],
                           "srv", "ep")
    bim, report = tmdl.to_bim(parts, "SM_Gold")
    assert report == []
    assert bim["model"]["expressions"][0]["expression"].startswith("let\n    database = Sql.Database(")
    src = _table(bim, "fact_sales")["partitions"][0]["source"]
    assert src == {"type": "entity", "entityName": "fact_sales", "schemaName": "dbo", "expressionSource": "DatabaseQuery"}
    out = model_ossie.export(bim)
    assert out["counts"]["datasets"] == 2 and out["counts"]["relationships"] == 1
    assert out["counts"]["portable_metrics"] >= 1  # SUM(fact_sales[amount]) also as SQL


def test_spaces_count_as_indent_and_crlf_is_fine():
    text = "table T\r\n    measure M =\r\n            SUM(T[x])\r\n        formatString: 0\r\n"
    bim, _ = tmdl.to_bim({"definition/tables/T.tmdl": text})
    m = bim["model"]["tables"][0]["measures"][0]
    assert m == {"name": "M", "expression": "SUM(T[x])", "formatString": "0"}


def test_unknown_lines_are_reported_not_dropped():
    text = "table T\n\tsomething odd here\n\t\twith a child\n\tcolumn c\n\t\tdataType: string\n"
    bim, report = tmdl.to_bim({"definition/tables/T.tmdl": text})
    assert bim["model"]["tables"][0]["columns"] == [{"name": "c", "dataType": "string"}]
    assert report == [{"object": "something odd here", "kind": "lost", "message": "not understood, left out (definition/tables/T.tmdl:2)"}]


def test_refuses_what_it_cant_read():
    with pytest.raises(ValueError, match="no .tmdl files"):
        tmdl.to_bim({"definition.pbism": "{}"})
    with pytest.raises(ValueError, match="larger than 20 MB"):
        tmdl.to_bim({"definition/model.tmdl": "x" * (tmdl.MAX_CHARS + 1)})
    with pytest.raises(ValueError, match="not a column reference"):
        tmdl.column_ref("nodot")


def test_a_long_dotted_reference_is_refused_fast():
    import time
    start = time.monotonic()
    rel = "relationship r\n\tfromColumn: " + ".".join(["a"] * 400_000) + "'\n"
    with pytest.raises(ValueError, match="longer than 1,024"):
        tmdl.to_bim({"definition/relationships.tmdl": rel})
    assert tmdl.column_ref("'a.b''c'.'d.e'") == ("a.b'c", "d.e")
    assert tmdl.column_ref("t.'col.x'") == ("t", "col.x")
    for bad in ("'open.col", "'t'col", "t.", ".c"):
        with pytest.raises(ValueError, match="not a column reference"):
            tmdl.column_ref(bad)
    assert time.monotonic() - start < 2


def test_deep_nesting_is_refused_not_a_crash():
    text = "\n".join("\t" * n + f"table t{n}" for n in range(3000))
    with pytest.raises(ValueError, match="nested too deeply"):
        tmdl.to_bim({"definition/tables/t.tmdl": text})
