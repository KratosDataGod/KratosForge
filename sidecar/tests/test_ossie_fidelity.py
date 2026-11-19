"""The five-class fidelity report of a conversion to and from Apache Ossie (OSSIE-004a, model_ossie.fidelity).

Every object the conversion reads appears exactly once, in one class decided by structure (what reached the
output, what sits only in the POWER_BI extension, what is missing); the converter's messages are evidence on the
object, never the class. Required scenarios: 4 (TMDL to Ossie), 5 (Ossie to TMDL), 6 (unsupported metadata
preserved and reported) and 13 (security metadata), and the property that report objects equal input objects."""

import collections
import copy
import json
import logging
import re
from pathlib import Path

import pytest
import yaml

from fabric_desktop import model_ossie, tmdl
from test_model_ossie import SOURCE, FakeFabric, _bim, _ossie, svc  # noqa: F401 - svc is a fixture
from test_tmdl import FILES
from test_tmdl_fuzz import _Gen

PRESERVED_OR_NAMED = {"preserved_through_extensions", "unsupported"}  # D.4: never a silent class


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True  # the converter also logs each warning
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _q(name):
    return f"'{name}'"


def _named(items):
    return [i for i in items or [] if isinstance(i, dict) and isinstance(i.get("name"), str)]


def _stash(obj):
    for e in (obj or {}).get("custom_extensions") or []:
        if e.get("vendor_name") == "POWER_BI":
            return json.loads(e["data"])
    return {}


def _bim_objects(bim):
    """Every object of a model.bim as the report names it, written out here independently of the engine."""
    model = bim["model"]
    out = ["model"]
    for t in _named(model.get("tables")):
        tid = f"table {_q(t['name'])}"
        out.append(tid)
        out += [f"{tid} {kind} {_q(i['name'])}" for key, kind in
                (("columns", "column"), ("measures", "measure"), ("hierarchies", "hierarchy")) for i in _named(t.get(key))]
    out += [f"relationship {_q(r.get('name') or model_ossie._rel_key(r))}" for r in model.get("relationships") or []]
    for key, kind in (("perspectives", "perspective"), ("roles", "role"), ("cultures", "culture"),
                      ("expressions", "expression")):
        out += [f"{kind} {_q(i['name'])}" for i in _named(model.get(key))]
    return out


def _ossie_objects(doc):
    """Every object of an Ossie document, its POWER_BI extensions' objects too."""
    out = ["model"]
    stash = _stash(doc)
    for d in _named(doc.get("datasets")):
        did = f"dataset {_q(d['name'])}"
        out.append(did)
        out += [f"{did} field {_q(f['name'])}" for f in _named(d.get("fields"))]
        ds = _stash(d)
        out += [f"table {_q(d['name'])} column {_q(c['name'])}" for c in _named(ds.get("excludedColumns"))]
        out += [f"table {_q(d['name'])} hierarchy {_q(h['name'])}" for h in _named(ds.get("hierarchies"))]
        out += [f"{did} metric {_q(m['name'])}" for m in _named(d.get("metrics"))]
    out += [f"metric {_q(m['name'])}" for m in _named(doc.get("metrics"))]
    out += [f"relationship {_q(r.get('name') or '<unnamed>')}" for r in doc.get("relationships") or []]
    for key, kind in (("perspectives", "perspective"), ("roles", "role"), ("cultures", "culture"),
                      ("expressions", "expression")):
        out += [f"{kind} {_q(i['name'])}" for i in _named(stash.get(key))]
    for t in _named(stash.get("excludedTables")):
        out.append(f"table {_q(t['name'])}")
        out += [f"table {_q(t['name'])} {kind} {_q(i['name'])}" for key, kind in
                (("columns", "column"), ("measures", "measure"), ("hierarchies", "hierarchy")) for i in _named(t.get(key))]
    out += [f"table {_q(e['table'])} measure {_q(e['measure']['name'])}" for e in stash.get("excludedMeasures") or []]
    out += [f"relationship {_q(r.get('name') or model_ossie._rel_key(r))}" for r in stash.get("excludedRelationships") or []]
    return out


def _ids(report):
    """The report's ids, a repeated name's "#2" suffix taken off so it counts as the name it repeats."""
    return [re.sub(r" #\d+$", "", o["object"]) for o in report["objects"]]


def _assert_every_object_once(report, expected):
    ids = [o["object"] for o in report["objects"]]
    assert len(ids) == len(set(ids)), "an object is listed twice"
    assert collections.Counter(_ids(report)) == collections.Counter(expected)
    assert sum(report["counts"].values()) == len(ids)
    assert sorted(i for v in report["by_class"].values() for i in v) == sorted(ids)
    for o in report["objects"]:
        assert o["class"] in model_ossie.CLASSES and o["label"] == model_ossie.CLASSES[o["class"]]
        assert bool(o["remediation"]) == (o["class"] != "fully_preserved")


def _with_calculation_group(bim):
    bim = copy.deepcopy(bim)
    bim["model"]["tables"].append({"name": "Time calc", "calculationGroup": {"calculationItems": [
        {"name": "YTD", "expression": "CALCULATE(SELECTEDMEASURE(), DATESYTD('Date'[Date]))"}]},
        "columns": [{"name": "Period", "dataType": "string", "sourceColumn": "Name"}],
        "partitions": [{"name": "p", "source": {"type": "calculationGroup"}}]})
    return bim


def _obj(report, ident):
    return next(o for o in report["objects"] if o["object"] == ident)


# ---- the property: report objects equal input objects, over the fuzz generator and the fixtures

def test_every_object_of_a_random_model_appears_exactly_once_both_ways():
    checked = 0
    for seed in range(120):
        bim = _Gen(seed).model()
        if seed % 3 == 0:
            bim = _with_calculation_group(bim)
        out = model_ossie.export(bim, translate_dax=bool(seed % 2))
        _assert_every_object_once(out["fidelity"], _bim_objects(bim))
        for o in out["fidelity"]["objects"]:
            if o["kind"] in ("role", "calculation_group"):
                assert o["class"] in PRESERVED_OR_NAMED, f"seed {seed}: {o}"
        doc = model_ossie.parse(out["yaml"])
        back, report = model_ossie.to_tmsl(out["yaml"])
        imported = model_ossie.fidelity(doc, back, report)
        _assert_every_object_once(imported, _ossie_objects(doc))
        flagged = imported["flagged"]
        assert [r["object"] for r in flagged["roles"]] == [f"role {_q(r['name'])}" for r in bim["model"].get("roles") or []]
        assert len(flagged["calculation_groups"]) == (seed % 3 == 0)
        checked += 1
    assert checked == 120


@pytest.mark.parametrize("bim", [_bim(), _with_calculation_group(_bim()), tmdl.to_bim(FILES, "Sales model")[0]],
                         ids=["fabric", "calculation group", "tmdl"])
def test_every_object_of_the_fixtures_appears_exactly_once(bim):
    out = model_ossie.export(bim)
    _assert_every_object_once(out["fidelity"], _bim_objects(bim))
    back, report = model_ossie.to_tmsl(out["yaml"])
    _assert_every_object_once(model_ossie.fidelity(model_ossie.parse(out["yaml"]), back, report),
                              _ossie_objects(model_ossie.parse(out["yaml"])))


def test_every_object_of_a_file_from_another_tool_appears_exactly_once():
    doc = model_ossie.parse(_ossie())
    bim, report = model_ossie.to_tmsl(_ossie(), SOURCE)
    _assert_every_object_once(model_ossie.fidelity(doc, bim, report), _ossie_objects(doc))


def test_the_report_names_the_vendored_converter_commit():
    readme = (Path(model_ossie.__file__).parent / "_vendor" / "ossie_microsoft" / "README.md").read_text(encoding="utf-8")
    commit = re.search(r"Commit: `([0-9a-f]{40})`", readme).group(1)
    report = model_ossie.export(_bim())["fidelity"]
    assert report["converter"]["commit"] == commit and commit.startswith("82b9eff")
    assert report["converter"]["version"] == model_ossie.OSSIE_VERSION in readme
    assert report["converter"]["name"] == "apache/ossie-converters"


# ---- an informational vendor note is not a loss

def test_a_note_that_power_bi_infers_a_measures_type_is_not_a_loss():
    """The converter says a metric's datatype "is not applied" (Power BI infers a measure's type from its DAX). The
    old rows label that "lost" by substring; the report keeps the measure Fully preserved, with the note as
    evidence and an informational change, and no remediation."""
    metric = {"name": "orders", "datatype": "Decimal",
              "expression": {"dialects": [{"dialect": "DAX", "expression": "COUNTROWS('fact_sales')"}]}}
    text = _ossie(metric)
    bim, report = model_ossie.to_tmsl(text, SOURCE)
    row = next(r for r in report if "infers a measure's data type" in r["message"])
    assert row["kind"] == "lost"  # the old label, kept for the current UI
    fid = model_ossie.fidelity(model_ossie.parse(text), bim, report)
    obj = _obj(fid, "dataset 'fact_sales' metric 'orders'")
    assert obj["class"] == "fully_preserved" and obj["remediation"] == ""
    assert row in obj["evidence"]
    assert obj["changes"] == [] and any("not applied" in n for n in obj["notes"])
    assert {"object": obj["object"], "change": obj["notes"][0], "informational": True} in fid["metadata_changes"]
    assert obj["object"] not in [e["object"] for e in fid["excluded"]]
    assert not any(w["object"] == "metric 'orders'" for w in fid["warnings"])


def test_an_inactive_relationship_the_converter_skips_is_preserved_through_extensions_not_lost():
    bim = _bim()
    bim["model"]["relationships"].append({"name": "r2", "fromTable": "fact_sales", "fromColumn": "customer_sk",
                                          "toTable": "dim_customer", "toColumn": "customer_sk", "isActive": False})
    out = model_ossie.export(bim)
    row = next(r for r in out["report"] if r["object"] == "relationship 'r2'")
    assert row["kind"] == "lost" and "skipped" in row["message"]
    obj = _obj(out["fidelity"], "relationship 'r2'")
    assert obj["class"] == "preserved_through_extensions" and obj["evidence"] == [row]
    back, _ = model_ossie.to_tmsl(out["yaml"])
    assert any(r["name"] == "r2" for r in back["model"]["relationships"])  # it does come back


# ---- scenario 4: TMDL to Ossie

def test_scenario_4_tmdl_to_ossie_reports_every_object_with_a_passing_round_trip():
    out = model_ossie.tmdl_to_ossie(FILES, "Sales model")
    fid = out["fidelity"]
    assert fid["source"]["format"] == "TMSL" and fid["destination"] == {"format": "Apache Ossie",
                                                                         "version": model_ossie.OSSIE_VERSION}
    assert fid["validation"]["check"] == "round_trip" and fid["validation"]["ran"] and fid["validation"]["passed"]
    _assert_every_object_once(fid, _bim_objects(tmdl.to_bim(FILES, "Sales model")[0]))
    assert _obj(fid, "culture 'fr-FR'")["class"] == "preserved_through_extensions"
    assert "round_trip" not in out  # tmdl_to_ossie keeps its rows in the report, as before


def test_scenario_4_a_model_kept_in_git_exports_offline_with_its_fidelity(svc, monkeypatch):
    root = Path(svc._project().root)
    (root / "semantic" / "Old.SemanticModel").mkdir(parents=True)
    (root / "semantic" / "Old.SemanticModel" / "model.bim").write_text(json.dumps(FakeFabric().bim), encoding="utf-8")
    monkeypatch.setattr(svc, "fake", None)  # no Fabric at all
    out = svc.meta_model_ossie_export_folder("semantic/Old.SemanticModel")
    assert out["round_trip"] == [] and out["fidelity"]["validation"]["passed"] is True
    assert _obj(out["fidelity"], "role 'EU only'")["class"] == "preserved_through_extensions"


def test_scenario_4_a_round_trip_difference_is_the_validation_result_on_the_object(monkeypatch):
    bim = _bim()
    monkeypatch.setattr(model_ossie, "round_trip", lambda b: [
        {"object": "table 'fact_sales' column 'amount' formatString", "kind": "lost",
         "message": "doesn't come back from Ossie as it was"}])
    fid = model_ossie.export(bim, validate=True)["fidelity"]
    assert fid["validation"]["passed"] is False
    assert fid["validation"]["objects"] == ["table 'fact_sales' column 'amount'"]
    assert _obj(fid, "table 'fact_sales' column 'amount'")["evidence"][-1]["check"] == "round_trip"


def test_the_fabric_export_has_the_fidelity_report_and_no_new_card(svc):
    out = svc.meta_model_ossie_export("test", "SM_Gold")
    assert out["fidelity"]["validation"]["ran"] and "round_trip" not in out
    assert out["fidelity"]["converter"]["commit"] == model_ossie.CONVERTER["commit"]


# ---- scenario 5: Ossie to TMDL

def test_scenario_5_an_ossie_file_becomes_tmdl_with_each_object_classed(svc, monkeypatch):
    monkeypatch.setattr(svc, "fake", None)
    case = {"name": "repeat rate", "expression": {"dialects": [
        {"dialect": "ANSI_SQL", "expression": "SUM(CASE WHEN amount > 0 THEN 1 ELSE 0 END)"}]}}
    text = _ossie(case)
    plan = svc.meta_model_ossie_to_tmdl(text)["confirm"]
    fid = plan["fidelity"]
    assert fid["source"]["format"] == "Apache Ossie" and fid["destination"]["format"] == "TMSL"
    _assert_every_object_once(fid, _ossie_objects(model_ossie.parse(text)))
    assert _obj(fid, "dataset 'fact_sales' metric 'net revenue'")["class"] == "unverified"  # SQL translated to DAX
    needs = _obj(fid, "dataset 'fact_sales' metric 'repeat rate'")
    assert needs["class"] == "partially_converted" and needs["changes"][0].startswith("needs_dax")
    assert "DAX" in needs["remediation"]
    assert any(r["object"] == needs["object"] for r in fid["remediation"])
    assert _obj(fid, "relationship 'sales_channel'")["class"] == "fully_preserved"
    assert fid["validation"]["ran"] is True
    out = svc.meta_model_ossie_to_tmdl(text, confirmed=True, token=plan["token"])
    assert out["fidelity"] == fid


def test_scenario_5_import_preview_carries_the_fidelity_report(svc):
    out = svc.meta_model_ossie_preview("test", _ossie(), name="SM_Gold", mode="update")
    assert out["report"] is not None and out["fidelity"]["counts"]["fully_preserved"] > 0
    _assert_every_object_once(out["fidelity"], _ossie_objects(model_ossie.parse(_ossie())))


# ---- scenario 6: unsupported metadata, preserved and reported

def test_scenario_6_what_ossie_has_no_place_for_is_preserved_through_extensions_and_named():
    bim = _with_calculation_group(_bim())
    bim["model"]["perspectives"] = [{"name": "Sales view", "tables": [{"name": "fact_sales"}]}]
    bim["model"]["tables"][0]["measures"][0]["kpi"] = {"targetExpression": "1"}
    bim["model"]["tables"][0]["measures"].append({"name": "draft"})  # no expression
    bim["model"]["tables"][0]["columns"].append({"name": "blob", "dataType": "binary", "sourceColumn": "blob"})
    fid = model_ossie.export(bim)["fidelity"]
    for ident in ("table 'Time calc'", "table 'Time calc' column 'Period'", "perspective 'Sales view'",
                  "table 'dim_customer' hierarchy 'Geo'", "table 'fact_sales' measure 'draft'"):
        obj = _obj(fid, ident)
        assert obj["class"] == "preserved_through_extensions" and "POWER_BI extension" in obj["remediation"], ident
        assert {"object": ident, "kind": obj["kind"], "class": obj["class"]} in fid["excluded"]
    assert _obj(fid, "table 'Time calc'")["kind"] == "calculation_group"
    assert fid["flagged"]["calculation_groups"] == [{"object": "table 'Time calc'", "class": "preserved_through_extensions"}]
    kpi = _obj(fid, "table 'fact_sales' measure 'fact_sales rows'")
    assert "kpi" in kpi["in_extension"] and any("KPI" in r["message"] for r in kpi["evidence"])
    blob = _obj(fid, "table 'fact_sales' column 'blob'")
    assert blob["class"] == "partially_converted" and "Opaque" in blob["changes"][0]
    assert {"object": blob["object"], "change": blob["changes"][0]} in fid["metadata_changes"]


def test_scenario_6_what_power_bi_has_no_place_for_is_partially_converted_or_unsupported_by_name():
    doc = yaml.safe_load(_ossie())
    fact, channel = doc["datasets"]
    fact["label"] = "Sales"
    fact["fields"][0]["custom_extensions"] = [{"vendor_name": "SNOWFLAKE", "data": "{}"}]
    fact["fields"].append({"name": "at", "datatype": "DateTimeTz",
                           "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "at"}]}})
    channel["primary_key"] = ["channel_sk", "channel"]
    doc["relationships"].append({"name": "composite", "from": "fact_sales", "to": "dim_channel",
                                 "from_columns": ["channel_sk", "at"], "to_columns": ["channel_sk", "channel"]})
    doc.setdefault("metrics", []).append({"name": "empty"})
    text = yaml.safe_dump(doc, sort_keys=False)
    bim, report = model_ossie.to_tmsl(text, SOURCE)
    fid = model_ossie.fidelity(model_ossie.parse(text), bim, report)
    _assert_every_object_once(fid, _ossie_objects(model_ossie.parse(text)))
    assert _obj(fid, "dataset 'fact_sales'")["class"] == "partially_converted"
    assert "label" in _obj(fid, "dataset 'fact_sales'")["changes"][0]
    assert "SNOWFLAKE" in _obj(fid, "dataset 'fact_sales' field 'customer_sk'")["changes"][0]
    assert "offset is lost" in _obj(fid, "dataset 'fact_sales' field 'at'")["changes"][0]
    assert "composite primary key" in _obj(fid, "dataset 'dim_channel'")["changes"][0]
    for ident in ("relationship 'composite'", "metric 'empty'"):
        obj = _obj(fid, ident)
        assert obj["class"] == "unsupported" and obj["remediation"] and obj["evidence"], ident
        assert {"object": ident, "kind": obj["kind"], "class": "unsupported"} in fid["excluded"]


def test_scenario_6_a_row_that_names_no_object_is_a_warning_not_dropped():
    out = model_ossie.export(_bim())
    rows = out["report"] + [{"object": "Direct Lake", "kind": "lost", "message": "placeholder ids"}]
    fid = model_ossie.fidelity(_bim(), yaml.safe_load(out["yaml"]), rows)
    assert {"object": "Direct Lake", "message": "placeholder ids", "severity": "warning"} in fid["warnings"]
    attached = [r for o in fid["objects"] for r in o["evidence"]]
    assert all(r in attached for r in out["report"])  # every converter row is evidence on an object


# ---- scenario 13: security metadata

def test_scenario_13_a_security_role_is_flagged_and_its_extension_only_life_is_a_security_warning():
    out = model_ossie.export(_bim())
    fid = out["fidelity"]
    role = _obj(fid, "role 'EU only'")
    assert role["class"] == "preserved_through_extensions" and role["flag"] == "security"
    assert any("row-level security" in r["message"] for r in role["evidence"])
    assert "row-level security" in role["remediation"]
    assert fid["warnings"][0] == {"object": "role 'EU only'", "severity": "security", "prominent": True,
                                  "message": fid["warnings"][0]["message"]}
    assert fid["flagged"]["roles"] == [{"object": "role 'EU only'", "class": "preserved_through_extensions"}]
    back, report = model_ossie.to_tmsl(out["yaml"])
    imported = model_ossie.fidelity(model_ossie.parse(out["yaml"]), back, report)
    assert _obj(imported, "role 'EU only'")["class"] == "fully_preserved"
    assert not any(w["severity"] == "security" for w in imported["warnings"])


def test_scenario_13_a_lost_security_role_is_never_unlisted_and_is_the_first_warning():
    bim = _bim()
    doc = yaml.safe_load(model_ossie.export(bim)["yaml"])
    stash = _stash(doc)
    del stash["roles"]  # a converter that dropped the role
    doc["custom_extensions"] = [{"vendor_name": "POWER_BI", "data": json.dumps(stash)}]
    fid = model_ossie.fidelity(bim, doc)
    assert _obj(fid, "role 'EU only'")["class"] == "unsupported"
    first = fid["warnings"][0]
    assert first["object"] == "role 'EU only'" and first["severity"] == "security" and first["prominent"]
    assert "lost" in first["message"]
    # The other way: a file whose extension has the role, written into a model without it.
    text = model_ossie.export(bim)["yaml"]
    back, report = model_ossie.to_tmsl(text)
    del back["model"]["roles"]
    fid = model_ossie.fidelity(model_ossie.parse(text), back, report)
    assert _obj(fid, "role 'EU only'")["class"] == "unsupported"
    assert fid["warnings"][0]["severity"] == "security" and "access" in _obj(fid, "role 'EU only'")["remediation"]


def test_scenario_13_a_lost_calculation_group_is_named_and_warned():
    bim = _with_calculation_group(_bim())
    doc = yaml.safe_load(model_ossie.export(bim)["yaml"])
    stash = _stash(doc)
    stash["excludedTables"] = [t for t in stash["excludedTables"] if t["name"] != "Time calc"]
    doc["custom_extensions"] = [{"vendor_name": "POWER_BI", "data": json.dumps(stash)}]
    fid = model_ossie.fidelity(bim, doc)
    assert _obj(fid, "table 'Time calc'")["class"] == "unsupported"
    assert any(w["object"] == "table 'Time calc'" and w["prominent"] for w in fid["warnings"])


def test_scenario_13_an_update_keeps_roles_and_the_report_says_so(svc):
    """The update merge keeps the model's own role; the file's report lists the file's objects only, and none
    of the roles is ever silent."""
    out = svc.meta_model_ossie_preview("test", model_ossie.export(_bim())["yaml"], mode="update")
    assert _obj(out["fidelity"], "role 'EU only'")["class"] == "fully_preserved"
    assert out["fidelity"]["flagged"]["roles"] == [{"object": "role 'EU only'", "class": "fully_preserved"}]


def test_fidelity_refuses_two_models_of_the_same_kind():
    with pytest.raises(ValueError, match="fidelity compares"):
        model_ossie.fidelity(_bim(), _bim())


# ---- roles and calculation groups are compared by content, not by name

def _restash(doc, stash):
    doc["custom_extensions"] = [e for e in doc.get("custom_extensions") or [] if e.get("vendor_name") != "POWER_BI"] + [
        {"vendor_name": "POWER_BI", "data": json.dumps(stash)}]
    return doc


@pytest.mark.parametrize("new_filter, says", [("", "came back empty"), ("TRUE()", "changed from")])
def test_a_role_whose_filter_changes_on_export_is_partially_converted_and_the_first_warning(new_filter, says):
    bim = _with_calculation_group(_bim())
    doc = yaml.safe_load(model_ossie.export(bim)["yaml"])
    stash = _stash(doc)
    stash["roles"][0]["tablePermissions"][0]["filterExpression"] = new_filter
    stash["excludedTables"][0]["calculationGroup"]["calculationItems"][0]["expression"] = "SELECTEDMEASURE()"
    fid = model_ossie.fidelity(bim, _restash(doc, stash))
    role = _obj(fid, "role 'EU only'")
    assert role["class"] == "partially_converted" and role["flag"] == "security"
    assert any(says in c and "dim_customer" in c for c in role["changes"])
    assert "filters" in role["remediation"]
    first = fid["warnings"][0]  # roles first, although the calculation group comes before them in the model
    assert first["object"] == "role 'EU only'" and first["severity"] == "security" and first["prominent"]
    assert says in first["message"]
    group = _obj(fid, "table 'Time calc'")
    assert group["class"] == "partially_converted"
    assert group["changes"] == [("calculation item 'YTD' expression changed from "
                                 "\"CALCULATE(SELECTEDMEASURE(), DATESYTD('Date'[Date]))\" to 'SELECTEDMEASURE()'")]
    assert any(w["object"] == "table 'Time calc'" and w["prominent"] for w in fid["warnings"][1:])
    assert {"object": "role 'EU only'", "class": "partially_converted"} in fid["flagged"]["roles"]


def test_a_role_whose_filter_members_or_permission_change_on_import_is_partially_converted():
    bim = _bim()
    bim["model"]["roles"][0]["members"] = [{"memberName": "ana@contoso.example"}]
    text = model_ossie.export(bim)["yaml"]
    back, report = model_ossie.to_tmsl(text)
    assert _obj(model_ossie.fidelity(model_ossie.parse(text), back, report), "role 'EU only'")["class"] == "fully_preserved"
    role = back["model"]["roles"][0]
    role["tablePermissions"][0]["filterExpression"] = ""
    role["members"] = []
    role["modelPermission"] = "administrator"
    fid = model_ossie.fidelity(model_ossie.parse(text), back, report)
    obj = _obj(fid, "role 'EU only'")
    assert obj["class"] == "partially_converted"
    changes = " | ".join(obj["changes"])
    assert "came back empty" in changes and "member(s) gone" in changes and "'administrator'" in changes
    assert fid["warnings"][0]["object"] == "role 'EU only'" and fid["warnings"][0]["severity"] == "security"


def test_a_calculation_item_whose_expression_changes_on_import_is_partially_converted():
    bim = _with_calculation_group(_bim())
    text = model_ossie.export(bim)["yaml"]
    back, report = model_ossie.to_tmsl(text)
    group = next(t for t in back["model"]["tables"] if t["name"] == "Time calc")
    group["calculationGroup"]["calculationItems"][0]["expression"] = "BLANK()"
    group["calculationGroup"]["calculationItems"][0]["ordinal"] = 3
    fid = model_ossie.fidelity(model_ossie.parse(text), back, report)
    obj = _obj(fid, "table 'Time calc'")
    assert obj["class"] == "partially_converted" and obj["kind"] == "calculation_group"
    assert [c.split(" changed")[0] for c in obj["changes"]] == ["calculation item 'YTD' expression",
                                                               "calculation item 'YTD' ordinal"]
    assert "calculation item" in obj["remediation"]


# ---- hostile sizes stay linear

def test_rows_naming_a_huge_name_are_matched_fast_and_the_name_is_cut_short():
    import time
    big = "a " * 20_000  # 40 KB, a space every other character
    bim = {"name": "m", "model": {"tables": [{"name": big, "columns": [{"name": "c", "dataType": "string"}]}]}}
    doc = {"version": model_ossie.OSSIE_VERSION, "name": "m", "datasets": [{"name": big, "fields": [{"name": "c"}]}]}
    rows = [{"object": f"nothing {big} {i}", "kind": "lost", "message": "m"} for i in range(200)]
    started = time.perf_counter()
    fid = model_ossie.fidelity(bim, doc, rows)
    assert time.perf_counter() - started < 1.0  # 26 s before the fix
    assert len(fid["warnings"]) == 200
    table = fid["objects"][1]
    assert table["object"].endswith("... (40000 characters)'") and len(table["object"]) < model_ossie.MAX_NAME + 40


def test_four_thousand_columns_of_one_name_are_numbered_fast():
    import time
    bim = {"name": "m", "model": {"tables": [{"name": "T", "columns": [{"name": "c", "dataType": "string"}] * 4000}]}}
    doc = {"version": model_ossie.OSSIE_VERSION, "name": "m", "datasets": [{"name": "T", "fields": [{"name": "c"}]}]}
    started = time.perf_counter()
    fid = model_ossie.fidelity(bim, doc)
    assert time.perf_counter() - started < 1.0
    ids = [o["object"] for o in fid["objects"]]
    assert len(ids) == len(set(ids)) == 4002 and ids[-1] == "table 'T' column 'c' #4000"


# ---- a report that can't be built, and relationships on the same columns

def test_a_document_whose_datasets_isnt_a_list_gives_an_unverified_report_not_an_error():
    back, _ = model_ossie.to_tmsl(_ossie(), SOURCE)
    fid = model_ossie.safe_fidelity({"version": model_ossie.OSSIE_VERSION, "datasets": "fact_sales"}, back)
    assert fid["status"] == "unverified" and "couldn't be built" in fid["error"]
    assert fid["objects"][0]["class"] == "unverified" and fid["counts"]["unverified"] == 1
    assert fid["validation"]["passed"] is None and fid["warnings"][0]["prominent"]
    assert model_ossie.fidelity(model_ossie.parse(_ossie()), back)["status"] == "complete"


def test_the_preview_and_ossie_to_tmdl_still_convert_when_the_report_cant_be_built(svc, monkeypatch):
    def broken(*args):
        raise AttributeError("'str' object has no attribute 'get'")
    monkeypatch.setattr(model_ossie, "fidelity", broken)
    preview = svc.meta_model_ossie_preview("test", _ossie(), name="SM_Gold", mode="update")
    assert preview["changes"] and preview["fidelity"]["status"] == "unverified"
    plan = svc.meta_model_ossie_to_tmdl(_ossie())["confirm"]
    assert plan["files"] and plan["fidelity"]["status"] == "unverified"
    out = svc.meta_model_ossie_to_tmdl(_ossie(), confirmed=True, token=plan["token"])
    assert out["fidelity"]["status"] == "unverified"


def _same_columns():
    bim = _bim()
    rel = {"fromTable": "fact_sales", "fromColumn": "customer_sk", "toTable": "dim_customer", "toColumn": "customer_sk"}
    bim["model"]["relationships"] = [dict(rel), dict(rel, isActive=False)]
    return bim


def test_an_unnamed_relationship_matches_one_core_relationship_once():
    bim = _same_columns()
    doc = yaml.safe_load(model_ossie.export(bim)["yaml"])
    ident = "relationship 'fact_sales.customer_sk -> dim_customer.customer_sk'"
    fid = model_ossie.fidelity(bim, doc)
    assert [_obj(fid, ident)["class"], _obj(fid, ident + " #2")["class"]] == ["fully_preserved", "preserved_through_extensions"]
    stash = _stash(doc)
    del stash["excludedRelationships"]  # the inactive one dropped: it must not borrow the active one's match
    fid = model_ossie.fidelity(bim, _restash(copy.deepcopy(doc), stash))
    assert [_obj(fid, ident)["class"], _obj(fid, ident + " #2")["class"]] == ["fully_preserved", "unsupported"]
    gone = copy.deepcopy(doc)
    del gone["relationships"]  # the active one dropped instead
    fid = model_ossie.fidelity(bim, gone)
    assert [_obj(fid, ident)["class"], _obj(fid, ident + " #2")["class"]] == ["unsupported", "preserved_through_extensions"]


def test_an_unnamed_relationship_set_aside_and_one_converted_on_the_same_columns_import_once_each():
    text = model_ossie.export(_same_columns())["yaml"]
    doc = model_ossie.parse(text)
    back, report = model_ossie.to_tmsl(text)
    classes = lambda fid: sorted(o["class"] for o in fid["objects"] if o["kind"] == "relationship")  # noqa: E731
    assert classes(model_ossie.fidelity(doc, back, report)) == ["fully_preserved", "fully_preserved"]
    back["model"]["relationships"] = [r for r in back["model"]["relationships"] if r.get("isActive") is not False]
    assert classes(model_ossie.fidelity(doc, back, report)) == ["fully_preserved", "unsupported"]


# ---- the round-trip check: skipped above a size, and never a crash

def test_a_model_too_large_for_the_round_trip_is_not_checked_and_never_passes(monkeypatch):
    monkeypatch.setattr(model_ossie, "MAX_ROUND_TRIP_OBJECTS", 3)
    out = model_ossie.export(_bim(), validate=True)
    check = out["fidelity"]["validation"]
    assert check["ran"] is False and check["passed"] is None and check["reason"] == "model too large for round-trip check"
    assert out["round_trip"] and "model too large" in out["round_trip"][0]["message"]


@pytest.mark.parametrize("error", [KeyError("x"), IndexError("y"), AttributeError("z"), TypeError("t")])
def test_a_round_trip_that_trips_is_reported_as_not_run(monkeypatch, error):
    def trips(bim):
        raise error
    monkeypatch.setattr(model_ossie, "round_trip", trips)
    check = model_ossie.round_trip_check(_bim())
    assert check["ran"] is False and check["passed"] is None and type(error).__name__ in check["error"]
