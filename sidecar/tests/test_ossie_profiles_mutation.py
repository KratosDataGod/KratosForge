"""ossie_profiles.py held to its exact refusals and words: which documents and profiles it refuses and how,
the version a result names, more than 200 problems counted, the rules each profile and scope has, a value
that isn't text, five names then "…", the count of files past twenty, where a merged problem is shown, each
file's status, and the summary's "other places". Everything reads values; nothing is fetched."""

from __future__ import annotations

import pytest

from fabric_desktop import ossie_profiles as op


def yaml(i=0):
    return f"version: 0.2.0.dev0\nname: m{i}\ndatasets:\n  - name: d{i}\n    source: s{i}\n"


@pytest.mark.parametrize("doc, message, parts, rule", [
    ([1], "This isn't an Ossie semantic model: it should be one object with version, name and datasets", (), "type"),
    ({"version": "0.2.0.dev0", "semantic_model": []},
     ("This file uses the older Ossie layout with a semantic_model list. Ossie 0.2.0.dev0 puts one model's name and "
      "datasets at the top of the file."), ("semantic_model",), "profile"),
])
def test_a_document_no_profile_reads_is_refused_with_where_it_goes(doc, message, parts, rule):
    with pytest.raises(op.UnknownProfile) as caught:
        op.detect_profile(doc)
    assert (str(caught.value), caught.value.parts, caught.value.rule) == (message, parts, rule)


def test_a_profile_that_isnt_there_is_refused_by_name():
    with pytest.raises(ValueError) as caught:
        op.rules("nope")
    assert str(caught.value) == ("There's no Ossie profile 'nope'; the profiles are ossie-0.3, ossie-0.2, "
                                 "fabric-semantic-view, snowflake-ossie")


def test_rules_are_chosen_by_profile_and_scope():
    assert len(op.rules()) == len(op.RULES)
    files = op.rules(scope="files")
    assert files and all(r["scope"] == "files" for r in files)
    assert [r["id"] for r in op.rules("fabric-semantic-view", "document")] == [
        "fabric/version", "fabric/schema", "fabric/metric-dax", "fabric/relationship-single-column",
        "fabric/extensions-omitted", "fabric/omitted"]
    assert op.rules("ossie-0.2", "files") == []


def test_which_profiles_the_converter_takes():
    assert {name: p["convertible"] for name, p in op.PROFILES.items()} == {
        "ossie-0.3": False, "ossie-0.2": True, "fabric-semantic-view": True, "snowflake-ossie": False}


def test_a_result_names_the_profiles_schema_version_or_the_documents_own():
    assert op.check({"version": "9.9", "name": "m", "datasets": []}, "ossie-0.2")["version"] == "0.2.0.dev0"
    assert op.check({"version": "0.1.1", "semantic_model": [{"name": "m"}]}, "snowflake-ossie")["version"] == "0.1.1"


def test_more_than_200_problems_are_counted_in_one_more_line():
    metrics = [{"name": f"x{i}", "expression": {"dialects": [{"dialect": "SNOWFLAKE", "expression": "1"}]}}
               for i in range(205)]
    got = op.check({"version": "0.2.0.dev0", "name": "m", "datasets": [{"name": "d", "source": "s"}],
                    "metrics": metrics}, "fabric-semantic-view")
    assert got["more"] == 5 and len(got["diagnostics"]) == 201
    assert got["diagnostics"][-1]["message"] == "5 more problems not shown"


def test_a_file_that_isnt_text_or_bytes_is_refused_by_name():
    with pytest.raises(TypeError) as caught:
        op.validate_files({"a.yaml": 5})
    assert str(caught.value) == "a.yaml should be text or bytes"


@pytest.mark.parametrize("count, listed", [(5, "x0.yml, x1.yml, x2.yml, x3.yml, x4.yml"),
                                           (6, "x0.yml, x1.yml, x2.yml, x3.yml, x4.yml, …")])
def test_ignored_files_are_named_five_at_most(count, listed):
    got = op.validate_files({"a.yaml": yaml(), **{f"x{i}.yml": "" for i in range(count)}})
    (w,) = [w for w in got["warnings"] if w["rule"] == "fabric/yml-ignored"]
    assert w["message"] == f"Fabric ignores files without the .yaml extension, so these are left out: {listed}"


@pytest.mark.parametrize("count, listed", [(5, "b0.yaml, b1.yaml, b2.yaml, b3.yaml, b4.yaml"),
                                           (6, "b0.yaml, b1.yaml, b2.yaml, b3.yaml, b4.yaml, …")])
def test_files_that_cant_be_read_are_named_five_at_most_and_each_is_invalid(count, listed):
    got = op.validate_files({"a.yaml": yaml(), **{f"b{i}.yaml": "a: [" for i in range(count)}})
    (note,) = got["merged"]["notes"]
    assert note["message"] == (f"{count} file(s) couldn't be read and aren't in the merged check; references to them "
                               f"may show as unresolved: {listed}")
    assert got["files"]["a.yaml"]["status"] == "Valid" and got["files"]["b0.yaml"]["status"] == "Invalid"
    assert got["status"] == "Invalid"


def test_past_twenty_files_the_rest_are_counted_as_not_checked():
    got = op.validate_files({f"f{i:02d}.yaml": yaml(i) for i in range(21)})
    (w,) = [w for w in got["warnings"] if w["rule"] == "fabric/not-checked"]
    assert (w["value"], w["message"]) == ("1", "only the first 20 .yaml files were checked one by one; 1 more weren't")


def test_a_file_missing_its_name_or_version_is_told_what_to_add():
    got = op.validate_files({"a.yaml": "name: m\n", "b.yaml": "version: 0.2.0.dev0\n"})
    fixes = {d["path"]: d["fix"] for f in got["files"].values() for d in f["diagnostics"]}
    assert fixes["$.version"] == "Add version: 0.2.0.dev0." and fixes["$.name"] == "Add name."


def test_a_problem_with_the_views_name_is_shown_in_the_first_file():
    got = op.validate_files({"a.yaml": yaml().replace("name: m0", "name: 5")})
    assert got["diagnostics"] == []
    assert [(d["path"], d["rule"]) for d in got["files"]["a.yaml"]["diagnostics"]] == [("$.name", "type")]


def test_yaml_over_the_limit_is_invalid_not_unchecked():
    got = op.validate_files({"a.yaml": "x" * 16_000_001})
    assert (got["status"], got["files"], got["files_checked"]) == ("Invalid", {}, False)


def test_a_set_of_files_names_its_own_rules():
    assert op.validate_files({"a.yaml": yaml()})["rules"] == [
        "fabric/file-count", "fabric/yml-ignored", "fabric/yaml-size", "fabric/tmdl-size", "fabric/fragment",
        "fabric/merged", "fabric/duplicate-name", "fabric/description", "fabric/datasets-inferred", "fabric/conflict"]


@pytest.mark.parametrize("places, said", [(1, "w"), (2, "w (and in 1 other place)"), (3, "w (and in 2 other places)")])
def test_a_summary_counts_the_other_places(places, said):
    (row,) = op._summary("r", [(f"p{i}",) for i in range(places)], "w", "f")
    assert row["message"] == said


def test_values_nested_too_deeply_to_compare_are_not_the_same():
    a, b = [], []
    for _ in range(100_000):
        a, b = [a], [b]
    assert op._same(a, b) is False
    assert op._same([1], [1]) is True
