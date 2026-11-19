"""An Ossie document checked against the pinned Apache Ossie JSON Schema (OSSIE-003a, ossie_validate.validate).

The schemas for 0.2.0.dev0 (what the vendored converter reads and writes) and 0.3.0.dev0 are vendored with their
commits and hashes. Required scenarios: 17 (malformed and hostile documents are refused cleanly by the existing
limits) and 19 (offline: nothing is fetched, and a remote ``$ref`` stays unresolved). Each required-field and
additionalProperties breach is one diagnostic with a JSON path."""

import copy
import hashlib
import json
import logging
import re
import socket
import urllib.request
from pathlib import Path

import pytest

from fabric_desktop import model_ossie, ossie_validate
from test_model_ossie import _bim, _ossie, svc  # noqa: F401 - svc is a fixture

SIDECAR = Path(__file__).resolve().parents[1]
VENDOR = ossie_validate.FOLDER
FIXTURES = SIDECAR / "tests" / "fixtures" / "ossie_schema"
EXAMPLES = sorted(FIXTURES.glob("*/*.yaml"))


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True  # the converter also logs each warning
    yield
    logging.getLogger("ossie_microsoft").disabled = False


@pytest.fixture
def offline(monkeypatch):
    """Any attempt to reach the network fails the test."""
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    ossie_validate._validator.cache_clear()  # the schema is read and the validator built under the patch
    yield attempts
    ossie_validate._validator.cache_clear()


def _doc():
    """The converter's own output for the test model, as plain JSON values."""
    return json.loads(json.dumps(model_ossie.parse(model_ossie.export(_bim())["yaml"])))


def _schema(version="0.2.0.dev0"):
    return json.loads((VENDOR / ossie_validate.SCHEMAS[version]["file"]).read_text(encoding="utf-8"))


def _rules(result, rule):
    return [d for d in result["diagnostics"] if d["rule"] == rule]


# ---- the vendored files

def test_the_readme_hashes_match_the_files():
    readme = (VENDOR / "README.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| `([^`]+)` \| `([0-9a-f]{64})` \|$", readme, re.M)
    assert len(rows) == 4 + len(EXAMPLES)
    for name, digest in rows:
        path = SIDECAR / name if name.startswith("tests/") else VENDOR / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    named = {name for name, _ in rows}
    assert {f"tests/fixtures/ossie_schema/{p.parent.name}/{p.name}" for p in EXAMPLES} <= named
    assert {p.name for p in VENDOR.iterdir() if p.suffix == ".json" or p.name in ("LICENSE", "NOTICE")} <= named


def test_the_readme_names_each_pinned_commit_and_url():
    readme = (VENDOR / "README.md").read_text(encoding="utf-8")
    for version in ossie_validate.SCHEMAS:
        info = ossie_validate.schema_info(version)
        assert re.fullmatch(r"[0-9a-f]{40}", info["commit"])
        assert info["url"] in readme and info["commit"] in readme


@pytest.mark.parametrize("version", sorted(ossie_validate.SCHEMAS))
def test_each_schema_is_the_version_it_is_filed_under(version):
    schema = _schema(version)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["version"]["const"] == version


def test_the_converters_version_has_a_pinned_schema():
    assert model_ossie.OSSIE_VERSION == "0.2.0.dev0"
    assert model_ossie.OSSIE_VERSION in ossie_validate.SCHEMAS


def test_the_vendored_license_is_apache_2():
    assert "Apache License" in (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert "Apache Ossie" in (VENDOR / "NOTICE").read_text(encoding="utf-8")


# ---- what validates

@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_the_upstream_examples_validate(path):
    result = ossie_validate.validate(path.read_text(encoding="utf-8"))
    assert result["diagnostics"] == []
    assert result["valid"] and result["checked"] and result["version"] == path.parent.name
    assert result["schema"] == ossie_validate.schema_info(path.parent.name)


def test_there_are_upstream_examples_for_each_version():
    assert {p.parent.name for p in EXAMPLES} == set(ossie_validate.SCHEMAS)


@pytest.mark.parametrize("translate_dax", [True, False])
def test_the_converters_output_validates_against_its_versions_schema(translate_dax):
    out = model_ossie.export(_bim(), translate_dax=translate_dax)
    result = ossie_validate.validate(out["yaml"])
    assert result["diagnostics"] == []
    assert result["valid"] and result["version"] == model_ossie.OSSIE_VERSION
    assert result["schema"]["commit"] == ossie_validate.SCHEMAS["0.2.0.dev0"]["commit"]


def test_a_file_another_tool_wrote_validates():
    assert ossie_validate.validate(_ossie())["valid"]


def test_a_parsed_document_validates_like_its_text():
    out = model_ossie.export(_bim())["yaml"]
    assert ossie_validate.validate(model_ossie.parse(out)) == ossie_validate.validate(out)


# ---- each breach is one diagnostic with a path

def _required(schema, name):
    return schema["$defs"][name]["required"]


@pytest.mark.parametrize("where, target", [
    ("$", lambda d: d),
    ("$.datasets[0]", lambda d: d["datasets"][0]),
    ("$.datasets[1].fields[2]", lambda d: d["datasets"][1]["fields"][2]),
    ("$.relationships[0]", lambda d: d["relationships"][0]),
])
def test_each_missing_required_property_is_one_diagnostic(where, target):
    kinds = {"$": "SemanticModel", "$.datasets[0]": "Dataset", "$.datasets[1].fields[2]": "Field",
             "$.relationships[0]": "Relationship"}
    for prop in _required(_schema(), kinds[where]):
        if where == "$" and prop == "version":
            continue  # without a version there is no schema to pick: its own test below
        doc = _doc()
        del target(doc)[prop]
        result = ossie_validate.validate(doc)
        assert [(d["path"], d["rule"]) for d in result["diagnostics"]] == [(f"{where}.{prop}", "required")], prop
        assert not result["valid"]
        d = result["diagnostics"][0]
        assert d["value"] == "(missing)" and prop in d["fix"] and d["message"]


def test_several_missing_properties_are_one_diagnostic_each():
    doc = _doc()
    dataset = doc["datasets"][0]
    del dataset["name"], dataset["source"]
    result = ossie_validate.validate(doc)
    assert [(d["path"], d["rule"]) for d in result["diagnostics"]] == [
        ("$.datasets[0].name", "required"), ("$.datasets[0].source", "required")]


@pytest.mark.parametrize("where, target", [
    ("$", lambda d: d),
    ("$.datasets[0]", lambda d: d["datasets"][0]),
    ("$.datasets[1].fields[2]", lambda d: d["datasets"][1]["fields"][2]),
    ("$.relationships[0]", lambda d: d["relationships"][0]),
    ("$.datasets[0].fields[0].expression", lambda d: d["datasets"][0]["fields"][0]["expression"]),
])
def test_each_unexpected_property_is_one_diagnostic(where, target):
    doc = _doc()
    target(doc)["colour"] = "blue"
    target(doc)["weight"] = 3
    result = ossie_validate.validate(doc)
    assert [(d["path"], d["rule"], d["value"]) for d in result["diagnostics"]] == [
        (f"{where}.colour", "additionalProperties", "'blue'"), (f"{where}.weight", "additionalProperties", "3")]
    assert all("custom_extensions" in d["fix"] for d in result["diagnostics"])  # 0.2's name for vendor data


def test_a_0_3_document_is_told_its_own_extensions_key():
    doc = _doc()
    doc["version"] = "0.3.0.dev0"
    doc.pop("custom_extensions", None)
    for ds in doc["datasets"]:
        ds.pop("custom_extensions", None)
        for f in ds.get("fields") or []:
            f.pop("custom_extensions", None)
        for m in ds.get("metrics") or []:
            m.pop("custom_extensions", None)
    for m in doc.get("metrics") or []:
        m.pop("custom_extensions", None)
    for r in doc.get("relationships") or []:
        r.pop("custom_extensions", None)
    assert ossie_validate.validate(doc)["valid"]
    doc["custom_extensions"] = []
    (d,) = ossie_validate.validate(doc)["diagnostics"]
    assert d["path"] == "$.custom_extensions" and "nonportable_extensions" in d["fix"]


def test_diagnostics_are_sorted_by_path_with_list_indexes_in_number_order():
    doc = _doc()
    fields = doc["datasets"][0]["fields"]
    for i in range(12):
        fields.append(dict(copy.deepcopy(fields[0]), name=f"f{i}", extra=i))
    result = ossie_validate.validate(doc)
    paths = [d["path"] for d in result["diagnostics"]]
    indexes = [int(re.search(r"fields\[(\d+)\]", p).group(1)) for p in paths]
    assert indexes == sorted(indexes) and len(paths) == 12


def test_wrong_types_and_values_name_their_rule():
    doc = _doc()
    doc["name"] = 5
    doc["datasets"][0]["fields"][0]["datatype"] = "Nope"
    doc["datasets"][0]["fields"][1]["expression"] = {}
    doc["datasets"][1]["fields"][2]["ai_context"] = {"synonyms": 5}
    doc["datasets"][0]["ai_context"] = 5
    result = ossie_validate.validate(doc)
    got = {d["path"]: d["rule"] for d in result["diagnostics"]}
    assert got == {"$.name": "type", "$.datasets[0].fields[0].datatype": "enum",
                   "$.datasets[0].fields[1].expression": "anyOf",
                   "$.datasets[1].fields[2].ai_context.synonyms": "type", "$.datasets[0].ai_context": "oneOf"}
    assert all(d["message"] and d["fix"] for d in result["diagnostics"])


def test_a_document_with_many_breaches_is_cut_with_a_count():
    doc = _doc()
    fields = doc["datasets"][0]["fields"]
    fields.extend(dict(copy.deepcopy(fields[0]), name=f"f{i}", extra=i) for i in range(1000))
    result = ossie_validate.validate(doc)
    assert len(result["diagnostics"]) == ossie_validate.MAX_DIAGNOSTICS + 1
    assert result["more"] == 1000 - ossie_validate.MAX_DIAGNOSTICS
    last = result["diagnostics"][-1]
    assert last["rule"] == "more" and last["message"] == f"{result['more']} more problems not shown"
    assert not result["valid"]


def test_a_long_value_is_cut_short():
    doc = _doc()
    doc["datasets"][0]["fields"][0]["note"] = "x" * 1_000_000
    doc["datasets"][0]["fields"][1]["nested"] = {"a": [list(range(100))] * 50, "b": {"c": {"d": {"e": 1}}}}
    result = ossie_validate.validate(doc)
    assert len(result["diagnostics"]) == 2
    assert all(len(d["value"]) <= ossie_validate.MAX_VALUE for d in result["diagnostics"])


# ---- versions

def test_another_version_is_a_named_diagnostic_never_a_pass():
    doc = _doc()
    doc["version"] = "0.1.1"
    result = ossie_validate.validate(doc)
    assert not result["valid"] and not result["checked"] and result["schema"] is None
    (d,) = result["diagnostics"]
    assert d["path"] == "$.version" and d["rule"] == "version"
    assert d["message"].startswith("no pinned schema for '0.1.1'")


def test_the_older_wrapper_layout_is_a_named_diagnostic():
    result = ossie_validate.validate({"version": "0.1.1", "semantic_model": [{"name": "x"}]})
    assert [d["rule"] for d in result["diagnostics"]] == ["version"] and not result["valid"]


@pytest.mark.parametrize("version", [None, 2, ["0.2.0.dev0"]])
def test_a_version_that_isnt_text_is_named(version):
    result = ossie_validate.validate(dict(_doc(), version=version))
    assert [d["rule"] for d in result["diagnostics"]] == ["version"] and not result["valid"]


def test_a_missing_version_is_named():
    doc = _doc()
    del doc["version"]
    result = ossie_validate.validate(doc)
    assert [(d["path"], d["rule"]) for d in result["diagnostics"]] == [("$.version", "required")]
    assert not result["valid"]


# ---- malformed and hostile documents (scenario 17)

@pytest.mark.parametrize("text, rule", [
    ("", "type"), ("null", "type"), ("[1, 2]", "type"), ("just text", "type"), ("{unclosed: [", "parse"),
])
def test_text_that_isnt_one_model_is_one_diagnostic(text, rule):
    result = ossie_validate.validate(text)
    assert [(d["path"], d["rule"]) for d in result["diagnostics"]] == [("$", rule)] and not result["valid"]


def test_yaml_aliases_and_alias_bombs_are_refused_by_the_loader():
    bomb = "a: &a [x, x, x, x, x, x, x, x, x]\n" + "".join(
        f"{chr(98 + i)}: &{chr(98 + i)} [*{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}]\n"
        for i in range(8))
    for text in ("version: 0.2.0.dev0\nname: &n x\ndatasets: *n\n", bomb):
        (d,) = ossie_validate.validate(text)["diagnostics"]
        assert d["rule"] == "parse" and "aliases" in d["message"]


@pytest.mark.parametrize("text", ["[" * 100_000, '{"a":' * 100_000, "a:\n" + "".join(" " * i + "- \n" for i in range(1, 3000))])
def test_deep_nesting_in_text_is_refused_cleanly(text):
    result = ossie_validate.validate(text)
    assert len(result["diagnostics"]) == 1 and not result["valid"]
    assert result["diagnostics"][0]["rule"] in ("parse", "depth", "type")


def test_deep_nesting_in_a_parsed_document_is_refused_before_the_check_recurses():
    doc = _doc()
    deep: dict = {}
    node = deep
    for _ in range(50_000):  # far beyond the recursion limit; built without recursion
        node["x"] = {}
        node = node["x"]
    doc["datasets"][0]["fields"][0]["ai_context"] = deep
    (d,) = ossie_validate.validate(doc)["diagnostics"]
    assert d["rule"] == "depth" and d["path"].startswith("$.datasets[0].fields[0].ai_context.x")


def test_nesting_just_inside_the_depth_limit_is_checked():
    doc = _doc()
    node = doc["datasets"][0]["fields"][0]["ai_context"] = {}
    for _ in range(ossie_validate.MAX_DEPTH - 6):
        node["x"] = {}
        node = node["x"]
    assert ossie_validate.validate(doc)["valid"]  # ai_context objects allow any extra properties


def test_text_over_the_size_limit_is_refused_by_the_existing_limit(monkeypatch):
    monkeypatch.setattr(model_ossie, "MAX_CHARS", 1_000)
    (d,) = ossie_validate.validate("name: " + "x" * 2_000)["diagnostics"]
    assert d["rule"] == "parse" and "at most" in d["message"]


def test_a_huge_string_inside_the_limit_is_checked_and_shown_short():
    doc = _doc()
    doc["name"] = ["y" * 5_000_000]
    (d,) = ossie_validate.validate(json.dumps(doc))["diagnostics"]
    assert d["path"] == "$.name" and d["rule"] == "type" and len(d["value"]) <= ossie_validate.MAX_VALUE


def test_yaml_values_json_has_no_type_for_are_type_breaches():
    text = model_ossie.export(_bim())["yaml"].replace("name: SM_Gold", "name: 2024-01-01", 1)
    result = ossie_validate.validate(text)
    assert [(d["path"], d["rule"]) for d in result["diagnostics"]] == [("$.name", "type")]


def test_keys_that_arent_text_are_named():
    doc = _doc()
    doc["datasets"][0]["fields"][0][7] = "seven"
    result = ossie_validate.validate(doc)
    assert [(d["path"], d["rule"]) for d in result["diagnostics"]] == [("$.datasets[0].fields[0]", "key")]


def test_odd_property_names_are_quoted_in_the_path():
    doc = _doc()
    doc["datasets"][0]['we"ird key'] = 1
    (d,) = ossie_validate.validate(doc)["diagnostics"]
    assert d["path"] == '$.datasets[0]["we\\"ird key"]'


# ---- offline (scenario 19)

def test_validation_reads_nothing_from_the_network(offline):
    for path in EXAMPLES:
        assert ossie_validate.validate(path.read_text(encoding="utf-8"))["valid"]
    assert ossie_validate.validate(model_ossie.export(_bim())["yaml"])["valid"]
    assert offline == []


@pytest.mark.parametrize("ref", ["http://example.com/schema.json", "https://example.com/defs.json#/$defs/Name",
                                 "other.json"])
def test_a_remote_ref_is_unresolved_and_never_fetched(offline, ref):
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:kratosforge:test",
              "type": "object", "properties": {"name": {"$ref": ref}}}
    result = ossie_validate.check({"name": "x"}, ossie_validate._validator_for(schema), "test")
    (d,) = result["diagnostics"]
    assert d["rule"] == "$ref" and not result["valid"]
    assert offline == []


def test_the_vendored_registry_refuses_a_remote_ref(offline):
    from referencing.exceptions import Unresolvable

    resolver = ossie_validate._validator("0.2.0.dev0")._resolver
    with pytest.raises(Unresolvable):
        resolver.lookup("https://example.com/ossie-schema.json")
    assert resolver.lookup("#/$defs/Dataset").contents == _schema()["$defs"]["Dataset"]
    assert offline == []


def test_a_vendored_schema_with_a_remote_ref_added_is_still_not_fetched(offline):
    schema = _schema()
    schema["$defs"]["Field"]["properties"]["name"] = {"$ref": "https://example.com/name.json"}
    result = ossie_validate.check(_doc(), ossie_validate._validator_for(schema), "0.2.0.dev0")
    assert [d["rule"] for d in result["diagnostics"]] == ["$ref"] and not result["valid"]
    assert offline == []


def test_jsonschema_loads_only_when_a_document_is_checked():
    import subprocess
    import sys

    probe = ("import sys; from fabric_desktop import ossie_validate, model_ossie_service; "
             "print('jsonschema' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", probe], cwd=SIDECAR, capture_output=True, text=True, timeout=120,
                         check=True)
    assert out.stdout.strip() == "False"


# ---- the service

def test_import_preview_returns_the_schema_check(svc):  # noqa: F811 - the fixture
    out = svc.meta_model_ossie_preview("test", model_ossie.export(_bim())["yaml"], "SM_Gold")
    assert out["schema_validation"]["valid"] and out["schema_validation"]["diagnostics"] == []
    doc = model_ossie.parse(_ossie())
    doc["datasets"][0]["colour"] = "blue"
    out = svc.meta_model_ossie_preview("test", json.dumps(doc), "SM_Gold")
    assert [d["path"] for d in out["schema_validation"]["diagnostics"]] == ["$.datasets[0].colour"]
