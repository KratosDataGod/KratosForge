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


def _keys(value):
    """Every key anywhere in a JSON value, without recursion."""
    stack, found = [value], set()
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            found.update(v)
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
    return found


@pytest.mark.parametrize("version", sorted(ossie_validate.SCHEMAS))
def test_the_pinned_schemas_use_no_regular_expressions(version):
    # The validator matches patternProperties with Python's re, and jsonschema checks pattern with it too, not
    # with ECMA-262 as JSON Schema specifies. Fine while no pinned schema has one; a schema that adds one trips
    # this, so the dialect is reviewed (and the docs note updated) before it is vendored.
    assert not {"pattern", "patternProperties"} & _keys(_schema(version))


@pytest.mark.parametrize("version", sorted(ossie_validate.SCHEMAS))
def test_the_pinned_schemas_have_nothing_that_inlining_local_refs_could_change(version):
    # inline_local_refs moves subschemas; that is only a pure substitution while no subschema has its own
    # $id, $anchor or dynamic ref (each changes how a ref resolves) and nothing tracks evaluated keywords.
    schema = _schema(version)
    nested = dict(schema)
    nested.pop("$id", None)
    assert "$id" not in _keys(nested["$defs"]) and "$id" not in _keys({k: v for k, v in nested.items() if k != "$defs"})
    assert not {"$anchor", "$dynamicRef", "$dynamicAnchor", "$recursiveRef", "$recursiveAnchor",
                "unevaluatedProperties", "unevaluatedItems"} & _keys(schema)


def _broken_docs():
    """Documents that break the schema in different ways, for comparing two validators."""
    docs = []
    for change in (
        lambda d: d["datasets"][0].pop("name"),
        lambda d: d["datasets"][0]["fields"][0].update(colour="blue", datatype="Nope"),
        lambda d: d["datasets"][0]["fields"][1].update(expression={}),
        lambda d: d["datasets"][1]["fields"][2].update(ai_context={"synonyms": 5}),
        lambda d: d["datasets"][0].update(ai_context=5),
        lambda d: d.update(name=5, relationships="x"),
        lambda d: d["relationships"][0].update(from_columns=[]),
    ):
        doc = _doc()
        change(doc)
        docs.append(doc)
    return docs


@pytest.mark.parametrize("version", sorted(ossie_validate.SCHEMAS))
def test_inlining_the_local_refs_changes_no_result(version):
    schema = _schema(version)
    before = copy.deepcopy(schema)
    inlined = ossie_validate.inline_local_refs(schema)
    assert schema == before  # not changed in place
    assert "$ref" not in json.dumps(inlined["properties"])  # every local ref resolved: none is cyclic
    plain, fast = ossie_validate._validator_for(schema), ossie_validate._validator_for(inlined)
    examples = [model_ossie.load_text(p.read_text(encoding="utf-8")) for p in EXAMPLES if p.parent.name == version]
    for doc in _broken_docs() + examples:
        doc["version"] = version
        assert ossie_validate.check(doc, fast, version) == ossie_validate.check(doc, plain, version)


def test_inlining_leaves_other_refs_and_cycles_as_refs():
    schema = {"$defs": {"Tree": {"type": "object", "properties": {"child": {"$ref": "#/$defs/Tree"}}}},
              "properties": {"t": {"$ref": "#/$defs/Tree"}, "r": {"$ref": "https://example.com/x.json"},
                             "s": {"$ref": "#/$defs/Tree", "description": "kept beside its ref"},
                             "d": {"$ref": "#/$defs/Missing"}}}
    props = ossie_validate.inline_local_refs(schema)["properties"]
    assert props["t"]["properties"]["child"] == {"$ref": "#/$defs/Tree"}
    assert props["r"] == {"$ref": "https://example.com/x.json"} and props["d"] == {"$ref": "#/$defs/Missing"}
    assert props["s"] == schema["properties"]["s"]


def test_the_vendored_license_is_apache_2():
    assert "Apache License" in (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert "Apache Ossie" in (VENDOR / "NOTICE").read_text(encoding="utf-8")


# ---- what validates

@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_the_upstream_examples_validate(path):
    result = ossie_validate.validate(path.read_text(encoding="utf-8"))
    assert result["diagnostics"] == []
    assert result["valid"] and result["checked"] and result["version"] == path.parent.name
    assert result["effect"] == "informs" and result["stopped"] is False
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


def test_a_file_wrong_everywhere_stops_after_the_raw_error_cap(monkeypatch):
    doc = _doc()
    template = doc["datasets"][0]
    field = dict(copy.deepcopy(template["fields"][0]), colour="blue")
    doc["datasets"] = [dict(copy.deepcopy(template), name=f"d{i}", fields=[dict(field, name=f"f{j}") for j in range(50)])
                       for i in range(2_000)]  # 100,000 bad fields
    real, pulled = ossie_validate._validator, []

    class Counting:  # counts the errors the check asks the schema validator for: work, not wall-clock time
        def __init__(self, inner):
            self.inner = inner

        def iter_errors(self, instance):
            for error in self.inner.iter_errors(instance):
                pulled.append(1)
                yield error

    monkeypatch.setattr(ossie_validate, "_validator", lambda version: Counting(real(version)))
    result = ossie_validate.validate(doc)
    assert len(pulled) == ossie_validate.MAX_RAW_ERRORS + 1  # not 100,000: the check stops at the cap
    assert result["stopped"] and not result["valid"]
    assert len(result["diagnostics"]) == ossie_validate.MAX_DIAGNOSTICS + 1
    assert result["more"] == ossie_validate.MAX_RAW_ERRORS - ossie_validate.MAX_DIAGNOSTICS
    last = result["diagnostics"][-1]
    assert last["rule"] == "more" and last["message"] == f"at least {result['more']} more problems not shown"
    assert result["diagnostics"][0]["path"] == "$.datasets[0].fields[0].colour"


def test_under_the_raw_error_cap_the_count_is_exact():
    result = ossie_validate.validate(_doc())
    assert result["stopped"] is False and result["more"] == 0


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
    # OSSIE-003b: Snowflake's 0.1.1 wrapper is recognized; with no 0.1.1 schema it is a note, never a pass
    result = ossie_validate.validate({"version": "0.1.1", "semantic_model": [{"name": "x"}]})
    assert result["diagnostics"] == [] and [d["rule"] for d in result["notes"]] == ["unverified"]
    assert not result["valid"] and result["status"] == "Unverified"


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


@pytest.mark.parametrize("text", ["[" * 100_000, '{"a":' * 100_000, "a:\n" + "".join(" " * i + "- \n" for i in range(1, 3000))],
                         ids=["flow-list", "flow-map", "block-list"])  # ids short: Windows caps PYTEST_CURRENT_TEST
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
    inlined = ossie_validate.inline_local_refs(_schema())  # what the check runs on (see the inlining tests)
    assert resolver.lookup("#/$defs/Dataset").contents == inlined["$defs"]["Dataset"]
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
    assert out["schema_validation"]["effect"] == "informs"


@pytest.mark.parametrize("name", ["SM_Gold", ""])  # without a name the file's own name is read from it
def test_the_preview_reads_the_file_once(svc, monkeypatch, name):  # noqa: F811 - the fixture
    text = model_ossie.export(_bim())["yaml"]
    real, reads = model_ossie.load_text, []

    def counting(t):
        if t == text:
            reads.append(1)
        return real(t)

    monkeypatch.setattr(model_ossie, "load_text", counting)
    out = svc.meta_model_ossie_preview("test", text, name)
    assert len(reads) == 1
    assert out["schema_validation"]["valid"] and out["name"] == "SM_Gold"


def test_a_failing_schema_check_never_blocks_the_import(svc):  # noqa: F811 - the fixture
    doc = model_ossie.parse(_ossie())
    doc["datasets"][0]["colour"] = "blue"  # the converter ignores it; the schema doesn't allow it
    text = json.dumps(doc)
    assert not svc.meta_model_ossie_preview("test", text, "SM_Gold")["schema_validation"]["valid"]
    out = svc.meta_model_ossie_import("test", text, "SM_Gold", confirmed=True)
    assert out["name"] == "SM_Gold" and svc.fake.saved


def test_conversion_from_a_parsed_document_matches_conversion_from_its_text():
    text = model_ossie.export(_bim())["yaml"]
    doc = model_ossie.parse(text)
    before = copy.deepcopy(doc)
    assert model_ossie.to_tmsl(doc) == model_ossie.to_tmsl(text)
    assert model_ossie.stashed(doc) == model_ossie.stashed(text)
    assert doc == before  # read only


def test_inlining_a_diamond_of_defs_keeps_the_plain_schema_instead_of_growing_exponentially():
    import time
    defs = {"d0": {"type": "string"}}
    for i in range(1, 25):  # each level refers to the one below twice: 2**24 copies if fully inlined
        defs[f"d{i}"] = {"anyOf": [{"$ref": f"#/$defs/d{i - 1}"}, {"$ref": f"#/$defs/d{i - 1}"}]}
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$defs": defs, "$ref": "#/$defs/d24"}
    start = time.perf_counter()
    out = ossie_validate.inline_local_refs(schema)
    assert time.perf_counter() - start < 2.0
    assert out is schema  # fell back to the plain schema, which validates the same
    for version in ossie_validate.SCHEMAS:  # the pinned schemas are far under the bound and are inlined
        assert ossie_validate.inline_local_refs(_schema(version)) is not _schema(version)
