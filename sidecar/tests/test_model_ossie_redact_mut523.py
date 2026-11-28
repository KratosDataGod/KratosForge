"""Mutation batch 523: what model_ossie_redact withholds from the AI tools, pinned one behaviour at a time.

Each test fails if a one-token change (a constant, a compare, a dropped return) is made to the module: the
shortest value it replaces, each form a message may quote a value in, the longest-first order, which rows about
a role are withheld, and what Export's YAML and Import preview's answer keep."""

import json

import yaml

from fabric_desktop import model_ossie, model_ossie_redact as r
from fabric_desktop.semantic_parity import _fingerprint
from test_model_ossie import _bim

UPN = "ana.secret@contoso.example"


def _secured(member_id="5f0c1d2e-member-id-91ab"):
    bim = _bim()
    role = bim["model"]["roles"][0]
    role["tablePermissions"][0]["filterExpression"] = '[country] = "Ruritania-West-7731"'
    role["members"] = [{"memberName": UPN, "memberId": member_id, "identityProvider": "AzureAD"}]
    return bim


def _scrubbed(value, text):
    return r._scrub(text, r._needles({value}))


def _mark(value):
    return f"<withheld: {_fingerprint(value)}>"


def test_a_six_character_member_id_is_the_shortest_value_withheld():
    bim = _secured(member_id="ab-123")
    out = model_ossie.export(bim, translate_dax=True, validate=True)
    out["path"] = "models/ab-123.ossie.yaml"
    result = r.export_result(out, bim)
    assert "ab-123" not in json.dumps(result, ensure_ascii=False)
    assert result["path"] == f"models/{_mark('ab-123')}.ossie.yaml"


def test_a_value_shorter_than_six_characters_is_left_where_it_is_quoted():
    assert r._sensitive([{"name": "r", "members": [{"memberName": "ab-12", "memberId": "abc123"}]}]) == {"abc123"}
    assert r._needles({"ab-12"}) == []


def test_a_value_cut_at_80_characters_is_withheld_in_each_way_a_message_quotes_it():
    value = "Rurit\x01ania-" + "x" * 90  # a control character, so repr and JSON write it differently
    plain_cut = value[:80] + "..."
    repr_cut = model_ossie._shown(value)[1:-1]  # as a role change message quotes a long filter
    assert _scrubbed(value, f"filter {plain_cut}") == f"filter {_mark(value)}..."
    assert _scrubbed(value, f"filter {repr_cut}") == f"filter {_mark(value)}..."


def test_a_value_is_withheld_where_it_is_quoted_as_repr_writes_it():
    for value in ("Rurit\x01ania-West", "Rurit\x01ania-" + "x" * 90):  # under and over the 80-character cut
        assert _scrubbed(value, "detail: " + repr(value)[1:-1] + " end") == f"detail: {_mark(value)} end"


def test_a_value_is_withheld_where_it_is_quoted_as_json_writes_it():
    value = "Ruritánia-West"  # JSON writes the á as \\u00e1, repr keeps it
    assert _scrubbed(value, "detail: " + json.dumps(value)[1:-1] + " end") == f"detail: {_mark(value)} end"


def test_the_longest_form_of_a_value_is_replaced_first():
    value = 'say "Ruritania"'  # JSON escapes the quotes, so its form is the longer one
    forms = [form for form, _ in r._needles({value})]
    assert forms == [json.dumps(value)[1:-1], value]


def test_numbers_flags_and_nulls_are_kept_as_they_are():
    kept = {"rows": 3, "share": 1.5, "ok": True, "none": None, "list": [0, False]}
    assert r._scrub(kept, r._needles({UPN})) == kept


def _report(*objects, **extra):
    return {"objects": list(objects), "warnings": [], **extra}


def _sides():
    bim = _secured()
    return bim, yaml.safe_load(model_ossie.export(bim)["yaml"])


def test_only_the_converters_row_about_the_roles_collection_is_kept_on_a_role():
    collection = {"object": "model", "kind": "lost", "message": "the model's ('roles') came through in part"}
    other_model = {"object": "model", "kind": "lost", "message": "something else"}
    not_model = {"object": "table 'x'", "kind": "lost", "message": "the ('roles') again"}
    role = {"object": "role 'EU only'", "kind": "role", "evidence": [collection, other_model, not_model]}
    out = r.fidelity(_report(role), *_sides())
    kept, *withheld = out["objects"][0]["evidence"]
    assert kept == collection
    assert all(set(w) == {"object", "kind", "withheld"} for w in withheld)


def test_a_row_about_a_role_is_withheld_from_another_objects_evidence_and_the_rest_is_kept():
    about_role = {"object": "role 'EU only'", "kind": "lost", "message": "anything"}
    plain = {"object": "measure 'Total'", "check": "text", "detail": "plain"}
    measure = {"object": "measure 'Total'", "kind": "measure", "evidence": [about_role, plain]}
    out = r.fidelity(_report(measure), *_sides())
    first, second = out["objects"][0]["evidence"]
    assert first == {"object": "role 'EU only'", "kind": "lost", "withheld": _fingerprint(model_ossie._canon(about_role))}
    assert second == plain


def test_the_metadata_changes_list_notes_as_informational():
    table = {"object": "table 'x'", "kind": "table", "changes": ["c1"], "notes": ["n1"], "evidence": []}
    out = r.fidelity(_report(table, metadata_changes=[]), *_sides())
    assert out["metadata_changes"] == [{"object": "table 'x'", "change": "c1"},
                                       {"object": "table 'x'", "change": "n1", "informational": True}]


def test_the_yaml_withholds_only_the_power_bi_sections_roles_and_writes_its_text_as_is():
    stash = {"roles": [{"name": "Région", "tablePermissions": [
        {"name": "t", "metadataPermission": "read"}, {"name": "u", "filterExpression": "[a] = 1"}],
        "members": [{"memberName": UPN, "identityProvider": "AzureAD"}]}]}
    doc = {"name": "m", "custom_extensions": [{"vendor_name": "OTHER", "data": "keep"},
                                              {"vendor_name": "POWER_BI", "data": json.dumps(stash)}]}
    out = r.document(doc)
    assert out["custom_extensions"][0] == {"vendor_name": "OTHER", "data": "keep"}
    data = out["custom_extensions"][1]["data"]
    assert '"Région"' in data  # not written as \\u00e9
    role = json.loads(data)["roles"][0]
    assert role["tablePermissions"] == [{"name": "t", "metadataPermission": "read"},
                                        {"name": "u", "filterExpression": _fingerprint("[a] = 1")}]
    assert role["members"] == [{"memberName": _fingerprint(UPN), "identityProvider": "AzureAD"}]
    assert doc["custom_extensions"][1]["data"] == json.dumps(stash)  # the caller's document is unchanged


def test_a_preview_with_no_fidelity_report_still_withholds_the_rest_and_says_so():
    bim, doc = _sides()
    result = r.preview_result({"fidelity": None, "changes": [f"member {UPN} added"]}, doc, bim)
    assert result["fidelity"] is None
    assert result["changes"] == [f"member {_mark(UPN)} added"]
    assert result["note"] == r.NOTE
