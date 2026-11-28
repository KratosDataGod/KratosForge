"""Edges of model_bpa that the main tests leave open (mutation batch 75)."""

import hashlib
import json

from fabric_desktop import model_bpa


def test_the_check_notebook_is_nbformat_4_5():
    nb = model_bpa.bpa_ipynb("ws1", "meta1")
    assert (nb["nbformat"], nb["nbformat_minor"]) == (4, 5)


def test_the_notebook_hash_ignores_key_order():
    a = {"b": 1, "a": [1, 2], "c": {"y": 1, "x": 2}}
    b = {"c": {"x": 2, "y": 1}, "a": [1, 2], "b": 1}
    assert model_bpa.notebook_hash(a) == model_bpa.notebook_hash(b)
    want = hashlib.sha1(json.dumps(a, sort_keys=True).encode(), usedforsecurity=False).hexdigest()[:12]
    assert model_bpa.notebook_hash(b) == want


def test_a_saved_errors_type_is_capped_at_200_characters():
    err = model_bpa.error({"error": {"type": "T" * 500, "message": "m"}})
    assert err["type"] == "T" * 200
    assert model_bpa.error({"error": {"type": "U" * 200}})["type"] == "U" * 200


def test_a_finding_with_empty_or_missing_fields_reads_as_empty_text():
    row = {"Severity": "Error", "Rule Name": None, "Category": "", "Object Type": None,
           "Object Name": "", "Description": None, "URL": ""}
    out = model_bpa.findings({"rows": [row]})
    (f,) = out["findings"]
    assert {k: f[k] for k in ("rule", "category", "object_type", "object", "description", "url")} == {
        "rule": "", "category": "", "object_type": "", "object": "", "description": "", "url": ""}


def test_a_finding_keeps_its_filled_fields_as_text():
    row = {"Severity": "Warning", "Rule Name": "R", "Category": "C", "Object Type": "Table",
           "Object Name": "T1", "Description": "D", "URL": "https://example.invalid/r"}
    (f,) = model_bpa.findings({"rows": [row]})["findings"]
    assert (f["rule"], f["category"], f["object_type"], f["object"], f["description"], f["url"]) == (
        "R", "C", "Table", "T1", "D", "https://example.invalid/r")


def test_falsy_non_text_fields_read_as_empty_text():
    row = {"Severity": "Info", "Rule Name": 0, "Category": False, "Object Type": [],
           "Object Name": {}, "Description": 0.0, "URL": None}
    (f,) = model_bpa.findings({"rows": [row]})["findings"]
    assert [f[k] for k in ("rule", "category", "object_type", "object", "description", "url")] == [""] * 6
