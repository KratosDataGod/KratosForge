"""model_directlake.py's remaining paths: an expression source among others or of neither kind, a guardrail
whose number is unknown, and rows with no key but none unmatched. Pure checks on a small model.bim."""

from __future__ import annotations

from fabric_desktop import model_directlake as dl

SQL = 'let\n    database = Sql.Database("abc", "SQL1")\nin\n    database'
ONELAKE = ('let\n    Source = AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/'
           '11111111-2222-3333-4444-555555555555/AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE", [HierarchicalNavigation=true])'
           '\nin\n    Source')


def _bim(*expressions):
    def table(name, entity, cols):
        return {"name": name, "columns": [{"name": c, "dataType": "int64", "sourceColumn": c} for c in cols],
                "partitions": [{"mode": "directLake", "source": {"type": "entity", "entityName": entity,
                                                                 "expressionSource": "DatabaseQuery"}}]}
    return {"model": {"tables": [table("Fact", "fact", ["k"]), table("Dim", "dim", ["k"])],
                      "relationships": [{"fromTable": "Fact", "fromColumn": "k", "toTable": "Dim", "toColumn": "k"}],
                      "expressions": list(expressions)}}


def test_source_skips_other_expressions_and_reads_the_named_one():
    bim = _bim({"name": "Other", "expression": SQL}, {"name": "DatabaseQuery", "expression": ONELAKE.split("\n")})
    assert dl.source(bim, "DatabaseQuery") == {"kind": "onelake", "workspace": "11111111-2222-3333-4444-555555555555",
                                               "item": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
    assert dl.source(bim, "Other") == {"kind": "sql", "endpoint": "sql1"}


def test_an_expression_of_neither_kind_and_one_not_there_have_no_source():
    bim = _bim({"name": "DatabaseQuery", "expression": 'let x = Csv.Document("a") in x'},
               {"name": "Empty", "expression": None})
    assert dl.source(bim, "DatabaseQuery") is None
    assert dl.source(bim, "Empty") is None
    assert dl.source(bim, "Missing") is None
    assert dl.source({}, "DatabaseQuery") is None


def test_unknown_row_groups_and_rows_have_no_guardrail_finding():
    stats = {"Fact": dl.TableStats([("k", "long")], rows=None, files=1, bytes=10, row_groups=None),
             "Dim": dl.TableStats([("k", "long")], rows=None, files=1, bytes=10, row_groups=None)}
    out = dl.check(_bim({"name": "DatabaseQuery", "expression": SQL}), stats, dl.limits("F2"))
    assert out["findings"] == []
    assert out["storage"] == "sql"
    t = dl.ModelTable("T", "", "t", "")
    assert dl._guardrail(t, "Rows", "rows", None, 10, "F2", "past") is None


def test_rows_with_no_key_and_none_unmatched_are_reported_alone():
    stats = {"Fact": dl.TableStats([("k", "long")], rows=5, files=1, bytes=10, row_groups=1),
             "Dim": dl.TableStats([("k", "long")], rows=5, files=1, bytes=10, row_groups=1)}
    out = dl.check(_bim({"name": "DatabaseQuery", "expression": SQL}), stats, None,
                   unmatched={"Fact.k": {"rows": 0, "values": 0, "empty": 1}})
    (f,) = out["findings"]
    assert (f["severity"], f["table"], f["check"], f["column"]) == ("info", "Fact", "Rows with no match", "k")
    assert f["message"].startswith("Fact has 1 row with no k. Power BI groups them under (Blank)")
    assert "isn't in" not in f["message"]
