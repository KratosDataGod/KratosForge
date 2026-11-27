"""Cross-platform compatibility matrix (OSSIE-015a, semantic_matrix.compatibility): one definition against Power BI,
Ossie 0.2, a Fabric Semantic View and Snowflake's Ossie 0.1.1. No row is ever better than "Structurally compatible
but behavior unverified"; every role and calculation group is named in every row; nothing reaches Fabric or
Snowflake."""

import copy
import json
import logging

import pytest
import yaml

from fabric_desktop import model_ossie, ossie_snowflake, semantic_matrix as sm, semantic_parity as sp, semantic_view
from test_model_ossie import _bim, _ossie
from test_ossie_fidelity import _with_calculation_group
from test_tmdl import FILES

ABOVE_CEILING = (sp.VERIFIED, sp.WITHIN_TOLERANCE)
FILTER = '[country] = "DE"'


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _rich():
    return _with_calculation_group(_bim())


def _export(bim):
    return yaml.safe_load(model_ossie.export(bim, translate_dax=True)["yaml"])


def _row(matrix, target):
    return next(r for r in matrix["targets"] if r["target"] == target)


def _sources():
    doc = _export(_rich())
    return {"bim": _rich(), "tmdl": FILES, "export": doc, "foreign": yaml.safe_load(_ossie()),
            "v03": dict(doc, version="0.3.0.dev0"), "bad": "{nope"}


def _text(value):
    return json.dumps(value, ensure_ascii=False).replace('\\"', '"')


def _assert_matrix(m):
    assert [r["target"] for r in m["targets"]] == list(sm.TARGETS)
    assert m["ceiling"] == sp.COMPATIBLE
    assert all(r["result"] in sp.L1_RESULTS and r["result"] not in ABOVE_CEILING for r in m["targets"])
    assert all(r["status"] in sm.STATUSES for r in m["targets"])
    assert [n["level"] for n in m["not_checked"]] == ["L3", "L4"]
    assert sum(m["summary"]["statuses"].values()) == 4 == sum(m["summary"]["results"].values())
    for r in m["targets"]:
        assert r["dropped"]["total"] >= len(r["dropped"]["objects"])
        severities = [w["severity"] for w in r["warnings"]]
        assert severities == sorted(severities, key=lambda s: s != "security")  # security first
    return m


# ---- the ceiling and the shape

@pytest.mark.parametrize("name", ["bim", "tmdl", "export", "foreign", "v03", "bad"])
def test_no_row_is_ever_better_than_the_ceiling(name):
    _assert_matrix(sm.compatibility(_sources()[name]))


def test_a_report_above_the_ceiling_is_refused(monkeypatch):
    real = sp.parity_l2

    def above(*args, **kwargs):
        return real(*args, **kwargs) | {"summary": sp.VERIFIED}

    monkeypatch.setattr(sp, "parity_l2", above)
    with pytest.raises(AssertionError, match="better than"):
        sm.compatibility(_rich())


def test_the_matrix_names_the_definition_its_columns_and_the_levels_not_checked():
    m = sm.compatibility(_rich())
    assert m["definition"]["kind"] == "tmsl" and m["definition"]["label"].startswith("TMSL")
    assert [c["key"] for c in m["columns"]] == ["target", "status", "result", "fidelity", "dropped", "parity",
                                                "roles", "calculation_groups"]
    assert m["security"] == {"roles": ["EU only"], "calculation_groups": ["Time calc"]}


def test_the_input_is_never_changed():
    bim = _rich()
    before = copy.deepcopy(bim)
    sm.compatibility(bim)
    assert bim == before


# ---- a model as the source

def test_a_model_is_its_own_power_bi_row_and_converted_for_the_other_three():
    m = sm.compatibility(_rich())
    assert [r["status"] for r in m["targets"]] == ["source", "converted", "converted", "converted"]
    source = _row(m, "power-bi-model")
    assert source["parity"] is None and source["fidelity"] is None
    assert [r["status"] for r in source["roles"] + source["calculation_groups"]] == ["kept", "kept"]


def test_ossie_02_keeps_roles_and_calculation_groups_only_in_the_extension_with_a_security_warning():
    row = _row(sm.compatibility(_rich()), "ossie-0.2")
    assert row["roles"][0]["status"] == row["calculation_groups"][0]["status"] == sm.EXTENSION_ONLY
    first = row["warnings"][0]
    assert first["severity"] == "security" and first["object"] == "role 'EU only'"
    assert "only in the POWER_BI extension" in first["message"]
    assert row["roles_dropped"] == [] and row["dropped"]["total"] == 0
    assert {o["object"] for o in row["extension_only"]["objects"]} >= {"role 'EU only'", "table 'Time calc'"}


def test_ossie_02_fidelity_comes_from_the_converter_report():
    out = model_ossie.export(_rich(), translate_dax=True)
    row = _row(sm.compatibility(_rich()), "ossie-0.2")
    assert row["fidelity"]["basis"] == "converter fidelity report"
    assert row["fidelity"]["counts"] == {model_ossie.CLASSES[c]: n for c, n in out["fidelity"]["counts"].items()}


def test_a_fidelity_report_that_cant_be_built_falls_back_to_the_parity_rows(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("no")

    monkeypatch.setattr(model_ossie, "fidelity", broken)
    row = _row(sm.compatibility(_rich()), "ossie-0.2")
    assert row["fidelity"]["basis"] == "derived from parity rows" and row["fidelity"]["scale"] == "parity results"
    assert row["fidelity"]["counts"] == row["parity"]["counts"]


def test_every_row_carries_the_full_l2_report_and_its_summary():
    for row in sm.compatibility(_rich())["targets"][1:]:
        assert row["parity"]["level"] == "L2" and row["parity"]["report"]["level"] == "L2"
        assert row["parity"]["summary"] == row["parity"]["report"]["summary"] == row["result"]


# ---- the Semantic View row

def test_the_view_drops_the_role_and_calculation_group_with_the_security_warning_first():
    row = _row(sm.compatibility(_rich()), "fabric-semantic-view")
    assert row["roles_dropped"] == ["EU only"] and row["calculation_groups_dropped"] == ["Time calc"]
    assert [(w["object"], w["severity"]) for w in row["warnings"]] == [("role 'EU only'", "security"),
                                                                        ("table 'Time calc'", "warning")]
    assert "no row-level security" in row["warnings"][0]["message"]
    assert row["fidelity"]["basis"] == "semantic view report"


def test_view_report_and_parity_names_are_matched_by_key_so_each_object_is_listed_once():
    names = [o["object"] for o in _row(sm.compatibility(_rich()), "fabric-semantic-view")["dropped"]["objects"]]
    assert len(names) == len(set(names))
    assert "table 'fact_sales' column 'amount'" in names
    assert not any(n.startswith("dataset ") for n in names)
    assert "expression 'DatabaseQuery'" in names  # only the view report knows it: listed under its own name


def test_a_measure_the_view_keeps_but_l2_cant_read_is_noted_not_dropped():
    row = _row(sm.compatibility(_rich()), "fabric-semantic-view")
    l2 = next(o for o in row["parity"]["report"]["objects"] if o["id"] == "table 'fact_sales' measure 'fact_sales total amount'")
    assert l2["result"] == sp.UNSUPPORTED and l2["ids"]["right"] is not None
    assert l2["ids"]["left"] not in [o["object"] for o in row["dropped"]["objects"]]
    assert any("limit of the check, not a loss" in n for n in row["notes"])


def test_dropped_objects_are_capped_with_the_total():
    bim = _rich()
    bim["model"]["tables"][1]["columns"] += [{"name": f"c{i}", "dataType": "string", "sourceColumn": f"c{i}"}
                                             for i in range(60)]
    dropped = _row(sm.compatibility(bim), "fabric-semantic-view")["dropped"]
    assert len(dropped["objects"]) == sm.MAX_DROPPED and dropped["total"] > 60


# ---- the Snowflake row

def test_snowflake_is_made_from_the_ossie_export_and_reported_unverified():
    row = _row(sm.compatibility(_rich()), "snowflake-ossie")
    assert row["status"] == "converted"
    assert row["fidelity"]["basis"] == "Snowflake report" and row["fidelity"]["status"] == "unverified"
    assert row["parity"]["report"]["sides"]["right"]["profile"] == "snowflake-ossie"
    assert any("OSSIE-010b" in n for n in row["notes"])


def test_snowflake_lists_a_dax_only_measure_it_skips_by_the_source_name():
    row = _row(sm.compatibility(_rich()), "snowflake-ossie")
    hit = [o for o in row["dropped"]["objects"] if o["object"] == "table 'fact_sales' measure 'fact_sales rows'"]
    assert len(hit) == 1 and "skips it silently" in hit[0]["why"]


def test_snowflake_carries_the_role_in_the_extension_but_doesnt_apply_it():
    row = _row(sm.compatibility(_rich()), "snowflake-ossie")
    assert row["roles"][0]["status"] == sm.EXTENSION_ONLY
    assert row["warnings"][0]["severity"] == "security"


def test_snowflake_drops_the_role_when_the_extension_doesnt_reach_the_file(monkeypatch):
    real = ossie_snowflake.to_snowflake

    def stripped(doc, *args, **kwargs):
        doc = copy.deepcopy(doc)
        doc.pop("custom_extensions", None)
        return real(doc, *args, **kwargs)

    monkeypatch.setattr(ossie_snowflake, "to_snowflake", stripped)
    row = _row(sm.compatibility(_rich()), "snowflake-ossie")
    assert row["roles_dropped"] == ["EU only"] and row["calculation_groups_dropped"] == ["Time calc"]


@pytest.mark.parametrize("path, key", [
    ("$.semantic_model[0].datasets[0].fields[1].expression", ("column", "d", "f2")),
    ("$.semantic_model[0].datasets[0].metrics[0].expression", ("measure", "dm")),
    ("$.semantic_model[0].metrics[0].expression", ("measure", "m")),
    ("$.semantic_model[0].metrics[9].expression", None),
    ("$.semantic_model[0].name", None),
], ids=["field", "dataset-metric", "metric", "out-of-range", "other"])
def test_a_snowflake_report_path_names_its_object_by_key(path, key):
    model = {"datasets": [{"name": "d", "fields": [{"name": "f1"}, {"name": "f2"}], "metrics": [{"name": "dm"}]}],
             "metrics": [{"name": "m"}]}
    assert sm._snowflake_key(model, path) == key


# ---- an Ossie file as the source

def test_an_ossie_02_file_is_its_own_row_and_its_roles_are_extension_only_there():
    m = _assert_matrix(sm.compatibility(_export(_rich())))
    assert [r["status"] for r in m["targets"]] == ["converted", "source", "converted", "converted"]
    source = _row(m, "ossie-0.2")
    assert source["roles"][0]["status"] == sm.EXTENSION_ONLY and source["warnings"][0]["severity"] == "security"


def test_an_ossie_file_imported_to_power_bi_keeps_its_role():
    row = _row(sm.compatibility(_export(_rich())), "power-bi-model")
    assert row["roles"][0]["status"] == row["calculation_groups"][0]["status"] == sm.KEPT
    assert row["fidelity"]["basis"] == "converter fidelity report" and not row["roles_dropped"]


def test_text_and_dicts_give_the_same_matrix():
    bim, doc = _rich(), _export(_rich())
    for value, text in ((bim, json.dumps(bim)), (doc, yaml.safe_dump(doc, sort_keys=False))):
        a, b = sm.compatibility(value), sm.compatibility(text)
        assert [(r["status"], r["result"], r["dropped"]) for r in a["targets"]] == \
               [(r["status"], r["result"], r["dropped"]) for r in b["targets"]]


def test_ossie_03_is_unsupported_on_every_row_with_the_reason():
    m = sm.compatibility(_sources()["v03"])
    assert {(r["status"], r["result"]) for r in m["targets"]} == {("refused", sp.UNSUPPORTED)}
    assert all("ossie-0.3" in r["reason"] and "OSSIE-012c" in r["reason"] for r in m["targets"])


def test_snowflake_011_is_its_own_row_and_the_others_are_refused():
    doc = _export(_rich())
    wrapped = {"version": "0.1.1", "semantic_model": [{k: v for k, v in doc.items() if k != "version"}]}
    m = _assert_matrix(sm.compatibility(wrapped))
    assert [r["status"] for r in m["targets"]] == ["refused", "refused", "refused", "source"]


# ---- what can't be read or converted

@pytest.mark.parametrize("value, kind", [
    ("{nope", None), (42, None), ({"x": 1}, None), ("version: 9.9\nname: a\n", None), (b"\xff", None),
    (_bim(), "semantic_view"), (_bim(), "nope"), ("a: 1", "tmdl"),
], ids=["json", "number", "no-kind", "version", "bytes", "view-kind", "bad-kind", "tmdl-text"])
def test_unreadable_input_is_test_failed_on_every_row(value, kind):
    m = _assert_matrix(sm.compatibility(value, kind))
    assert {(r["status"], r["result"]) for r in m["targets"]} == {("failed", sp.TEST_FAILED)}
    assert m["definition"]["read"] is False and m["definition"]["error"]
    assert all("couldn't be read" in r["reason"] for r in m["targets"])


def test_a_refused_export_makes_snowflake_not_available(monkeypatch):
    def refuse(*args, **kwargs):
        raise ValueError("refused here")

    monkeypatch.setattr(model_ossie, "export", refuse)
    m = _assert_matrix(sm.compatibility(_rich()))
    assert (_row(m, "ossie-0.2")["status"], _row(m, "ossie-0.2")["result"]) == ("refused", sp.UNSUPPORTED)
    sf = _row(m, "snowflake-ossie")
    assert (sf["status"], sf["result"]) == ("not available", sp.UNSUPPORTED) and "Ossie 0.2.0.dev0 export" in sf["reason"]
    assert [r["status"] for r in sf["roles"]] == [sm.UNVERIFIED]  # still named, never silent


def test_a_converter_that_trips_is_test_failed_for_that_target_only(monkeypatch):
    def trip(*args, **kwargs):
        raise TypeError("tripped")

    monkeypatch.setattr(semantic_view, "semantic_view", trip)
    m = _assert_matrix(sm.compatibility(_rich()))
    view = _row(m, "fabric-semantic-view")
    assert (view["status"], view["result"]) == ("failed", sp.TEST_FAILED) and "tripped" in view["reason"]
    assert _row(m, "ossie-0.2")["status"] == "converted"


def test_a_snowflake_refusal_is_unsupported_with_the_reason(monkeypatch):
    def refuse(*args, **kwargs):
        raise ValueError("not this one")

    monkeypatch.setattr(ossie_snowflake, "to_snowflake", refuse)
    sf = _row(sm.compatibility(_rich()), "snowflake-ossie")
    assert (sf["status"], sf["result"], sf["reason"]) == ("refused", sp.UNSUPPORTED, "not this one")


# ---- roles and calculation groups: never silent

@pytest.mark.parametrize("name", ["bim", "tmdl", "export"])
def test_every_role_and_calculation_group_is_named_in_every_row(name):
    m = sm.compatibility(_sources()[name])
    assert m["security"]["roles"]
    for r in m["targets"]:
        assert [x["name"] for x in r["roles"]] == m["security"]["roles"]
        assert [x["name"] for x in r["calculation_groups"]] == m["security"]["calculation_groups"]
        assert all(x["status"] in sm.FATES for x in r["roles"] + r["calculation_groups"])


@pytest.mark.parametrize("row, ext, fate", [
    (None, False, "unverified"),
    ({"result": sp.TEST_FAILED, "ids": {"right": "x"}, "fidelity": {"left": None, "right": None}}, False, "unverified"),
    ({"result": sp.UNSUPPORTED, "ids": {"right": "x"}, "fidelity": {"left": "Unsupported", "right": None}}, False,
     "unverified"),
    ({"result": sp.DIFFERENT, "ids": {"right": None}, "fidelity": {"left": None, "right": None}}, False, "dropped"),
    ({"result": sp.UNSUPPORTED, "ids": {"right": None}, "fidelity": {"left": None, "right": None}}, True,
     "extension only"),
    ({"result": sp.COMPATIBLE, "ids": {"right": "x"}, "fidelity": {"left": None, "right": "Preserved through extensions"}},
     False, "extension only"),
    ({"result": sp.DIFFERENT, "ids": {"right": "x"}, "fidelity": {"left": None, "right": None}}, False, "changed"),
    ({"result": sp.COMPATIBLE, "ids": {"right": "x"}, "fidelity": {"left": None, "right": None}}, False, "kept"),
], ids=["no-row", "failed", "source-lost", "missing", "in-extension", "extension", "changed", "kept"])
def test_what_a_target_does_to_an_object_comes_from_its_parity_row(row, ext, fate):
    assert sm._fate_of(row, "k", {"k"} if ext else set()) == fate


def test_the_summary_counts_dropped_roles_and_security_warnings():
    s = sm.compatibility(_rich())["summary"]
    assert s["roles_dropped"] == ["EU only"] and s["calculation_groups_dropped"] == ["Time calc"]
    assert s["security_warnings"] == 3  # Ossie 0.2 and Snowflake: extension only; the view: dropped
    assert s["result"] == sp.UNSUPPORTED


# ---- redacted

def test_redacted_hides_filter_text_and_withholds_text_on_dropped_roles():
    m = sm.compatibility(_rich())
    before = copy.deepcopy(m)
    out = sm.redacted(m)
    assert m == before and out["redacted"] is True
    assert FILTER not in _text(out)
    assert all(r["parity"]["report"]["redacted"] for r in out["targets"][1:])
    view = _row(out, "fabric-semantic-view")
    assert view["roles"][0]["withheld"] is True and "fingerprint" in view["roles"][0]["why"]
    assert all(d.get("withheld") for d in view["dropped"]["objects"] if d["kind"] == "role")
    assert _row(out, "ossie-0.2")["roles"][0]["why"] == _row(m, "ossie-0.2")["roles"][0]["why"]  # not dropped: kept


def test_a_role_the_import_changes_is_changed_and_redacted_hides_both_filters(monkeypatch):
    real = model_ossie.to_tmsl

    def changed(*args, **kwargs):
        bim, rows = real(*args, **kwargs)
        bim["model"]["roles"][0]["tablePermissions"][0]["filterExpression"] = '[country] = "FR"'
        return bim, rows

    monkeypatch.setattr(model_ossie, "to_tmsl", changed)
    m = sm.compatibility(_export(_rich()))
    row = _row(m, "power-bi-model")
    assert row["roles"][0]["status"] == sm.CHANGED and row["warnings"][0]["severity"] == "security"
    text = _text(m)
    assert FILTER in text and '[country] = "FR"' in text
    out = _text(sm.redacted(m))
    assert FILTER not in out and '[country] = "FR"' not in out
    assert _row(sm.redacted(m), "power-bi-model")["roles"][0]["withheld"] is True
