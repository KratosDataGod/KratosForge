"""Ossie version compare (OSSIE-002b, ossie_compare.compare_versions): scenario 14 (Git compare).

Two versions of one model, in any kind KratosForge converts (TMSL, TMDL, Ossie 0.2.0.dev0), give what was added,
removed and changed per object, with what changed; roles and calculation groups are flagged, and the redacted
report never holds a row-level security filter or a role member."""

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


def _row(report, ident):
    hits = [o for o in report["objects"] if o["id"] == ident]
    assert len(hits) == 1, (ident, [o["id"] for o in report["objects"]])
    return hits[0]


def _whats(row):
    return [c["what"] for c in row["changes"]]


def _calc_bim():
    bim, _ = tmdl.to_bim(FILES)
    return bim


def _table(bim, name):
    return next(t for t in bim["model"]["tables"] if t["name"] == name)


# ---- identical versions

@pytest.mark.parametrize("kind", ["bim", "tmdl", "ossie", "ossie_text"])
def test_identical_versions_report_no_changes(kind):
    bim = _bim()
    value = {"bim": bim, "tmdl": tmdl.from_bim(bim)[0], "ossie": _export(bim),
             "ossie_text": model_ossie.export(bim)["yaml"]}[kind]
    report = oc.compare_versions(value, copy.deepcopy(value))
    assert report["status"] == "compared" and report["error"] == ""
    assert report["objects"] == [] and report["look_at"] == []
    assert report["summary"]["changed"] == report["summary"]["added"] == report["summary"]["removed"] == 0
    assert report["summary"]["unchanged"] == 12
    assert report["summary"]["text"] == "No changes; 12 unchanged"


def test_a_model_and_its_ossie_export_are_the_same_version():
    bim = _bim()
    for left, right in ((bim, _export(bim)), (tmdl.from_bim(bim)[0], _export(bim, translate_dax=False))):
        report = oc.compare_versions(left, right)
        assert report["objects"] == [], report["objects"]


# ---- added and removed, each kind

def _add_table(bim):
    bim["model"]["tables"].append({"name": "dim_date", "columns": [{"name": "d", "dataType": "dateTime"}]})


def _add_column(bim):
    _table(bim, "dim_customer")["columns"].append({"name": "zip", "dataType": "string"})


def _add_measure(bim):
    _table(bim, "fact_sales")["measures"].append({"name": "max amount", "expression": "MAX('fact_sales'[amount])"})


def _add_relationship(bim):
    _add_table(bim)
    _table(bim, "fact_sales")["columns"].append({"name": "d", "dataType": "dateTime"})
    bim["model"]["relationships"].append({"name": "r2", "fromTable": "fact_sales", "fromColumn": "d",
                                          "toTable": "dim_date", "toColumn": "d"})


def _add_role(bim):
    bim["model"]["roles"].append({"name": "All", "modelPermission": "read"})


ADDS = [("table", _add_table, "table 'dim_date'"),
        ("column", _add_column, "table 'dim_customer' column 'zip'"),
        ("measure", _add_measure, "table 'fact_sales' measure 'max amount'"),
        ("relationship", _add_relationship, "relationship 'r2'"),
        ("role", _add_role, "role 'All'")]


@pytest.mark.parametrize(("kind", "edit", "ident"), ADDS, ids=[a[0] for a in ADDS])
def test_an_added_object_is_added_and_the_reverse_is_removed(kind, edit, ident):
    old, new = _bim(), _bim()
    edit(new)
    forward = oc.compare_versions(old, new)
    assert _row(forward, ident)["status"] == oc.ADDED
    assert forward["summary"]["by_kind"][kind]["added"] == 1
    back = oc.compare_versions(new, old)
    assert _row(back, ident)["status"] == oc.REMOVED
    assert back["summary"]["by_kind"][kind]["removed"] == 1
    assert {o["status"] for o in forward["objects"]} == {oc.ADDED}


def test_a_calculation_group_added_and_removed_is_flagged():
    with_group = _calc_bim()
    without = copy.deepcopy(with_group)
    without["model"]["tables"] = [t for t in without["model"]["tables"] if t["name"] != "Calc"]
    report = oc.compare_versions(without, with_group)
    group = _row(report, "table 'Calc'")
    assert (group["kind"], group["status"], group["security"]) == ("calculation_group", oc.ADDED, True)
    assert any(r["id"] == "table 'Calc'" for r in report["look_at"])
    assert _row(oc.compare_versions(with_group, without), "table 'Calc'")["status"] == oc.REMOVED


# ---- changed, each kind

def test_a_column_type_change_is_one_type_change():
    new = _bim()
    _table(new, "dim_customer")["columns"][1]["dataType"] = "int64"
    row = _row(oc.compare_versions(_bim(), new), "table 'dim_customer' column 'country'")
    assert row["status"] == oc.CHANGED and _whats(row) == ["type"]
    assert "string" in row["changes"][0]["detail"] and "int64" in row["changes"][0]["detail"]


def test_a_measure_expression_change_is_one_row_with_the_form_change():
    new = _bim()
    _table(new, "fact_sales")["measures"][1]["expression"] = "SUM('fact_sales'[customer_sk])"
    report = oc.compare_versions(_bim(), new)
    assert report["summary"]["changed"] == 1 and len(report["objects"]) == 1
    row = _row(report, "table 'fact_sales' measure 'fact_sales total amount'")
    assert _whats(row) == ["expression"]
    assert "column fact_sales[amount] in the old version, fact_sales[customer_sk] in the new version" \
        in row["changes"][0]["detail"]


def test_a_rewrite_with_the_same_form_is_told_apart_from_a_new_computation():
    new = _bim()
    _table(new, "fact_sales")["measures"][1]["expression"] = "SUM ( fact_sales[amount] )"
    row = _row(oc.compare_versions(_bim(), new), "table 'fact_sales' measure 'fact_sales total amount'")
    assert _whats(row) == ["expression text"]
    assert "canonical form (L2) is the same" in row["changes"][0]["detail"]


def test_a_format_only_change_is_a_format_change_not_an_expression_change():
    new = _bim()
    _table(new, "fact_sales")["measures"][1]["formatString"] = "#,0"
    _table(new, "fact_sales")["columns"][1]["formatString"] = "0.0"
    report = oc.compare_versions(_bim(), new)
    assert _whats(_row(report, "table 'fact_sales' measure 'fact_sales total amount'")) == ["format"]
    assert _whats(_row(report, "table 'fact_sales' column 'amount'")) == ["format"]


def test_an_expression_outside_the_l2_subset_is_still_a_change_with_the_reason():
    new = _bim()
    _table(new, "fact_sales")["measures"][0]["expression"] = "CALCULATE(COUNTROWS('fact_sales'))"
    row = _row(oc.compare_versions(_bim(), new), "table 'fact_sales' measure 'fact_sales rows'")
    assert _whats(row) == ["expression"] and "not compared as a form" in row["changes"][0]["detail"]


def test_a_description_change_is_listed_without_its_text():
    new = _bim()
    _table(new, "dim_customer")["description"] = "Customers, secret note"
    row = _row(oc.compare_versions(_bim(), new), "table 'dim_customer'")
    assert row["changes"] == [{"what": "description", "detail": "description added",
                               "detail_redacted": "description added"}]


def test_a_relationship_change_lists_what_changed():
    new = _bim()
    new["model"]["relationships"][0]["isActive"] = False
    row = _row(oc.compare_versions(_bim(), new), "relationship 'r1'")
    assert row["status"] == oc.CHANGED and row["changes"][0]["detail"] == "active flag changed from true to false"


def test_a_calculation_item_change_is_flagged_and_redacted_without_its_expression():
    old = _calc_bim()
    new = copy.deepcopy(old)
    item = _table(new, "Calc")["calculationGroup"]["calculationItems"][0]
    item["expression"] = "CALCULATE(SELECTEDMEASURE(), SECRETFILTER)"
    report = oc.compare_versions(old, new)
    row = _row(report, "table 'Calc'")
    assert row["security"] and _whats(row) == ["calculation item"]
    assert [r["id"] for r in report["look_at"]] == ["table 'Calc'"]
    safe = json.dumps(oc.redacted(report))
    assert "SECRETFILTER" not in safe and "calculation item 'YTD' expression changed" in safe


# ---- security: roles

def test_a_role_filter_change_is_flagged_and_never_leaks_in_the_redacted_report():
    old, new = _bim(), _bim()
    old["model"]["roles"][0]["members"] = [{"memberName": "alice@contoso.com", "identityProvider": "AzureAD"}]
    new["model"]["roles"][0]["members"] = [{"memberName": "bob@contoso.com", "identityProvider": "AzureAD"}]
    new["model"]["roles"][0]["tablePermissions"][0]["filterExpression"] = '[country] = "TOPSECRET"'
    report = oc.compare_versions(old, new)
    row = _row(report, "role 'EU only'")
    assert row["status"] == oc.CHANGED and row["security"]
    assert report["look_at"] == [{"id": "role 'EU only'", "kind": "role", "status": oc.CHANGED,
                                  "why": oc._LOOK_AT_WHY["role"]}]
    assert "TOPSECRET" in json.dumps(report)  # the local pane may show it
    safe = oc.redacted(report)
    text = json.dumps(safe)
    for secret in ("TOPSECRET", "DE", "alice", "bob", "contoso"):
        assert secret not in text, secret
    assert safe["redacted"] is True and "role 'EU only'" in text
    assert any("sha256" in c["detail"] for c in _row(safe, "role 'EU only'")["changes"])
    assert all("detail_redacted" not in c for o in safe["objects"] for c in o["changes"])
    assert report == oc.compare_versions(old, new)  # redacted() left the report as it was


def test_redacted_fingerprints_expression_forms():
    new = _bim()
    _table(new, "fact_sales")["measures"][1]["expression"] = "SUM('fact_sales'[customer_sk])"
    change = _row(oc.redacted(oc.compare_versions(_bim(), new)),
                  "table 'fact_sales' measure 'fact_sales total amount'")["changes"][0]
    assert all("sha256" in v for v in change["forms"].values())


# ---- bad input, refused versions

@pytest.mark.parametrize("bad", [None, 5, "{{{", "", [1, 2], {"model": 5}, {"version": 3}, {"a.tmdl": 5},
                                 "x: [unclosed"], ids=lambda v: type(v).__name__ + str(len(str(v))))
def test_bad_input_never_raises_and_says_why(bad):
    for report in (oc.compare_versions(bad, _bim()), oc.compare_versions(_bim(), bad)):
        assert report["status"] == "failed" and report["error"]
        assert report["objects"] == [] and report["summary"]["unchanged"] == 0
        safe = oc.redacted(report)
        assert safe["error"] and "could not be read" in safe["error"]


def test_ossie_03_and_snowflake_011_are_refused_with_a_plain_reason():
    doc = _export(_bim())
    v03 = dict(doc, version="0.3.0.dev0")
    wrapped = {"version": "0.1.1", "semantic_model": [{k: v for k, v in doc.items() if k != "version"}]}
    for other, label in ((v03, "Ossie 0.3.0.dev0"), (wrapped, "Snowflake's Ossie 0.1.1")):
        report = oc.compare_versions(doc, other)
        assert report["status"] == "refused" and report["objects"] == []
        assert report["sides"]["new"]["refused"].startswith(f"This file is {label}")
        assert "Version compare reads Ossie 0.2.0.dev0" in report["error"]
        assert oc.redacted(report)["error"] == report["error"]
        assert oc.compare_versions(other, doc)["sides"]["old"]["refused"]


# ---- the report

def test_the_report_states_its_known_gaps_and_ignores_a_rename_and_column_order():
    new = _bim()
    new["name"] = "Renamed"
    cols = _table(new, "dim_customer")["columns"]
    cols.reverse()
    report = oc.compare_versions(_bim(), new)
    assert report["objects"] == []
    gaps = {n["what"] for n in report["not_checked"]}
    assert {"model rename", "column order"} <= gaps


def test_the_same_inputs_give_the_same_report():
    old, new = _bim(), _bim()
    for edit in (_add_table, _add_measure, _add_role):
        edit(new)
    new["model"]["relationships"][0]["isActive"] = False
    first = oc.compare_versions(old, new)
    assert json.dumps(first, sort_keys=True) == json.dumps(oc.compare_versions(copy.deepcopy(old), new),
                                                            sort_keys=True)
    assert json.dumps(oc.redacted(first)) == json.dumps(oc.redacted(first))
    assert first["summary"]["text"] == "4 added, 0 removed, 1 changed; 11 unchanged"


def test_a_line_the_reader_leaves_out_is_named_never_a_silent_no_changes():
    files = dict(FILES)
    table = next(k for k in files if "tables/" in k)
    new = {**files, table: files[table].rstrip("\n") + "\n\tfrobnicate the widget\n"}
    r = oc.compare_versions(files, new)
    assert r["status"] == "compared" and r["sides"]["new"]["warnings_total"] == 1
    assert "left out" in r["summary"]["text"] and any("left out" in n for n in r["notes"])


@pytest.mark.parametrize("old", [None, "{{{"], ids=["none", "bad-json"])
def test_a_failed_compare_never_summarizes_as_no_changes(old):
    r = oc.compare_versions(old, FILES)
    assert r["status"] != "compared" and r["summary"]["text"].startswith("Not compared")
    assert "No changes" not in r["summary"]["text"] and "No changes" not in oc.redacted(r)["summary"]["text"]
