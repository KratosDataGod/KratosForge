"""Ossie version profiles (OSSIE-003b, ossie_profiles): which Ossie a file is, read by name or refused by name,
and the rules each target adds beyond the pinned schema. Fixtures are synthetic and small."""

import copy
import logging
import re

import pytest
import yaml

from fabric_desktop import model_ossie, ossie_profiles, ossie_validate
from test_model_ossie import svc  # noqa: F401 - svc is a fixture


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _expr(*dialects):
    return {"dialects": [{"dialect": d, "expression": e} for d, e in dialects]}


def _v02():
    """A flat 0.2.0.dev0 model that Fabric would publish as it is: DAX metric, single-column relationship."""
    return {"version": "0.2.0.dev0", "name": "orders", "datasets": [
        {"name": "sales", "source": "dbo.sales"}, {"name": "channel", "source": "dbo.channel"}],
        "relationships": [{"name": "sales_channel", "from": "sales", "to": "channel",
                           "from_columns": ["channel_sk"], "to_columns": ["channel_sk"]}],
        "metrics": [{"name": "revenue", "expression": _expr(("DAX", "SUM('sales'[amount])"))}]}


def _v03():
    doc = _v02()
    doc["version"] = "0.3.0.dev0"
    doc["nonportable_extensions"] = [{"vendor_name": "COMMON", "data": {"owner": "finance"}}]
    return doc


def _v011():
    """Snowflake's 0.1.1 document wrapper, as SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW writes it."""
    return {"version": "0.1.1", "semantic_model": [{"name": "orders", "datasets": [
        {"name": "sales", "source": "db.s.sales",
         "fields": [{"name": "amount", "expression": _expr(("SNOWFLAKE", "amount"))}]}],
        "metrics": [{"name": "revenue", "expression": _expr(("ANSI_SQL", "SUM(sales.amount)"))}]}]}


def _text(doc):
    return yaml.safe_dump(doc, sort_keys=False)


# ---- detection: a known profile, or a refusal by name (R1)

@pytest.mark.parametrize("doc, profile", [(_v02(), "ossie-0.2"), (_v03(), "ossie-0.3"), (_v011(), "snowflake-ossie")])
def test_each_version_is_detected_as_its_profile(doc, profile):
    assert ossie_profiles.detect_profile(doc) == profile
    assert ossie_validate.validate(doc)["profile"] == profile


@pytest.mark.parametrize("doc, says", [
    ({"version": "9.9", "name": "x", "datasets": [{"name": "a", "source": "b"}]}, "Ossie version 9.9"),
    ({"version": "0.1.1", "name": "x", "datasets": [{"name": "a", "source": "b"}]}, "no semantic_model wrapper"),
    ({"version": "0.2.0.dev0", "semantic_model": []}, "older Ossie layout"),
    ({"version": "0.4.0", "semantic_model": []}, "its version is 0.4.0"),
    ({"version": 2, "name": "x", "datasets": []}, "Ossie version 2"),
])
def test_an_unknown_version_is_refused_by_name(doc, says):
    with pytest.raises(ossie_profiles.UnknownProfile, match=re.escape(says)):
        ossie_profiles.detect_profile(doc)
    with pytest.raises(ValueError, match=re.escape(says)):
        model_ossie.parse(_text(doc))
    result = ossie_validate.validate(doc)
    assert not result["valid"] and result["profile"] == "" and result["status"] == "Not checked"
    (d,) = result["diagnostics"]
    assert d["rule"] in ("version", "profile") and says in d["message"]


def test_a_0_3_file_is_read_as_0_3_not_refused():
    doc = model_ossie.parse(_text(_v03()))
    assert doc["version"] == "0.3.0.dev0"
    result = ossie_validate.validate(_text(_v03()))
    assert result["valid"] and result["profile"] == "ossie-0.3" and result["status"] == "Valid"
    assert result["schema"]["commit"] == ossie_validate.SCHEMAS["0.3.0.dev0"]["commit"]


def test_a_0_3_file_or_a_0_1_1_wrapper_never_reaches_the_0_2_converter(monkeypatch):
    called = []
    monkeypatch.setattr(model_ossie, "convert_ossie_to_semantic_model", lambda *a, **k: called.append(a))
    for doc, profile in ((_v03(), "ossie-0.3"), (_v011(), "snowflake-ossie")):
        assert profile in model_ossie.not_convertible(doc)
        with pytest.raises(ValueError, match=r"can't convert it yet.*OSSIE-012c"):
            model_ossie.to_tmsl(_text(doc))
        with pytest.raises(ValueError, match="OSSIE-012c"):
            model_ossie.ossie_to_tmdl(_text(doc))
    assert called == []
    assert model_ossie.not_convertible(_v02()) == ""


def test_import_refuses_0_3_by_name_while_preview_still_checks_it(svc):  # noqa: F811
    text = _text(_v03())
    preview = svc.meta_model_ossie_preview("test", text, "SM_Gold")
    assert preview["convertible"] is False and "ossie-0.3" in preview["refused"]
    assert preview["changes"] == [] and preview["schema_validation"]["profile"] == "ossie-0.3"
    assert preview["schema_validation"]["valid"]
    for confirmed in (False, True):
        with pytest.raises(ValueError, match="can't convert it yet"):
            svc.meta_model_ossie_import("test", text, "SM_Gold", confirmed=confirmed)
    assert svc.fake.saved == []
    with pytest.raises(ValueError, match="OSSIE-012c"):
        svc.meta_model_ossie_to_tmdl(text, "orders")


def test_a_snowflake_wrapper_previews_as_unverified(svc):  # noqa: F811
    preview = svc.meta_model_ossie_preview("test", _text(_v011()), "SM_Gold")
    assert preview["convertible"] is False and "snowflake-ossie" in preview["refused"]
    assert preview["schema_validation"]["status"] == "Unverified"


@pytest.mark.parametrize("wrap", [lambda m: [m], lambda m: m], ids=["list", "object"])
def test_the_preview_of_a_wrapper_names_the_model_inside_it(svc, wrap):  # noqa: F811
    doc = _v011()
    doc["semantic_model"] = wrap(doc["semantic_model"][0])
    assert svc.meta_model_ossie_preview("test", _text(doc))["name"] == "orders"
    assert model_ossie.model_name({"version": "0.1.1", "semantic_model": []}) == ""


def test_a_0_2_file_still_previews_and_converts(svc):  # noqa: F811
    preview = svc.meta_model_ossie_preview("test", _text(_v02()), "SM_Gold")
    assert preview["convertible"] is True and preview["schema_validation"]["profile"] == "ossie-0.2"


# ---- one valid and one invalid fixture per profile

def _broken_v02():
    doc = _v02()
    doc["datasets"][0]["colour"] = "blue"
    return doc


def _broken_v03():
    doc = _v03()
    doc["custom_extensions"] = []  # 0.2's name, not a property in 0.3
    return doc


@pytest.mark.parametrize("profile, good, bad, rule", [
    ("ossie-0.2", _v02, _broken_v02, "additionalProperties"),
    ("ossie-0.3", _v03, _broken_v03, "additionalProperties"),
])
def test_the_pinned_schema_profiles(profile, good, bad, rule):
    ok = ossie_validate.validate(good(), profile)
    assert ok["valid"] and ok["status"] == "Valid" and ok["checked"] and ok["effect"] == "informs"
    assert ok["rules"] == [f"{profile}/schema"]
    broken = ossie_validate.validate(bad(), profile)
    assert not broken["valid"] and broken["status"] == "Invalid"
    assert [d["rule"] for d in broken["diagnostics"]] == [rule]


def test_the_fabric_profile_passes_a_view_fabric_publishes():
    result = ossie_validate.validate(_v02(), "fabric-semantic-view")
    assert result["valid"] and result["status"] == "Valid" and result["warnings"] == []
    assert result["schema"]["commit"] == ossie_validate.SCHEMAS["0.2.0.dev0"]["commit"]


def test_a_non_dax_metric_gets_a_fabric_diagnostic():
    doc = _v02()
    doc["metrics"][0]["expression"] = _expr(("ANSI_SQL", "SUM(sales.amount)"))
    doc["datasets"][0]["metrics"] = [{"name": "rows", "expression": {"sql": "COUNT(*)"}}]
    result = ossie_validate.validate(doc, "fabric-semantic-view")
    assert not result["valid"] and result["status"] == "Invalid"
    assert [(d["path"], d["rule"]) for d in result["diagnostics"]] == [
        ("$.datasets[0].metrics[0].expression", "fabric/metric-dax"), ("$.metrics[0].expression", "fabric/metric-dax")]
    assert all(set(d) == {"path", "rule", "value", "message", "fix"} for d in result["diagnostics"])
    assert ossie_validate.validate(doc)["valid"]  # plain 0.2 has no such rule


def test_a_composite_relationship_gets_a_fabric_diagnostic():
    doc = _v02()
    doc["relationships"][0]["from_columns"] = ["channel_sk", "region"]
    doc["relationships"][0]["to_columns"] = ["channel_sk", "region"]
    result = ossie_validate.validate(doc, "fabric-semantic-view")
    assert [(d["path"], d["rule"], d["value"]) for d in result["diagnostics"]] == [
        ("$.relationships[0].from_columns", "fabric/relationship-single-column", "['channel_sk', 'region']"),
        ("$.relationships[0].to_columns", "fabric/relationship-single-column", "['channel_sk', 'region']")]


def test_fabric_warns_that_custom_extensions_are_dropped_and_still_passes():
    doc = _v02()
    doc["custom_extensions"] = [{"vendor_name": "POWER_BI", "data": "{}"}]
    doc["datasets"][1]["custom_extensions"] = [{"vendor_name": "COMMON", "data": "{}"}]
    doc["datasets"][0]["fields"] = [{"name": "amount", "expression": {"sql": "amount"}, "ai_context": "money"}]
    result = ossie_validate.validate(doc, "fabric-semantic-view")
    assert result["valid"] and result["diagnostics"] == []
    by_rule = {(w["rule"], w["path"]): w for w in result["warnings"]}
    ext = by_rule[("fabric/extensions-omitted", "$.custom_extensions")]
    assert ext["value"] == "2" and "dropped" in ext["message"] and "1 other place" in ext["message"]
    assert ("fabric/omitted", "$.datasets[0].fields") in by_rule
    assert ("fabric/omitted", "$.datasets[0].fields[0].ai_context") in by_rule


def test_the_fabric_profile_refuses_another_version_by_its_own_rule():
    result = ossie_validate.validate(_v03(), "fabric-semantic-view")
    assert [d["rule"] for d in result["diagnostics"]] == ["fabric/version"] and not result["valid"]
    assert result["status"] == "Invalid"


def test_the_snowflake_wrapper_is_recognized_and_always_unverified():
    result = ossie_validate.validate(_text(_v011()))
    assert result["profile"] == "snowflake-ossie" and result["status"] == "Unverified"
    assert not result["valid"] and not result["checked"] and result["schema"] is None
    assert result["diagnostics"] == [] and result["warnings"] == []  # a correct 0.1.1 file has no errors
    (note,) = result["notes"]
    assert (note["path"], note["rule"]) == ("$.version", "unverified") and "Unverified" in note["message"]
    assert model_ossie.parse(_text(_v011()))["semantic_model"][0]["name"] == "orders"


def test_a_broken_snowflake_wrapper_is_named_and_still_unverified():
    doc = _v011()
    doc["semantic_model"][0]["datasets"][0]["fields"][0]["expression"] = _expr(("DAX", "[amount]"))
    warned = ossie_validate.validate(doc)
    (w,) = warned["warnings"]
    assert w["rule"] == "snowflake/dialect" and w["path"] == "$.semantic_model[0].datasets[0].fields[0].expression"
    flat = ossie_validate.validate(_v02(), "snowflake-ossie")
    # real errors make it Invalid; Unverified is only for a file whose documented shape passes
    assert {d["rule"] for d in flat["diagnostics"]} == {"snowflake/version", "snowflake/wrapper"}
    assert flat["status"] == "Invalid" and not flat["valid"] and flat["notes"][0]["rule"] == "unverified"
    assert warned["status"] == "Unverified"  # a warning isn't an error


def test_an_unknown_profile_name_is_refused():
    with pytest.raises(ValueError, match="no Ossie profile 'tableau'"):
        ossie_validate.validate(_v02(), "tableau")
    with pytest.raises(ValueError, match="Only fabric-semantic-view"):
        ossie_validate.validate_files({"a.yaml": _text(_v02())}, "ossie-0.2")


# ---- Fabric's limits on a set of files

def _files(count, ext=".yaml"):
    """A view split across ``count`` files: the first holds the model, each other one more dataset."""
    files = {f"part00{ext}": _text(_v02())}
    files |= {f"part{i:02}{ext}": _text({"version": "0.2.0.dev0", "name": "orders",
                                          "datasets": [{"name": f"extra{i}", "source": f"dbo.extra{i}"}]})
              for i in range(1, count)}
    return files


@pytest.mark.parametrize("count, ok", [(20, True), (21, False)])
def test_the_fabric_20_file_limit(count, ok):
    result = ossie_validate.validate_files(_files(count))
    assert result["valid"] is ok and result["counts"]["yaml_files"] == count
    assert [d["rule"] for d in result["diagnostics"]] == ([] if ok else ["fabric/file-count"])
    # past the limit only the first 20 are checked one by one, and the set says so
    assert result["files_checked"] is ok and len(result["files"]) == min(count, 20)


def test_yml_files_are_ignored_with_a_warning():
    files = _files(20) | _files(3, ".yml") | {"notes.json": "{}"}
    result = ossie_validate.validate_files(files)
    assert result["valid"] and result["counts"]["yaml_files"] == 20
    assert [w["rule"] for w in result["warnings"]] == ["fabric/yml-ignored"]
    assert result["counts"]["ignored"] == sorted([*_files(3, ".yml"), "notes.json"])


def test_the_yaml_extension_is_matched_in_any_case_which_is_labelled_derived():
    result = ossie_validate.validate_files({"A.YAML": _text(_v02()), "b.YML": "x"})
    assert result["valid"] and result["counts"]["yaml_files"] == 1 and result["counts"]["ignored"] == ["b.YML"]
    by_id = {r["id"]: r for r in ossie_profiles.rules("fabric-semantic-view")}
    for rule_id in ("fabric/file-count", "fabric/yml-ignored"):
        assert "derived" in by_id[rule_id]["says"] and "case" in by_id[rule_id]["derived"]


def _padded(total):
    """One valid .yaml file of exactly ``total`` bytes, padded with a comment."""
    base = _text(_v02())
    pad = total - len(base.encode()) - 2
    return base + "#" + "x" * pad + "\n"


@pytest.mark.parametrize("extra, ok", [(0, True), (1, False)])
def test_the_fabric_16_mb_yaml_limit(extra, ok):
    limit = ossie_profiles.FABRIC_MAX_YAML_BYTES
    assert limit == 16_000_000
    files = {"a.yaml": _text({"version": "0.2.0.dev0", "name": "orders",
                              "datasets": [{"name": "returns", "source": "dbo.returns"}]})}
    files["b.yaml"] = _padded(limit + extra - len(files["a.yaml"].encode()))
    result = ossie_validate.validate_files(files)
    assert result["counts"]["yaml_bytes"] == limit + extra
    assert result["valid"] is ok and result["files_checked"] is ok
    assert [d["rule"] for d in result["diagnostics"]] == ([] if ok else ["fabric/yaml-size"])


@pytest.mark.parametrize("extra, ok", [(0, True), (1, False)])
def test_the_fabric_30_mb_tmdl_limit(extra, ok):
    limit = ossie_profiles.FABRIC_MAX_TMDL_BYTES
    assert limit == 30_000_000
    tmdl = {"definition/model.tmdl": "model Model\n", "definition/tables/sales.tmdl": "x" * (limit + extra - 12)}
    result = ossie_validate.validate_files({"a.yaml": _text(_v02())}, tmdl=tmdl)
    assert result["counts"]["tmdl_bytes"] == limit + extra and result["valid"] is ok
    assert [d["rule"] for d in result["diagnostics"]] == ([] if ok else ["fabric/tmdl-size"])


def test_the_tmdl_limit_is_checked_only_when_tmdl_is_given():
    result = ossie_validate.validate_files({"a.yaml": _text(_v02())})
    assert result["valid"] and result["counts"]["tmdl_bytes"] is None


def test_a_file_in_the_set_is_checked_by_the_fabric_rules():
    bad = {"version": "0.2.0.dev0", "name": "orders",
           "metrics": [{"name": "rows", "expression": {"sql": "COUNT(*)"}}]}
    result = ossie_validate.validate_files({"a.yaml": _text(_v02()), "b.yaml": _text(bad)})
    assert not result["valid"] and result["diagnostics"] == []
    assert result["files"]["a.yaml"]["valid"]
    # found in the merged view's metrics[1], shown at its own place in b.yaml
    assert [(d["path"], d["rule"]) for d in result["files"]["b.yaml"]["diagnostics"]] == [
        ("$.metrics[0].expression", "fabric/metric-dax")]


# ---- a view split across files (semantic-views-yaml-reference, "Validation across files")

def _part(**more):
    return _text({"version": "0.2.0.dev0", "name": "orders"} | more)


def test_a_view_split_into_fragments_is_checked_merged():
    files = {"datasets.yaml": _part(datasets=_v02()["datasets"]),
             "relationships.yaml": _part(relationships=_v02()["relationships"]),
             "metrics.yaml": _part(metrics=_v02()["metrics"])}
    result = ossie_validate.validate_files(files)
    assert result["valid"] and result["status"] == "Valid" and result["merged"]["checked"]
    assert all(r["valid"] for r in result["files"].values())


def test_each_fragment_needs_version_and_name_and_nonempty_datasets():
    files = {"a.yaml": _text(_v02()), "b.yaml": _text({"name": "orders"}), "c.yaml": _part(datasets=[]),
             "d.yaml": "# only a comment\n", "e.yaml": _text({"version": "0.2.0.dev0"})}
    result = ossie_validate.validate_files(files)
    got = {n: [(d["path"], d["rule"]) for d in r["diagnostics"]] for n, r in result["files"].items()}
    assert got == {"a.yaml": [], "b.yaml": [("$.version", "fabric/fragment")],
                   "c.yaml": [("$.datasets", "fabric/fragment")], "d.yaml": [("$", "fabric/fragment")],
                   "e.yaml": [("$.name", "fabric/fragment")]}
    assert not result["valid"]


def test_names_repeated_across_files_and_different_descriptions_are_named():
    files = {"a.yaml": _text(_v02() | {"description": "Orders"}),
             "b.yaml": _part(datasets=[{"name": "sales", "source": "dbo.sales2"}], description="Sales")}
    result = ossie_validate.validate_files(files)
    assert [(d["path"], d["rule"]) for d in result["files"]["b.yaml"]["diagnostics"]] == [
        ("$.datasets[0].name", "fabric/duplicate-name"), ("$.description", "fabric/description")]
    assert "a.yaml" in result["files"]["b.yaml"]["diagnostics"][0]["message"]


def test_files_may_give_different_names_because_fabric_does_not_use_the_name():
    # yaml-reference: name is "Required, but the value isn't used. The view folder name is used as the view and
    # semantic model name."
    files = {"a.yaml": _text(_v02()), "b.yaml": _text({"version": "0.2.0.dev0", "name": "something else",
                                                       "datasets": [{"name": "returns", "source": "dbo.returns"}]})}
    result = ossie_validate.validate_files(files)
    assert result["valid"] and not any(r["diagnostics"] for r in result["files"].values())
    assert not any(r["id"] == "fabric/name" for r in ossie_profiles.RULES)


def test_a_top_level_key_given_differently_in_two_files_is_a_conflict():
    files = {"a.yaml": _text(_v02() | {"ai_context": "orders"}),
             "b.yaml": _part(ai_context="sales", datasets=[{"name": "returns", "source": "dbo.returns"}]),
             "c.yaml": _part(ai_context="orders", datasets=[{"name": "stock", "source": "dbo.stock"}])}
    result = ossie_validate.validate_files(files)
    assert not result["valid"]
    (d,) = result["files"]["b.yaml"]["diagnostics"]
    assert (d["path"], d["rule"]) == ("$.ai_context", "fabric/conflict") and "a.yaml" in d["message"]
    assert result["files"]["c.yaml"]["diagnostics"] == []  # the same value is no conflict
    rule = {r["id"]: r for r in ossie_profiles.RULES}["fabric/conflict"]
    assert rule["basis"] == "documented" and "conflicting definitions across files" in rule["says"]


def test_files_that_cannot_be_read_are_named_in_the_merged_note():
    files = {"a.yaml": _text(_v02()), "b.yaml": "a: [", "c.yaml": "{unclosed: ["}
    result = ossie_validate.validate_files(files)
    assert not result["valid"]
    assert [d["rule"] for d in result["files"]["b.yaml"]["diagnostics"]] == ["parse"]
    (note,) = result["merged"]["notes"]
    assert note["rule"] == "fabric/merged" and note["value"] == "2"
    assert note["message"].startswith("2 file(s) couldn't be read and aren't in the merged check; references to "
                                      "them may show as unresolved")
    assert "b.yaml" in note["message"] and "c.yaml" in note["message"]
    assert ossie_validate.validate_files({"a.yaml": _text(_v02())})["merged"]["notes"] == []


def test_metrics_without_datasets_are_valid_and_datasets_are_inferred():
    result = ossie_validate.validate_files({"metrics.yaml": _part(metrics=_v02()["metrics"])})
    assert result["valid"]
    assert [w["rule"] for w in result["warnings"]] == ["fabric/datasets-inferred"]


def test_a_view_with_no_yaml_files_fails():
    result = ossie_validate.validate_files({"view.yml": _text(_v02())})
    assert not result["valid"] and [d["rule"] for d in result["diagnostics"]] == ["fabric/fragment"]


# ---- every rule cites its source

def test_every_rule_has_a_source_a_page_date_and_a_basis():
    assert {r["profile"] for r in ossie_profiles.RULES} == set(ossie_profiles.PROFILES)
    for rule in ossie_profiles.RULES:
        assert rule["source"].startswith("https://") and rule["page_date"], rule["id"]
        assert rule["basis"] in ossie_profiles.BASES and rule["id"].count("/") == 1, rule["id"]
        assert rule["scope"] in ("document", "files") and rule["severity"] in ("error", "warning")
    ids = [r["id"] for r in ossie_profiles.RULES]
    assert len(ids) == len(set(ids))


def test_the_pinned_schema_rules_cite_the_vendored_commits():
    for rule in ossie_profiles.RULES:
        if rule["basis"] == "pinned schema":
            assert any(info["commit"] in rule["source"] for info in ossie_validate.SCHEMAS.values())
    assert ossie_profiles.rules("ossie-0.2")[0]["source"].endswith(
        "21d14d7a4044e71a19ea46d024c974a57323174a/core-spec/ossie-schema.json")


def test_snowflake_rules_without_a_schema_and_derived_rules_are_labelled():
    sf = ossie_profiles.rules("snowflake-ossie")
    assert not any(r["basis"] == "pinned schema" for r in sf)
    assert {r["id"]: r["basis"] for r in sf}["snowflake/dialect"] == "derived"
    assert all(r["page_date"] == "no page date" for r in sf)


def test_every_rule_id_a_diagnostic_names_is_a_listed_rule():
    ids = {r["id"] for r in ossie_profiles.RULES}
    doc = _v02()
    doc["metrics"][0]["expression"] = {"sql": "x"}
    doc["relationships"][0]["to_columns"] = ["a", "b"]
    doc["custom_extensions"] = []
    found = ossie_validate.validate(doc, "fabric-semantic-view")
    named = {d["rule"] for d in found["diagnostics"] + found["warnings"]}
    assert named and named <= ids


def test_a_document_is_not_changed_by_any_profile():
    for profile in ossie_profiles.PROFILES:
        doc = _v011() if profile == "snowflake-ossie" else _v02()
        before = copy.deepcopy(doc)
        ossie_validate.validate(doc, profile)
        assert doc == before


def test_past_the_file_count_only_the_first_twenty_files_are_checked_one_by_one(monkeypatch):
    from fabric_desktop import ossie_profiles
    calls = []
    real = model_ossie.load_text
    monkeypatch.setattr(model_ossie, "load_text", lambda text: calls.append(1) or real(text))  # one per file read
    files = {f"f{i:04}.yaml": "version: 0.2.0.dev0\nname: x\ndatasets: []\n" for i in range(500)}
    out = ossie_profiles.validate_files(files)
    assert len(calls) == ossie_profiles.FABRIC_MAX_FILES and len(out["files"]) == ossie_profiles.FABRIC_MAX_FILES
    assert not out["valid"] and not out["files_checked"]
    assert [d["rule"] for d in out["diagnostics"]] == ["fabric/file-count"]
    assert any(w["rule"] == "fabric/not-checked" and "480 more" in w["message"] for w in out["warnings"])


def test_profile_rules_run_only_after_the_depth_and_key_checks(monkeypatch):
    # ossie_profiles.check is reached only through ossie_validate.validate, after _shape; validate_files goes
    # through validate for each file too
    from fabric_desktop import ossie_profiles, ossie_validate
    seen = []
    monkeypatch.setattr(ossie_profiles, "check", lambda doc, profile: seen.append(profile) or {})
    deep: dict = {}
    node = deep
    for _ in range(ossie_validate.MAX_DEPTH + 5):
        node["x"] = {}
        node = node["x"]
    doc = {"version": "0.2.0.dev0", "name": "m", "datasets": [], "deep": deep}
    assert ossie_validate.validate(doc)["diagnostics"][0]["rule"] == "depth" and not seen
    ossie_profiles.validate_files({"a.yaml": "version: 0.2.0.dev0\nname: m\ndatasets: []\n" + "x: [" * 200 + "]" * 200})
    assert not seen
