"""Import's fidelity report is as honest as Export's about what only Power BI reads (rolefid), and a Semantic
View keeps its relationships in TMDL with its end columns' data types left unknown. Synthetic data only."""

from __future__ import annotations

import copy
import json

import pytest

import ossie_corpus as oc
from fabric_desktop import model_ossie as mo, semantic_matrix as sm, semantic_parity as sp

ROLE, GROUP = "role 'EU readers'", "table 'time intelligence'"
MEMBER = "ana@contoso.example"


def _rich() -> dict:
    bim = copy.deepcopy(oc.load(oc.entry("tmsl/02_rich.bim")))
    bim["model"]["roles"][0]["members"] = [{"memberName": MEMBER}]
    return bim


def _imported(bim: dict) -> tuple[dict, dict]:
    """Export's report of ``bim`` and Import's report of the file Export wrote."""
    out = mo.export(bim, translate_dax=False)
    doc = mo.parse(out["yaml"])
    back, report = mo.to_tmsl(doc)
    return out["fidelity"], mo.fidelity(doc, back, report)


def _obj(fid: dict, name: str) -> dict:
    return next(o for o in fid["objects"] if o["object"] == name)


# ---- roles and calculation groups that live only in the POWER_BI extension

def test_a_role_only_in_the_extension_is_preserved_through_extensions_both_ways():
    exported, imported = _imported(_rich())
    for fid in (exported, imported):
        assert _obj(fid, ROLE)["class"] == "preserved_through_extensions"
        assert _obj(fid, ROLE)["label"] == "Preserved through extensions"
        assert fid["flagged"]["roles"] == [{"object": ROLE, "class": "preserved_through_extensions"}]
    assert mo.RESTORED in _obj(imported, ROLE)["notes"]


def test_import_gives_the_role_the_security_warning_export_gives():
    exported, imported = _imported(_rich())
    assert imported["warnings"][0] == exported["warnings"][0] == {
        "object": ROLE, "severity": "security", "prominent": True,
        "message": "This security role is only in the POWER_BI extension: a tool other than Power BI that reads the "
                   "file won't apply it"}


def test_a_calculation_group_only_in_the_extension_is_preserved_through_extensions_both_ways():
    exported, imported = _imported(_rich())
    for fid in (exported, imported):
        assert _obj(fid, GROUP)["class"] == "preserved_through_extensions"
        assert fid["flagged"]["calculation_groups"] == [{"object": GROUP, "class": "preserved_through_extensions"}]
        assert not [w for w in fid["warnings"] if w["object"] == GROUP]  # as on Export: kept, so no warning


def test_import_remediation_says_the_object_reached_the_model():
    _, imported = _imported(_rich())
    actions = {r["object"]: r["action"] for r in imported["remediation"]}
    assert "it reached the model" in actions[ROLE] and "row-level security" in actions[ROLE]
    assert "it reached the model" in actions[GROUP] and "calculation group" in actions[GROUP]
    assert "comes back on import" not in actions[GROUP]


def test_the_report_carries_no_filter_text_or_member_names():
    _, imported = _imported(_rich())
    text = json.dumps([imported["warnings"], imported["remediation"]])
    assert MEMBER not in text and "[country]" not in text and '"DE"' not in text


def test_a_role_changed_on_import_is_still_partially_converted():
    bim = _rich()
    out = mo.export(bim, translate_dax=False)
    doc = mo.parse(out["yaml"])
    back, report = mo.to_tmsl(doc)
    back["model"]["roles"][0]["members"] = []
    fid = mo.fidelity(doc, back, report)
    assert _obj(fid, ROLE)["class"] == "partially_converted"
    assert fid["warnings"][0]["message"].startswith("This security role came through changed")


def test_a_role_lost_on_import_is_unsupported_and_the_first_warning():
    out = mo.export(_rich(), translate_dax=False)
    doc = mo.parse(out["yaml"])
    back, report = mo.to_tmsl(doc)
    del back["model"]["roles"]
    fid = mo.fidelity(doc, back, report)
    assert _obj(fid, ROLE)["class"] == "unsupported"
    assert fid["warnings"][0]["message"].startswith("This security role is lost")


# ---- the consumers read the class as they did

def test_the_matrix_keeps_the_role_in_power_bi_and_extension_only_in_the_file():
    doc = mo.export(_rich(), translate_dax=False)["yaml"]
    m = sm.compatibility(doc)
    rows = {r["target"]: r for r in m["targets"]}
    power_bi = rows["power-bi-model"]
    assert power_bi["roles"][0]["status"] == power_bi["calculation_groups"][0]["status"] == sm.KEPT
    assert not [w for w in power_bi["warnings"] if w["severity"] == "security"]
    assert rows["ossie-0.2"]["roles"][0]["status"] == sm.EXTENSION_ONLY


def test_the_matrix_flag_reading_depends_on_the_direction():
    _, imported = _imported(_rich())
    exported, _ = _imported(_rich())
    assert sm._flagged(imported)[ROLE] == sm.KEPT
    assert sm._flagged(exported)[ROLE] == sm.EXTENSION_ONLY


def test_parity_still_reads_the_role_as_extension_only_on_the_ossie_side():
    doc = mo.export(_rich(), translate_dax=False)["yaml"]
    report = sp.parity_l1(_rich(), doc, left_kind="tmsl", right_kind="ossie")
    role = next(o for o in report["objects"] if o["kind"] == "role")
    assert role["fidelity"]["right"] == "Preserved through extensions"


# ---- a Semantic View's relationships

VIEW = """version: 0.2.0.dev0
name: v
datasets:
- name: orders
  source: dbo.orders
- name: customers
  source: dbo.customers
relationships:
- name: orders_customers
  from: orders
  to: customers
  from_columns: [customer_id]
  to_columns: [customer_id]
"""


def test_a_view_relationship_declares_its_end_columns_without_a_data_type():
    doc = mo.parse(VIEW)
    before = copy.deepcopy(doc)
    bim, report = mo.to_tmsl(doc)
    assert doc == before  # the caller's document is unchanged
    tables = {t["name"]: t for t in bim["model"]["tables"]}
    for name in ("orders", "customers"):
        assert tables[name]["columns"] == [{"name": "customer_id", "sourceColumn": "customer_id"}]
    assert [(r["fromTable"], r["toTable"]) for r in bim["model"]["relationships"]] == [("orders", "customers")]
    rows = [r for r in report if r["object"] == "relationship 'orders_customers'"]
    assert [r["kind"] for r in rows] == ["kept", "kept"]
    assert "'orders'[customer_id]" in rows[0]["message"] and "'customers'[customer_id]" in rows[1]["message"]
    assert all("its data type wasn't known" in r["message"] and "lists no fields" in r["message"] for r in rows)


def test_a_view_relationship_reaches_tmdl_and_its_fidelity_row_names_the_unknown_types():
    files, report = mo.ossie_to_tmdl(VIEW)
    assert "fromColumn: orders.customer_id" in files["definition/relationships.tmdl"]
    assert not any("dataType" in text for path, text in files.items() if path.startswith("definition/tables/"))
    doc = mo.parse(VIEW)
    bim, rows = mo.to_tmsl(doc)
    rel = _obj(mo.fidelity(doc, bim, rows), "relationship 'orders_customers'")
    assert rel["class"] == "fully_preserved" and len(rel["notes"]) == 2
    assert all("data type wasn't known" in n for n in rel["notes"])


def test_a_dataset_with_fields_but_not_the_column_still_loses_the_relationship():
    text = VIEW.replace("  source: dbo.orders\n", "  source: dbo.orders\n  fields:\n  - name: amount\n")
    bim, report = mo.to_tmsl(text)
    assert "relationships" not in bim["model"]
    assert any(r["object"] == "relationship 'orders_customers'" and r["kind"] == "lost" for r in report)


def test_one_column_is_declared_once_for_two_relationships():
    text = VIEW + """- name: again
  from: orders
  to: customers
  from_columns: [customer_id]
  to_columns: [customer_id]
"""
    assert mo.view_columns(mo.parse(text)) == [("orders", "customer_id", "orders_customers"),
                                                ("customers", "customer_id", "orders_customers")]


@pytest.mark.parametrize("change", ["composite", "unknown_dataset"])
def test_no_column_is_declared_where_no_relationship_could_be_written(change):
    text = (VIEW.replace("[customer_id]", "[customer_id, region]") if change == "composite"
            else VIEW.replace("  to: customers\n", "  to: nowhere\n"))
    declared = mo.view_columns(mo.parse(text))
    assert declared == ([] if change == "composite" else [("orders", "customer_id", "orders_customers")])
    bim, _ = mo.to_tmsl(text)
    assert "relationships" not in bim["model"]


def test_a_column_the_extension_keeps_is_not_declared_again():
    doc = mo.parse(VIEW)
    doc["datasets"][0]["custom_extensions"] = [{"vendor_name": "POWER_BI", "data": json.dumps(
        {"_v": 2, "excludedColumns": [{"name": "customer_id", "dataType": "int64", "sourceColumn": "customer_id"}]})}]
    assert mo.view_columns(doc) == [("customers", "customer_id", "orders_customers")]


def test_the_corpus_view_round_trips_with_its_relationship():
    bim, _ = mo.to_tmsl(oc.load(oc.entry("view/sales_view.yaml")))
    assert mo.round_trip_check(bim)["passed"] is True
    assert len(bim["model"]["relationships"]) == 1
