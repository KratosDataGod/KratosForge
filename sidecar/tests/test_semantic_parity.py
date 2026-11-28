"""L1 structural semantic parity (OSSIE-006a, semantic_parity.parity_l1).

Two definitions of any kind (TMSL, TMDL, Ossie in any profile) compared object by object. The best L1 result is
"Structurally compatible but behavior unverified"; L1 never says Verified equivalent or Equivalent within
tolerance. Unsupported objects are never matches. Required scenarios: 10 (relationships) and 13 (security
metadata), plus the same model in three kinds, bad input, a property over random hostile models and a timing."""

import copy
import json
import re
import logging

import pytest
import yaml

from fabric_desktop import model_ossie, semantic_parity as sp, tmdl
from test_model_ossie import _bim
from test_tmdl import FILES
from test_tmdl_fuzz import _Gen

ABOVE_CEILING = (sp.VERIFIED, sp.WITHIN_TOLERANCE)


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _obj(report, ident):
    hits = [o for o in report["objects"] if o["id"] == ident]
    assert len(hits) == 1, (ident, [o["id"] for o in report["objects"]])
    return hits[0]


def _assert_l1(report):
    """What every L1 report holds, whatever it compares."""
    assert report["level"] == "L1"
    assert report["ceiling"] == sp.COMPATIBLE
    assert report["summary"] not in ABOVE_CEILING
    assert all(o["result"] not in ABOVE_CEILING for o in report["objects"])
    assert report["counts"][sp.VERIFIED] == report["counts"][sp.WITHIN_TOLERANCE] == 0
    assert sum(report["counts"].values()) == len(report["objects"])
    assert set(report["counts"]) == set(sp.RESULTS)
    assert [n["level"] for n in report["not_checked"]] == ["L2", "L3", "L4"]
    assert report["tolerances"]["used"] is False
    return report


def _ossie_export(bim):
    return yaml.safe_load(model_ossie.export(bim, translate_dax=False)["yaml"])


def _without_extensions(value):
    if isinstance(value, dict):
        return {k: _without_extensions(v) for k, v in value.items() if k != "custom_extensions"}
    if isinstance(value, list):
        return [_without_extensions(v) for v in value]
    return value


def _ossie_03(bim):
    """The model as an Ossie 0.3.0.dev0 document another tool wrote: the core only, no Power BI extension."""
    doc = _without_extensions(_ossie_export(bim))
    doc["version"] = "0.3.0.dev0"
    return doc


def _two_relationships():
    bim = _bim()
    bim["model"]["tables"].append({"name": "dim_date", "columns": [
        {"name": "date_sk", "dataType": "int64", "sourceColumn": "date_sk"}]})
    bim["model"]["tables"][0]["columns"].append({"name": "date_sk", "dataType": "int64", "sourceColumn": "date_sk"})
    bim["model"]["relationships"].append({"name": "r2", "fromTable": "fact_sales", "fromColumn": "date_sk",
                                          "toTable": "dim_date", "toColumn": "date_sk"})
    return bim


def _set_stash(obj, data):
    obj["custom_extensions"] = [e for e in obj.get("custom_extensions") or [] if e.get("vendor_name") != "POWER_BI"] \
        + [{"vendor_name": "POWER_BI", "data": json.dumps(data)}]


def _stash(obj):
    return next(json.loads(e["data"]) for e in obj["custom_extensions"] if e["vendor_name"] == "POWER_BI")


# ---- the ceiling

def test_the_same_model_as_bim_tmdl_and_ossie_is_never_more_than_structurally_compatible():
    bim = _bim()
    files, _ = tmdl.from_bim(bim)
    doc = _ossie_export(bim)
    for left, right in ((bim, bim), (bim, files), (bim, doc), (files, doc), (doc, doc)):
        report = _assert_l1(sp.parity_l1(left, right))
        assert report["summary"] == sp.COMPATIBLE, [o for o in report["objects"] if o["result"] != sp.COMPATIBLE]
        assert report["counts"][sp.COMPATIBLE] == len(report["objects"])


def test_the_tmdl_fixture_and_its_ossie_export_are_structurally_compatible_with_extension_classes_shown():
    bim, _ = tmdl.to_bim(FILES)
    report = _assert_l1(sp.parity_l1(FILES, model_ossie.export(bim)["yaml"]))
    assert report["summary"] == sp.COMPATIBLE
    assert report["sides"]["left"]["format"] == "TMDL" and report["sides"]["right"]["profile"] == "ossie-0.2"
    assert _obj(report, "table 'Calc'")["kind"] == "calculation_group"
    assert _obj(report, "table 'Calc'")["fidelity"] == {"left": None, "right": "Preserved through extensions"}
    assert _obj(report, "relationship 'r-1'")["fidelity"]["right"] == "Preserved through extensions"


def test_l1_refuses_to_report_anything_above_the_ceiling(monkeypatch):
    with pytest.raises(AssertionError):
        sp._check_ceiling([{"result": sp.VERIFIED}], sp.VERIFIED)
    with pytest.raises(AssertionError):
        sp._check_ceiling([{"result": sp.WITHIN_TOLERANCE}], sp.COMPATIBLE)
    real = sp._pair

    def boasting(*args):
        return real(*args) | {"result": sp.VERIFIED}
    monkeypatch.setattr(sp, "_pair", boasting)
    with pytest.raises(AssertionError):
        sp.parity_l1(_bim(), _bim())


def test_a_measure_on_both_sides_is_compatible_with_a_note_that_expressions_werent_compared():
    left, right = _bim(), _bim()
    right["model"]["tables"][0]["measures"][0]["expression"] = "42"
    report = _assert_l1(sp.parity_l1(left, right))
    m = _obj(report, "table 'fact_sales' measure 'fact_sales rows'")
    assert m["result"] == sp.COMPATIBLE
    assert any("expressions weren't compared" in n and "L2" in n for n in m["notes"])


def test_a_measure_on_another_table_is_different():
    left, right = _bim(), _bim()
    right["model"]["tables"][1]["measures"] = [right["model"]["tables"][0]["measures"].pop()]
    m = _obj(sp.parity_l1(left, right), "table 'fact_sales' measure 'fact_sales total amount'")
    assert m["result"] == sp.DIFFERENT
    assert m["changes"] == ["home table changed from 'fact_sales' to 'dim_customer'"]


# ---- scenario 10: relationships

@pytest.mark.parametrize(("change", "expected"), [
    ({"isActive": False}, "active flag changed from true to false"),
    ({"fromCardinality": "many", "toCardinality": "many"}, "cardinality changed from many-to-one to many-to-many"),
    ({"crossFilteringBehavior": "bothDirections"},
     "cross-filter direction changed from 'oneDirection' to 'bothDirections'"),
    ({"toColumn": "date_sk", "toTable": "dim_date", "fromColumn": "date_sk"},
     ("endpoints changed from 'fact_sales'[customer_sk] -> 'dim_customer'[customer_sk] to "
      "'fact_sales'[date_sk] -> 'dim_date'[date_sk]")),
])
def test_scenario_10_a_changed_relationship_is_different_with_the_change_listed(change, expected):
    left, right = _two_relationships(), _two_relationships()
    right["model"]["relationships"][0].update(change)
    report = _assert_l1(sp.parity_l1(left, right))
    r1 = _obj(report, "relationship 'r1'")
    assert r1["result"] == sp.DIFFERENT
    assert r1["changes"] == [expected]
    assert _obj(report, "relationship 'r2'")["result"] == sp.COMPATIBLE
    assert report["summary"] == sp.DIFFERENT


def test_scenario_10_several_changes_to_one_relationship_are_each_listed():
    left, right = _bim(), _bim()
    right["model"]["relationships"][0].update(isActive=False, crossFilteringBehavior="bothDirections",
                                              fromCardinality="many", toCardinality="many")
    r1 = _obj(sp.parity_l1(left, right), "relationship 'r1'")
    assert len(r1["changes"]) == 3


def test_scenario_10_a_missing_relationship_is_different_and_names_the_side():
    left, right = _two_relationships(), _two_relationships()
    del right["model"]["relationships"][1]
    report = _assert_l1(sp.parity_l1(left, right))
    r2 = _obj(report, "relationship 'r2'")
    assert r2["result"] == sp.DIFFERENT
    assert r2["changes"] == ["missing on the right side (TMSL (compatibility level 1604))"]
    report = sp.parity_l1(right, left)
    assert _obj(report, "relationship 'r2'")["changes"] == ["missing on the left side (TMSL (compatibility level 1604))"]


def test_scenario_10_a_one_to_many_written_the_other_way_round_is_the_same_relationship():
    left, right = _bim(), _bim()
    right["model"]["relationships"][0] = {"name": "r1", "fromTable": "dim_customer", "fromColumn": "customer_sk",
                                          "fromCardinality": "one", "toCardinality": "many",
                                          "toTable": "fact_sales", "toColumn": "customer_sk"}
    assert _obj(sp.parity_l1(left, right), "relationship 'r1'")["result"] == sp.COMPATIBLE


def test_scenario_10_a_relationship_renamed_is_matched_by_its_endpoints():
    left, right = _bim(), _bim()
    right["model"]["relationships"][0]["name"] = "a-guid"
    r1 = _obj(sp.parity_l1(left, right), "relationship 'r1'")
    assert r1["result"] == sp.COMPATIBLE
    assert r1["ids"] == {"left": "relationship 'r1'", "right": "relationship 'a-guid'"}


def test_scenario_10_a_cross_filter_change_in_tmdl_is_different():
    bim, _ = tmdl.to_bim(FILES)
    files = dict(FILES)
    files["definition/relationships.tmdl"] = files["definition/relationships.tmdl"].replace(
        "crossFilteringBehavior: bothDirections", "crossFilteringBehavior: oneDirection")
    r = _obj(_assert_l1(sp.parity_l1(bim, files)), "relationship 'r-1'")
    assert r["result"] == sp.DIFFERENT
    assert r["changes"] == ["cross-filter direction changed from 'bothDirections' to 'oneDirection'"]


def test_scenario_10_an_inactive_or_many_to_many_relationship_has_no_place_in_ossie_03_so_is_unsupported():
    for change, need in (({"isActive": False}, "inactive relationship"),
                         ({"fromCardinality": "many", "toCardinality": "many"}, "many-to-many relationship"),
                         ({"crossFilteringBehavior": "bothDirections"}, "bothDirections cross-filter direction")):
        bim = _bim()
        doc = _ossie_03(bim)  # written from the active, one-direction model
        bim["model"]["relationships"][0].update(change)
        r1 = _obj(_assert_l1(sp.parity_l1(bim, doc)), "relationship 'r1'")
        assert r1["result"] == sp.UNSUPPORTED, change
        assert any(f"no place for a {need}" in n for n in r1["notes"])


def test_scenario_10_an_active_relationship_matches_ossie_03_with_the_converter_defaults_noted():
    bim = _bim()
    r1 = _obj(_assert_l1(sp.parity_l1(bim, _ossie_03(bim))), "relationship 'r1'")
    assert r1["result"] == sp.COMPATIBLE
    assert any("doesn't record an active flag; read as true" in n for n in r1["notes"])


def test_scenario_10_an_inactive_relationship_in_the_extension_matches_and_says_so_and_a_change_is_seen():
    bim = _bim()
    bim["model"]["relationships"][0]["isActive"] = False
    doc = _ossie_export(bim)
    r1 = _obj(sp.parity_l1(bim, doc), "relationship 'r1'")
    assert r1["result"] == sp.COMPATIBLE
    assert r1["fidelity"]["right"] == "Preserved through extensions"
    edited = copy.deepcopy(doc)
    data = _stash(edited)
    data["excludedRelationships"][0]["crossFilteringBehavior"] = "bothDirections"
    _set_stash(edited, data)
    r1 = _obj(sp.parity_l1(bim, edited), "relationship 'r1'")
    assert r1["result"] == sp.DIFFERENT
    assert r1["changes"] == ["cross-filter direction changed from 'oneDirection' to 'bothDirections'"]


def test_scenario_10_a_composite_ossie_relationship_is_unsupported_against_tmsl():
    bim = _bim()
    doc = _ossie_03(bim)
    doc["relationships"].append({"name": "two_columns", "from": "fact_sales", "to": "dim_customer",
                                 "from_columns": ["customer_sk", "amount"], "to_columns": ["customer_sk", "city"]})
    r = _obj(_assert_l1(sp.parity_l1(bim, doc)), "relationship 'two_columns'")
    assert r["result"] == sp.UNSUPPORTED
    assert any("no place for a composite relationship" in n for n in r["notes"])


# ---- scenario 13: security metadata

def _with_members(bim, *names):
    bim["model"]["roles"][0]["members"] = [{"memberName": n, "identityProvider": "AzureAD"} for n in names]
    return bim


@pytest.mark.parametrize(("edit", "expected"), [
    (lambda role: role["tablePermissions"][0].update(filterExpression='[country] = "FR"'),
     "the filter on table 'dim_customer' changed from '[country] = \"DE\"' to '[country] = \"FR\"'"),
    (lambda role: role.update(tablePermissions=[]),
     "the filter on table 'dim_customer' is gone (it was '[country] = \"DE\"')"),
    (lambda role: role["members"].append({"memberName": "eve@contoso.com", "identityProvider": "AzureAD"}),
     "1 member(s) added: ('eve@contoso.com', 'AzureAD')"),
    (lambda role: role.update(modelPermission="administrator"),
     "model permission 'read' became 'administrator'"),
])
def test_scenario_13_a_changed_role_is_different_with_the_change_listed(edit, expected):
    left = _with_members(_bim(), "ana@contoso.com")
    right = copy.deepcopy(left)
    edit(right["model"]["roles"][0])
    role = _obj(_assert_l1(sp.parity_l1(left, right)), "role 'EU only'")
    assert role["result"] == sp.DIFFERENT
    assert role["changes"] == [expected]


def test_scenario_13_a_removed_role_is_different_and_names_the_side():
    left, right = _bim(), _bim()
    del right["model"]["roles"]
    role = _obj(_assert_l1(sp.parity_l1(left, right)), "role 'EU only'")
    assert role["result"] == sp.DIFFERENT
    assert role["changes"] == ["missing on the right side (TMSL (compatibility level 1604))"]


def test_scenario_13_a_role_only_in_the_ossie_extension_is_reported_with_its_class_and_never_silently_matched():
    bim = _bim()
    doc = _ossie_export(bim)
    role = _obj(_assert_l1(sp.parity_l1(bim, doc)), "role 'EU only'")
    # Present on both sides, so structurally compatible at best; the extension-only life is on the row.
    assert role["result"] == sp.COMPATIBLE
    assert role["fidelity"] == {"left": None, "right": "Preserved through extensions"}
    assert any("only in the POWER_BI extension" in n and "row-level security" in n for n in role["notes"])
    # Against a model without the role, the extension's role is a difference, never a match.
    without = _bim()
    del without["model"]["roles"]
    role = _obj(sp.parity_l1(without, doc), "role 'EU only'")
    assert role["result"] == sp.DIFFERENT
    assert role["changes"] == ["missing on the left side (TMSL (compatibility level 1604))"]
    assert role["fidelity"]["right"] == "Preserved through extensions"


def test_scenario_13_a_role_edited_in_the_ossie_extension_is_different():
    bim = _with_members(_bim(), "ana@contoso.com")
    doc = _ossie_export(bim)
    data = _stash(doc)
    data["roles"][0]["members"] = []
    _set_stash(doc, data)
    role = _obj(sp.parity_l1(bim, doc), "role 'EU only'")
    assert role["result"] == sp.DIFFERENT
    assert role["changes"] == ["1 member(s) gone: ('ana@contoso.com', 'AzureAD')"]


def test_scenario_13_a_role_against_ossie_03_is_unsupported_not_matched_and_not_different():
    bim = _bim()
    report = _assert_l1(sp.parity_l1(bim, _ossie_03(bim)))
    role = _obj(report, "role 'EU only'")
    assert role["result"] == sp.UNSUPPORTED
    assert any("has no place for a role" in n for n in role["notes"])
    assert report["summary"] == sp.UNSUPPORTED


def test_scenario_13_a_calculation_group_item_change_is_different():
    bim, _ = tmdl.to_bim(FILES)
    other = copy.deepcopy(bim)
    calc = next(t for t in other["model"]["tables"] if t["name"] == "Calc")
    calc["calculationGroup"]["calculationItems"][0]["ordinal"] = 5
    group = _obj(sp.parity_l1(bim, other), "table 'Calc'")
    assert group["result"] == sp.DIFFERENT
    assert group["changes"] == ["calculation item 'YTD' ordinal changed from '0' to '5'"]


# ---- Unsupported is never a match

def test_an_object_the_conversion_cant_carry_is_unsupported_and_never_counted_compatible():
    doc = _ossie_export(_bim())
    doc["relationships"].append({"name": "two_columns", "from": "fact_sales", "to": "dim_customer",
                                 "from_columns": ["customer_sk", "amount"], "to_columns": ["customer_sk", "city"]})
    doc["metrics"] = [{"name": "no expression"}]
    report = _assert_l1(sp.parity_l1(doc, doc))
    unsupported = {o["id"] for o in report["objects"] if o["result"] == sp.UNSUPPORTED}
    assert unsupported == {"relationship 'two_columns'", "metric 'no expression'"}
    for ident in unsupported:
        assert _obj(report, ident)["fidelity"] == {"left": "Unsupported", "right": "Unsupported"}
    assert report["counts"][sp.UNSUPPORTED] == 2
    assert report["counts"][sp.COMPATIBLE] == len(report["objects"]) - 2
    assert report["summary"] == sp.UNSUPPORTED


def test_an_ossie_object_lost_in_conversion_is_unsupported_even_when_the_other_side_has_it():
    bim = _bim()
    bim["model"]["tables"][0]["measures"].append({"name": "no expression", "expression": "1"})
    doc = _ossie_export(_bim())
    doc["metrics"].append({"name": "no expression"})  # the converter writes no measure for it
    report = _assert_l1(sp.parity_l1(bim, doc))
    m = _obj(report, "table 'fact_sales' measure 'no expression'")
    assert m["result"] == sp.UNSUPPORTED
    assert m["fidelity"] == {"left": None, "right": "Unsupported"}
    assert any(e["source"] == "fidelity" and e["class"] == "Unsupported" for e in m["evidence"])
    assert report["counts"][sp.COMPATIBLE] == len(report["objects"]) - 1


# ---- data types through the converter's maps only

@pytest.mark.parametrize(("tmsl", "ossie", "same"), [
    ("int64", "Integer", True), ("dateTime", "Date", True), ("dateTime", "DateTime", True),
    ("dateTime", "Time", False), ("dateTime", "DateTimeTz", False), ("binary", "Opaque", False),
    ("double", "Decimal", False), ("decimal", "Decimal", True), ("automatic", None, True), (None, "String", False),
])
def test_a_tmsl_and_an_ossie_type_are_the_same_only_when_the_converter_reverses_one_exactly(tmsl, ossie, same):
    bim = {"name": "m", "model": {"tables": [{"name": "t", "columns": [{"name": "c", **({"dataType": tmsl} if tmsl else {})}]}]}}
    doc = {"version": "0.3.0.dev0", "name": "m", "datasets": [{"name": "t", "fields": [
        {"name": "c", **({"datatype": ossie} if ossie else {})}]}]}
    col = _obj(sp.parity_l1(bim, doc), "table 't' column 'c'")
    assert col["result"] == (sp.COMPATIBLE if same else sp.DIFFERENT)
    if not same:
        assert col["changes"][0].startswith("data type changed from ")


def test_an_ossie_02_time_field_isnt_the_same_as_the_datetime_the_converter_writes():
    doc = {"version": model_ossie.OSSIE_VERSION, "name": "m", "datasets": [{"name": "t", "source": "t", "fields": [
        {"name": "c", "datatype": "Time", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "c"}]}}]}]}
    bim = {"name": "m", "model": {"tables": [{"name": "t", "columns": [{"name": "c", "dataType": "dateTime"}]}]}}
    col = _obj(sp.parity_l1(bim, doc), "table 't' column 'c'")
    assert col["result"] == sp.DIFFERENT
    assert col["changes"] == ["data type changed from 'dateTime' (TMSL) to 'Time' (Ossie)"]
    assert col["fidelity"]["right"] == "Partially converted"


# ---- reading each kind

def test_snowflake_011_and_ossie_03_are_compared_as_written():
    doc = _ossie_03(_bim())
    wrapped = {"version": "0.1.1", "semantic_model": [{k: v for k, v in doc.items() if k != "version"}]}
    report = _assert_l1(sp.parity_l1(doc, wrapped))
    assert report["summary"] == sp.COMPATIBLE
    assert report["sides"]["right"]["profile"] == "snowflake-ossie"
    two = {"version": "0.1.1", "semantic_model": wrapped["semantic_model"] * 2}
    report = _assert_l1(sp.parity_l1(doc, two))
    assert report["summary"] == sp.UNSUPPORTED and "2 models" in report["error"]


def test_ossie_text_and_a_kind_named_by_the_caller_are_read():
    bim = _bim()
    text = model_ossie.export(bim)["yaml"]
    report = _assert_l1(sp.parity_l1(json.dumps(bim), text, left_kind="bim", right_kind="ossie"))
    assert report["summary"] == sp.COMPATIBLE
    assert report["sides"]["left"]["kind"] == "tmsl" and report["sides"]["right"]["kind"] == "ossie"


@pytest.mark.parametrize(("left", "kind"), [
    (None, None), (42, None), ({"x": 1}, None), ("version: [unclosed", None), ({"version": "9.9"}, None),
    ({"version": "0.1.1", "semantic_model": [5]}, None), ({"model": 5}, "tmsl"), ({"a.tmdl": 5}, None),
    ({"definition/model.tmdl": "model Model\n"}, "sql"), (_bim(), "tmdl"), ("x" * (model_ossie.MAX_CHARS + 1), None),
    ({"definition.pbism": "{}"}, "tmdl"),
], ids=lambda v: None if v is None or len(repr(v)) <= 40 else f"{type(v).__name__}-{len(repr(v))}-chars")
# Short ids: pytest puts the id in PYTEST_CURRENT_TEST, and a 20 MB id stalls a Windows runner (32 KB env limit).
def test_bad_input_is_test_failed_with_the_reason_never_an_exception(left, kind):
    report = _assert_l1(sp.parity_l1(left, _bim(), left_kind=kind))
    assert report["summary"] == sp.TEST_FAILED
    assert report["counts"][sp.TEST_FAILED] == 1
    assert report["error"].startswith("the left side couldn't be read: ")
    assert report["sides"]["left"]["read"] is False and report["sides"]["right"]["read"] is True


def test_tolerances_are_echoed_with_their_defaults_and_unused_at_l1():
    report = sp.parity_l1(_bim(), _bim(), tolerances={"absolute": 0.01, "bogus": 1, "relative": -1})
    tol = report["tolerances"]
    assert tol["values"] == {"absolute": 0.01, "relative": 0.0}
    assert tol["defaults"] == sp.DEFAULT_TOLERANCES
    assert tol["ignored"] == ["bogus", "relative"] and tol["used"] is False
    assert sp.parity_l1(_bim(), _bim(), tolerances="loose")["tolerances"]["ignored"] == ["(not a mapping: str)"]


def test_hostile_names_are_cut_short_in_ids_and_duplicates_are_numbered():
    long = "x" * 100_000
    bim = {"name": "m", "model": {"tables": [{"name": long, "columns": [{"name": "c"}, {"name": "c"}]},
                                             {"name": long + "y"}]}}
    report = _assert_l1(sp.parity_l1(bim, copy.deepcopy(bim)))
    assert report["summary"] == sp.COMPATIBLE
    assert all(len(o["id"]) < 3 * model_ossie.MAX_NAME for o in report["objects"])
    assert len({o["id"] for o in report["objects"]}) == len(report["objects"]) == 5
    assert sum(o["id"].endswith("column 'c' #2") for o in report["objects"]) == 1


# ---- property and timing

def test_property_a_random_hostile_model_compared_with_itself_is_never_different_nor_above_the_ceiling():
    for seed in range(120):
        bim = _Gen(seed).model()
        sides = [bim, copy.deepcopy(bim)]
        try:
            sides.append(tmdl.from_bim(bim)[0])
        except ValueError:  # what TMDL can't hold, refused by name (test_tmdl_fuzz)
            pass
        sides.append(model_ossie.export(bim, translate_dax=seed % 2 == 0)["yaml"])
        for other in sides:
            report = _assert_l1(sp.parity_l1(bim, other))
            assert report["counts"][sp.DIFFERENT] == 0, (seed, [o for o in report["objects"] if o["result"] == sp.DIFFERENT][:2])
            assert report["summary"] in (sp.COMPATIBLE, sp.UNSUPPORTED), seed


def test_4000_columns_compare_with_work_that_grows_with_the_objects(monkeypatch):
    """Comparing 4,000 columns pairs each object once, claims each candidate once and numbers ids with a few
    lookups each. It counts that work rather than timing it: a busy Windows runner missed a 2-second budget that
    takes about 0.3 s here, and a quadratic match (each object against every candidate) would be 16 million steps."""
    paired, claimed, looked = [0], [0], [0]
    real_pair, real_claim = sp._pair, sp._claim

    def pair(*args):
        paired[0] += 1
        return real_pair(*args)

    def claim(queue, used):
        claimed[0] += 1
        return real_claim(queue, used)

    class Counted(dict):
        def __contains__(self, key):
            looked[0] += 1
            return super().__contains__(key)

    class Objects(model_ossie._Objects):
        def __init__(self):
            super().__init__()
            self.ids = Counted()
    tables = [{"name": f"t{i}", "columns": [{"name": f"c{j}", "dataType": "int64"} for j in range(100)],
               "measures": [{"name": f"m{i}", "expression": "1"}]} for i in range(40)]
    bim = {"name": "big", "compatibilityLevel": 1604, "model": {"tables": tables, "relationships": [
        {"name": f"r{i}", "fromTable": f"t{i}", "fromColumn": "c0", "toTable": f"t{i + 1}", "toColumn": "c1"}
        for i in range(39)]}}
    files, _ = tmdl.from_bim(bim)
    monkeypatch.setattr(sp, "_pair", pair)
    monkeypatch.setattr(sp, "_claim", claim)
    monkeypatch.setattr(model_ossie, "_Objects", Objects)
    report = sp.parity_l1(bim, files)
    assert report["summary"] == sp.COMPATIBLE
    rows = len(report["objects"])
    assert sum(o["kind"] == "column" for o in report["objects"]) == 4000
    assert paired[0] == rows  # one pairing per row
    assert claimed[0] <= 2 * rows  # each left object claims once by key, a relationship once more by name
    assert looked[0] < 10 * 3 * rows, looked[0]  # ids numbered on both sides and in the report, a few lookups each


# ---- review follow-ups: TMSL text, detection order, redaction

def test_a_model_bim_as_text_is_json_under_its_own_cap_past_ossies():
    bim = _bim()
    bim["model"]["description"] = "x" * (model_ossie.MAX_CHARS + 1_000)  # past Ossie's 20 MB, under TMSL's cap
    text = json.dumps(bim)
    assert model_ossie.MAX_CHARS < len(text) < sp.MAX_TMSL_CHARS
    assert sp.MAX_TMSL_CHARS == tmdl.MAX_CHARS * 4  # one bound for a model.bim everywhere
    report = _assert_l1(sp.parity_l1(text, _bim()))
    assert report["summary"] == sp.COMPATIBLE and report["sides"]["left"]["kind"] == "tmsl"
    report = _assert_l1(sp.parity_l1("﻿" + json.dumps(_bim()), _bim(), left_kind="tmsl"))
    assert report["summary"] == sp.COMPATIBLE


def test_a_model_bim_text_past_the_tmsl_cap_or_not_json_is_test_failed_with_the_reason(monkeypatch):
    monkeypatch.setattr(sp, "MAX_TMSL_CHARS", 1_000)
    report = sp.parity_l1(json.dumps(_bim()), _bim())
    assert report["summary"] == sp.TEST_FAILED
    assert "a model.bim is compared at most" in report["error"]
    report = sp.parity_l1("{not json", _bim(), left_kind="tmsl")
    assert report["summary"] == sp.TEST_FAILED and "isn't valid JSON" in report["error"]


def test_an_ossie_json_document_past_ossies_cap_keeps_ossies_cap():
    doc = _ossie_03(_bim())
    doc["description"] = "x" * (model_ossie.MAX_CHARS + 1)
    report = sp.parity_l1(json.dumps(doc), _bim())
    assert report["summary"] == sp.TEST_FAILED and "an Ossie model is at most" in report["error"]


def test_ossie_markers_win_over_a_stray_model_key():
    doc = _ossie_03(_bim())
    doc["model"] = {"tables": []}
    report = _assert_l1(sp.parity_l1(doc, _bim()))
    assert report["sides"]["left"]["kind"] == "ossie" and report["sides"]["left"]["profile"] == "ossie-0.3"
    wrapped = {"semantic_model": [doc | {"version": None}], "model": {"tables": []}, "version": "0.1.1"}
    assert sp.parity_l1(wrapped, _bim())["sides"]["left"]["profile"] == "snowflake-ossie"
    bim = _bim() | {"version": 3}  # a version that isn't text isn't an Ossie version: TMSL
    assert sp.parity_l1(bim, _bim())["sides"]["left"]["kind"] == "tmsl"
    assert sp.parity_l1({"version": 3, "datasets": []}, _bim())["sides"]["left"]["kind"] == "ossie"
    assert sp.parity_l1(_bim(), _bim())["sides"]["left"]["kind"] == "tmsl"


def test_redacted_keeps_member_principals_and_filter_text_out_and_shows_counts():
    secret_filter, secret_member = '[country] = "DE-SECRET"', "ana.secret@contoso.com"
    left = _bim()
    left["model"]["roles"][0]["tablePermissions"][0]["filterExpression"] = secret_filter
    left["model"]["roles"][0]["members"] = [{"memberName": secret_member, "memberId": "oid-123"}]
    right = copy.deepcopy(left)
    right["model"]["roles"][0]["tablePermissions"][0]["filterExpression"] = '[country] = "FR-SECRET"'
    right["model"]["roles"][0]["members"] = [{"memberName": "eve.secret@contoso.com", "memberId": "oid-456"}]
    report = sp.parity_l1(left, right)
    full = json.dumps(report)
    assert "DE-SECRET" in full and secret_member in full  # the local pane's report is complete
    safe = sp.redacted(report)
    text = json.dumps(safe)
    for leaked in ("SECRET", "secret@", "oid-123", "oid-456"):
        assert leaked not in text, leaked
    role = _obj(safe, "role 'EU only'")
    assert role["result"] == sp.DIFFERENT
    new_filter = right["model"]["roles"][0]["tablePermissions"][0]["filterExpression"]
    changed = (f"the filter on table 'dim_customer' changed from {sp._fingerprint(secret_filter)} to "
               f"{sp._fingerprint(new_filter)}")
    assert role["changes"] == [changed, "1 member(s) gone", "1 member(s) added"]
    assert re.search(r"23 characters, fingerprint [0-9a-f]{12}", role["changes"][0])
    assert safe["redacted"] is True and "redacted" not in report  # the original is unchanged
    assert _obj(report, "role 'EU only'")["changes"][0].startswith("the filter on table 'dim_customer' changed from '")


def test_redacted_withholds_role_evidence_from_the_ossie_extension():
    bim = _with_members(_bim(), "ana.secret@contoso.com")
    doc = _ossie_export(bim)
    data = _stash(doc)
    data["roles"][0]["members"] = []
    _set_stash(doc, data)
    report = sp.parity_l1(bim, doc)
    safe = sp.redacted(report)
    assert "secret" not in json.dumps(safe)
    role = _obj(safe, "role 'EU only'")
    assert role["changes"] == ["1 member(s) gone"]
    assert all(e["source"] == "structure" or "withheld" in e for e in role["evidence"])


def _strings(value):
    """Every string anywhere in a JSON-like value, keys included, without recursion."""
    stack, out = [value], []
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            stack.extend(v.keys())
            stack.extend(v.values())
        elif isinstance(v, (list, tuple)):
            stack.extend(v)
    return out


def test_redacted_has_no_filter_text_or_member_anywhere_when_filters_are_added_or_removed():
    left = _bim()
    role = left["model"]["roles"][0]
    role["members"] = [{"memberName": "ana.secret@contoso.com", "memberId": "oid-1", "identityProvider": "AzureAD"}]
    right = copy.deepcopy(left)
    gone = role["tablePermissions"][0]["filterExpression"] = '[region] = "GONE-SECRET"'
    right["model"]["roles"][0]["tablePermissions"] = [  # the old filter removed, a filter on another table added
        {"name": "fact_sales", "filterExpression": '[amount] > 42 && [tag] = "ADDED-SECRET"'}]
    right["model"]["roles"][0]["members"] = [{"memberName": "ana.secret@contoso.com", "memberId": "oid-2",
                                              "identityProvider": "AzureAD"}]
    report = sp.parity_l1(left, right)
    assert any("GONE-SECRET" in s for s in _strings(report)) and gone
    for s in _strings(sp.redacted(report)):
        for leaked in ("SECRET", "ana.secret", "oid-1", "oid-2", "[amount]", "[region]"):
            assert leaked not in s, (leaked, s)
