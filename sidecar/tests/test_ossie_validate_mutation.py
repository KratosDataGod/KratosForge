"""Ossie schema check: what the first mutation run of ossie_validate.py left alive (batch 335)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import ossie_validate as ov


def err(rule: str, expected, instance=None, path=(), schema=None, context=None):
    return SimpleNamespace(validator=rule, validator_value=expected, instance=instance, absolute_path=list(path),
                           schema=schema or {}, context=context, relative_schema_path=[])


class FakeValidator:
    def __init__(self, n: int):
        self.n = n

    def iter_errors(self, doc):
        for i in range(self.n):
            yield err("type", "string", instance=i, path=("datasets", i))


def test_type_names_read_as_words():
    assert ov._type_names("integer") == "a whole number"
    assert ov._type_names(["string", "null"]) == "text or null"
    assert ov._type_names(("object", "custom")) == "an object or custom"
    assert ov._type_names(None) == ""


@pytest.mark.parametrize("rule, expected, message, fix", [
    ("const", "1.0", "should be '1.0'", "Write '1.0'."),
    ("minLength", 1, "should not be empty", "Fill it in."),
    ("minLength", 3, "should be at least 3 characters", "Write a longer value."),
    ("maxLength", 9, "should be at most 9 characters", "Write a shorter value."),
    ("minItems", 1, "should have at least 1 item", "Add the missing items."),
    ("minItems", 2, "should have at least 2 items", "Add the missing items."),
    ("maxItems", 4, "should have at most 4 items", "Take out the extra items."),
    ("uniqueItems", True, "has the same item twice", "Take out the repeated item."),
    ("pattern", "^a+$", "should match '^a+$'", "Change the value to match the pattern."),
    ("minimum", 0, "breaks minimum 0", "Change the number."),
    ("maximum", 9, "breaks maximum 9", "Change the number."),
    ("exclusiveMinimum", 0, "breaks exclusiveMinimum 0", "Change the number."),
    ("exclusiveMaximum", 9, "breaks exclusiveMaximum 9", "Change the number."),
    ("dependentRequired", {}, "breaks the schema's dependentRequired rule",
     "Change it to match the Ossie specification."),
])
def test_each_rule_has_its_own_message_and_fix(rule, expected, message, fix):
    assert ov._message(err(rule, expected, instance="x")) == (message, fix)


def test_a_missing_property_says_where_unless_at_the_top():
    (d,) = ov._flatten(err("required", ["name"], instance={}, path=("datasets", 0)), "ext")
    assert d["message"] == "is required in $.datasets[0]" and d["path"] == "$.datasets[0].name"
    (d,) = ov._flatten(err("required", ["name"], instance={}), "ext")
    assert d["message"] == "is required"


def test_at_most_200_diagnostics_are_shown_then_one_more_line():
    diags = [ov._diag(("datasets", i), "type", "1", "m", "f") for i in range(201)]
    out = ov._result("0.3.0.dev0", diags, True)
    assert len(out["diagnostics"]) == 201 and out["more"] == 1
    assert out["diagnostics"][-1]["message"] == "1 more problems not shown"
    diags = [ov._diag(("datasets", i), "type", "1", "m", "f") for i in range(200)]
    out = ov._result("0.3.0.dev0", diags, True)
    assert len(out["diagnostics"]) == 200 and out["more"] == 0


def test_the_check_stops_after_5000_schema_errors():
    diags, total, stopped = ov.schema_errors({}, FakeValidator(5000), "0.3.0.dev0")
    assert (total, stopped) == (5000, False)
    diags, total, stopped = ov.schema_errors({}, FakeValidator(5001), "0.3.0.dev0")
    assert (total, stopped) == (5000, True)


def test_a_short_form_is_at_most_80_characters():
    assert len(ov.short("x" * 500)) == 70  # reprlib keeps 80 - 10 characters of a string
    assert ov._short_text("y" * 80) == "y" * 80
    assert ov._short_text("y" * 81) == "y" * 79 + "…"


def nested(depth: int) -> dict:
    doc: dict = {}
    node = doc
    for _ in range(depth):
        node["a"] = {}
        node = node["a"]
    return doc


def test_nesting_of_64_levels_is_allowed_and_65_isnt():
    assert ov._shape(nested(64)) == []
    (d,) = ov._shape(nested(65))
    assert d["rule"] == "depth" and d["message"] == "nested more than 64 levels deep"


def test_a_schema_of_100000_objects_and_lists_is_inlined_and_one_more_isnt():
    def schema(n: int) -> dict:  # the root and its list are two of the n
        return {"items": [{} for _ in range(n - 2)]}

    small = schema(100_000)
    assert ov.inline_local_refs(small) is not small
    big = schema(100_001)
    assert ov.inline_local_refs(big) is big


def test_only_any_of_and_one_of_read_their_branches():
    (d,) = ov._flatten(err("allOf", [], instance=1, context=[err("type", "string", instance=1)]), "ext")
    assert d["message"] == "breaks the schema's allOf rule"
    (d,) = ov._flatten(err("anyOf", [], instance=1, context=[err("type", "string", instance=1)]), "ext")
    assert d["message"] == "matches none of its allowed forms: should be text"


class LongRepr:
    def __repr__(self) -> str:
        return "L" * 200


def test_a_short_form_cuts_each_kind_of_value():
    assert ov.short([[[1]]]) == "[[[...]]]"
    assert ov.short(list(range(6))) == "[0, 1, 2, 3, ...]"
    assert ov.short(tuple(range(6))) == "(0, 1, 2, 3, ...)"
    assert ov.short(set(range(6))) == "{0, 1, 2, 3, ...}"
    assert ov.short({i: i for i in range(6)}) == "{0: 0, 1: 1, 2: 2, 3: 3, ...}"
    assert len(ov.short(LongRepr())) == 70
    assert len(ov.short(10 ** 60)) == 40


def test_a_document_nested_too_deep_keeps_its_version_when_it_is_text():
    doc = nested(70)
    doc["version"] = "0.3.0.dev0"
    out = ov.validate(doc)
    assert out["version"] == "0.3.0.dev0" and out["diagnostics"][0]["rule"] == "depth"
    doc["version"] = 3
    assert ov.validate(doc)["version"] == ""


class RaisingValidator:
    def __init__(self, exc: BaseException):
        self.exc = exc

    def iter_errors(self, doc):
        raise self.exc
        yield  # a generator, as jsonschema's is


def test_a_check_that_ran_says_so():
    out = ov.check({}, FakeValidator(0), "0.3.0.dev0")
    assert out["checked"] is True and out["valid"] is True and out["status"] == "Valid"
    assert out["schema"]["version"] == "0.3.0.dev0"


def test_the_kept_list_is_cut_to_200_only_once_it_passes_800():
    diags, total, stopped = ov.schema_errors({}, FakeValidator(1000), "0.3.0.dev0")
    assert total == 1000 and len(diags) == 399  # cut to 200 at the 801st, then 199 more
    diags, total, _ = ov.schema_errors({}, FakeValidator(300), "0.3.0.dev0")
    assert total == 300 and len(diags) == 300


def test_an_unresolvable_ref_and_a_recursion_each_count_as_one_problem():
    from referencing.exceptions import Unresolvable

    diags, total, stopped = ov.schema_errors({}, RaisingValidator(Unresolvable(ref="https://example.com/x")),
                                             "0.3.0.dev0")
    assert total == 1 and diags[0]["rule"] == "$ref" and not stopped
    diags, total, _ = ov.schema_errors({}, RaisingValidator(RecursionError()), "0.3.0.dev0")
    assert total == 1 and diags[0]["rule"] == "depth"


def test_an_enum_lists_twelve_values_then_an_ellipsis():
    message, _ = ov._message(err("enum", list(range(13)), instance=99))
    assert message == "should be one of 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, …"
    message, _ = ov._message(err("enum", ["a", "b"], instance="c"))
    assert message == "should be one of 'a', 'b'"


def test_a_check_that_stopped_says_at_least_one_more_even_with_few_shown():
    out = ov._result("0.3.0.dev0", [ov._diag(("a",), "type", "1", "m", "f")], True, total=1, stopped=True)
    assert out["more"] == 1 and out["diagnostics"][-1]["message"] == "at least 1 more problems not shown"


def test_a_document_that_isnt_an_object_isnt_checked():
    out = ov.validate([1, 2])
    assert out["checked"] is False and out["status"] == "Not checked" and out["diagnostics"][0]["rule"] == "type"


def test_a_document_nested_too_deep_isnt_checked():
    out = ov.validate(nested(70) | {"version": "0.3.0.dev0"})
    assert out["checked"] is False and out["status"] == "Not checked"


def test_a_key_that_isnt_text_is_reported_under_the_detected_profile_without_a_schema_check():
    from fabric_desktop import ossie_profiles

    doc = {"version": "0.3.0.dev0", "name": "m", "datasets": [], 1: "x"}
    want = ossie_profiles.detect_profile({k: v for k, v in doc.items() if isinstance(k, str)})
    out = ov.validate(doc)
    assert out["checked"] is False and out["profile"] == want and out["status"] == "Invalid"
    assert [d["rule"] for d in out["diagnostics"]] == ["key"]


def test_required_names_only_the_missing_properties():
    assert ov._message(err("required", ["a", "b"], instance={"a": 1})) == ("needs 'b'", "Add b.")


def test_a_document_without_a_version_or_that_cant_be_read_isnt_checked():
    out = ov.validate({"name": "m"})
    assert out["checked"] is False and out["status"] == "Not checked" and out["diagnostics"][0]["path"] == "$.version"
    out = ov.validate("a: [")
    assert out["checked"] is False and out["status"] == "Not checked" and out["diagnostics"][0]["rule"] == "parse"


def test_diagnostics_sort_list_positions_by_number_before_names():
    diags = [ov._diag(("datasets", p), "type", "1", "m", "f") for p in ("x", 10, 2)]
    out = ov._result("0.3.0.dev0", diags, True)
    assert [d["path"] for d in out["diagnostics"]] == ["$.datasets[2]", "$.datasets[10]", "$.datasets.x"]


def nested_lists(depth: int) -> list:
    doc: list = []
    node = doc
    for _ in range(depth):
        node.append([])
        node = node[0]
    return doc


def test_nesting_in_lists_counts_too():
    assert ov._shape(nested_lists(64)) == []
    (d,) = ov._shape(nested_lists(65))
    assert d["rule"] == "depth" and d["path"].startswith("$[0][0]")


def test_a_path_keeps_letters_that_arent_ascii():
    assert ov.json_path(("é-x",)) == '$["é-x"]'


def test_a_key_that_isnt_text_is_named_as_text_in_the_paths_below_it():
    found = ov._shape({1: {2: "x"}})
    assert sorted(d["path"] for d in found) == ["$", '$["1"]']


def test_any_of_reports_the_branch_with_fewest_problems_by_branch():
    def e(branch: int, key: str):
        return SimpleNamespace(validator="type", validator_value="string", instance=1, absolute_path=[key],
                               schema={}, context=None, relative_schema_path=[branch, "properties", key, "type"])

    error = err("anyOf", [], instance={}, context=[e(0, "a"), e(0, "b"), e(1, "c")])
    assert [d["path"] for d in ov._flatten(error, "ext")] == ["$.c"]


def test_an_unknown_version_shows_the_version_as_its_value():
    (d,) = ov.validate({"version": "9.9", "name": "m", "datasets": []})["diagnostics"]
    assert (d["path"], d["rule"], d["value"]) == ("$.version", "version", "'9.9'")


def test_a_list_of_exactly_800_isnt_cut():
    diags, total, _ = ov.schema_errors({}, FakeValidator(800), "0.3.0.dev0")
    assert total == 800 and len(diags) == 800


def test_an_enum_of_exactly_twelve_values_has_no_ellipsis():
    message, _ = ov._message(err("enum", list(range(12)), instance=99))
    assert message == "should be one of 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11"


def test_a_value_that_matches_no_form_lists_at_most_six_of_them():
    rules = [("maxLength", 1), ("minLength", 3), ("maxItems", 1), ("minItems", 2), ("const", "a"), ("pattern", "b"),
             ("type", "integer")]
    error = err("oneOf", [], instance="x", context=[err(r, v, instance="x") for r, v in rules])
    (d,) = ov._flatten(error, "ext")
    assert d["message"].count(";") == 5
    assert d["message"] == ("matches none of its allowed forms: should be 'a'; should be a whole number; "
                            "should be at least 3 characters; should be at most 1 characters; "
                            "should have at least 2 items; should have at most 1 items")
