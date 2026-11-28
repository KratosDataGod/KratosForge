"""The Ossie fixture corpus (OSSIE-012a): every converter and reader entry point over every file, as the manifest
says. Findings the corpus shows are recorded in the manifest and pinned here, not fixed."""

import json
import re

import pytest

import ossie_corpus as oc
from fabric_desktop import model_ossie as mo
from fabric_desktop import ossie_profiles, ossie_validate, semantic_parity, semantic_view, tmdl

ENTRIES = oc.manifest()
IDS = [e["path"].rsplit("/", 1)[-1] for e in ENTRIES]
WRONG_TYPED = [e for e in ENTRIES if e["path"].startswith("hostile/wrong_")]


@pytest.fixture(scope="module")
def results():
    """Every file through every entry point once: path -> point -> (word, detail)."""
    return {e["path"]: oc.outcomes(e) for e in ENTRIES}


def _of(kind):
    return [pytest.param(e, id=e["path"].rsplit("/", 1)[-1]) for e in ENTRIES if kind in oc.ENTRY_POINTS[e["kind"]]]


def _check(results, e, point):
    word, detail = results[e["path"]][point]
    assert word == e["expect"][point], (point, detail)
    if word == oc.REFUSED or word.startswith("error:"):
        assert e["says"] in detail["message"]


def _depth(value):
    """How deeply a parsed value nests (iteratively, so a deep value can't stop the count)."""
    deepest, todo = 0, [(value, 1)]
    while todo:
        v, d = todo.pop()
        deepest = max(deepest, d)
        items = v.values() if isinstance(v, dict) else v if isinstance(v, list) else ()
        todo.extend((i, d + 1) for i in items)
    return deepest


# ---- the corpus and its manifest

def test_the_manifest_lists_every_fixture_once():
    paths = [e["path"] for e in ENTRIES]
    assert len(paths) == len(set(paths))
    assert sorted(paths) == oc.corpus_files()


def test_each_entry_expects_exactly_the_entry_points_of_its_kind():
    for e in ENTRIES:
        assert e["kind"] in oc.KINDS
        assert tuple(e["expect"]) == oc.ENTRY_POINTS[e["kind"]], e["path"]
        assert e["what"]


def test_a_refusal_in_the_manifest_always_says_what_it_says():
    for e in ENTRIES:
        refused = [p for p, w in e["expect"].items() if w == oc.REFUSED or w.startswith("error:")]
        assert bool(refused) == bool(e.get("says")), e["path"]


def test_the_fixtures_are_small_hand_written_files():
    for p in oc.CORPUS.rglob("*"):
        if p.is_file() and p != oc.MANIFEST:
            assert p.stat().st_size < 16_000, p.name


def test_the_corpus_covers_each_profile_and_kind():
    profiles = set()
    for e in ENTRIES:
        if e["kind"] in ("ossie", "view"):
            try:
                profiles.add(ossie_profiles.detect_profile(mo.load_text(oc.load(e))))
            except (ValueError, TypeError, AttributeError):  # narrow: the hostile files have no profile
                continue
    assert {"ossie-0.2", "ossie-0.3", "snowflake-ossie"} <= profiles
    assert {e["kind"] for e in ENTRIES} == set(oc.KINDS)


def test_the_valid_ossie_files_grow_in_richness():
    sizes = []
    for name in ("v02_01_minimal.yaml", "v02_02_star.yaml", "v02_03_rich.yaml", "v02_04_powerbi.yaml"):
        doc = mo.parse(oc.load(oc.entry("ossie/" + name)))
        bim, _ = mo.to_tmsl(doc)
        model = bim["model"]
        sizes.append(sum(len(t.get("columns") or []) + len(t.get("measures") or []) for t in model["tables"])
                     + len(model.get("relationships") or []) + len(model.get("roles") or []))
    assert sizes == sorted(sizes) and len(set(sizes)) == len(sizes)


def test_the_loader_reads_each_kind_as_its_entry_points_take_it():
    assert isinstance(oc.load(oc.entry("ossie/v02_01_minimal.yaml")), str)
    assert isinstance(oc.load(oc.entry("tmsl/01_minimal.bim"))["model"], dict)
    files = oc.load(oc.entry("tmdl/rich.SemanticModel"))
    assert "definition/model.tmdl" in files and all(p.endswith(".tmdl") for p in files)


def test_the_deep_fixtures_stay_well_under_secret_scans_recursion_limit():
    for e in ENTRIES:
        if e["kind"] in ("ossie", "view", "tmsl"):
            try:
                value = mo.load_text(oc.load(e)) if e["kind"] != "tmsl" else oc.load(e)
            except (ValueError, TypeError):  # narrow: the files that don't read are not deep
                continue
            assert _depth(value) < 1_000, e["path"]
    assert _depth(mo.load_text(oc.load(oc.entry("hostile/deep_ai_context.yaml")))) > 300
    assert _depth(oc.load(oc.entry("hostile/deep_extended_property.bim"))) > 300


# ---- each entry point over each file, as the manifest says

@pytest.mark.parametrize("e", _of("to_tmsl"))
def test_to_tmsl_meets_the_manifest(results, e):
    _check(results, e, "to_tmsl")


@pytest.mark.parametrize("e", _of("export"))
def test_export_meets_the_manifest(results, e):
    _check(results, e, "export")


@pytest.mark.parametrize("e", _of("validate"))
def test_validate_meets_the_manifest(results, e):
    _check(results, e, "validate")


@pytest.mark.parametrize("e", _of("round_trip"))
def test_round_trip_meets_the_manifest(results, e):
    _check(results, e, "round_trip")


@pytest.mark.parametrize("e", _of("tmdl"))
def test_tmdl_meets_the_manifest(results, e):
    _check(results, e, "tmdl")


@pytest.mark.parametrize("e", _of("semantic_view"))
def test_semantic_view_meets_the_manifest(results, e):
    _check(results, e, "semantic_view")


@pytest.mark.parametrize("e", _of("parity_l1"))
def test_parity_l1_meets_the_manifest(results, e):
    _check(results, e, "parity_l1")


def test_every_conversion_that_runs_has_a_complete_fidelity_report(results):
    for path, points in results.items():
        for point in ("to_tmsl", "export"):
            word, detail = points.get(point, ("", {}))
            if word == oc.OK:
                assert detail["status"] == "complete" and detail["fidelity"], (path, point)


def test_the_valid_files_round_trip_the_same(results):
    for e in ENTRIES:
        if e["path"].split("/")[0] in ("ossie", "view", "tmsl", "tmdl"):
            assert results[e["path"]]["round_trip"][0] == "same", e["path"]


# ---- wrong types: a plain ValueError only

@pytest.mark.parametrize("e", [pytest.param(e, id=e["path"].rsplit("/", 1)[-1]) for e in WRONG_TYPED])
def test_wrong_typed_input_is_only_ever_a_value_error(results, e):
    for point, (word, detail) in results[e["path"]].items():
        assert not word.startswith("error:"), (point, detail)
        if word == oc.REFUSED:
            assert detail["type"] in ("ValueError", "UnknownProfile"), (point, detail)


def test_no_file_raises_anything_but_a_value_error(results):
    for e in ENTRIES:
        assert not [p for p, (w, _) in results[e["path"]].items() if w.startswith("error:")], e["path"]
        assert not [p for p, w in e["expect"].items() if w.startswith("error:")], e["path"]


def test_a_root_that_isnt_an_object_is_a_plain_value_error():  # fixed in typed-errors
    for name in ("top_level_list.yaml", "empty.yaml"):
        e = oc.entry("hostile/" + name)
        with pytest.raises(ValueError, match="^" + re.escape(mo.NOT_A_MODEL) + "$"):
            mo.to_tmsl(oc.load(e))
        assert ossie_validate.validate(oc.load(e))["diagnostics"][0]["rule"] == "type"


def test_refusals_name_the_path_and_never_the_value(results):
    for e in WRONG_TYPED:
        for word, detail in results[e["path"]].values():
            if word == oc.REFUSED and "shape" in detail["message"]:
                assert "$." in detail["message"]
                assert "order_id, amount" not in detail["message"] and "2024" not in detail["message"]


def test_a_huge_name_is_cut_short_in_a_refusal(results):
    message = results["hostile/wrong_type_huge_name.yaml"]["to_tmsl"][1]["message"]
    assert "(2000 characters)" in message and "n" * (mo.MAX_NAME + 1) not in message
    assert len(message) < mo.MAX_NAME + 400


def test_a_huge_name_converts_and_the_report_cuts_it_short(results):
    names = results["hostile/huge_name.yaml"]["to_tmsl"][1]["fidelity"]
    assert names and all(len(n) < 2 * mo.MAX_NAME + 200 for n in names)
    assert any("(2000 characters)" in n for n in names)


# ---- hostile text

def test_unicode_names_survive_conversion_and_the_view():
    e = oc.entry("hostile/unicode_names.yaml")
    bim, _ = mo.to_tmsl(oc.load(e))
    columns = {c["name"] for t in bim["model"]["tables"] for c in t.get("columns") or []}
    assert {"Größe", "顧客", "מחיר", "status 🚚", "line break"} <= columns
    out = semantic_view.semantic_view(mo.parse(oc.load(e)), view_name="corpus_view")
    assert " " not in "".join(out["files"].values())  # written as an escape, never raw


def test_a_line_separator_in_a_name_is_refused_by_tmdl_by_name():
    with pytest.raises(ValueError, match=r"column 'line\\u2028break': its name has a line break"):
        mo.ossie_to_tmdl(oc.load(oc.entry("hostile/unicode_names.yaml")))


def test_aliases_and_repeated_keys_are_refused_with_the_line():
    with pytest.raises(ValueError, match=r"aliases \(line 10\)"):
        mo.parse(oc.load(oc.entry("hostile/aliases.yaml")))
    with pytest.raises(ValueError, match=r"'datasets' twice in one object \(line 9\)"):
        mo.parse(oc.load(oc.entry("hostile/duplicate_key.yaml")))


# ---- findings: those fixed in typed-errors, then in harden, then in rolefid

def test_conversion_keeps_the_depth_limit_validate_uses():  # fixed in typed-errors
    text = oc.load(oc.entry("hostile/deep_ai_context.yaml"))
    result = ossie_validate.validate(text)
    assert result["status"] == "Not checked" and result["diagnostics"][0]["rule"] == "depth"
    limit = rf"more than {ossie_validate.MAX_DEPTH} levels"
    for call in (mo.to_tmsl, mo.ossie_to_tmdl, lambda t: semantic_view.semantic_view(t, view_name="v")):
        with pytest.raises(ValueError, match=limit):
            call(text)
    assert re.search(limit, semantic_parity.parity_l1(text, text)["error"])


def test_a_model_bim_nested_past_the_limit_is_refused_but_l1_still_reads_it():  # fixed in typed-errors, but L1
    bim = oc.load(oc.entry("hostile/deep_extended_property.bim"))
    limit = rf"more than {mo.MAX_DEPTH} levels"
    with pytest.raises(ValueError, match=limit):
        mo.export(bim)
    with pytest.raises(ValueError, match=limit):
        tmdl.from_bim(bim)
    assert not mo.round_trip_check(bim)["ran"]
    assert semantic_parity.parity_l1(bim, bim)["summary"] == semantic_parity.CEILING


def test_a_semantic_view_converted_to_tmdl_keeps_its_relationships_and_says_the_types_werent_known():  # rolefid
    files, report = mo.ossie_to_tmdl(oc.load(oc.entry("view/sales_view.yaml")))
    assert "relationship orders_customers" in files["definition/relationships.tmdl"]
    rows = [r for r in report if r["object"] == "relationship 'orders_customers'"]
    assert [r["kind"] for r in rows] == ["kept", "kept"]
    assert all("data type wasn't known" in r["message"] for r in rows)
    assert "dataType" not in files["definition/tables/orders.tmdl"]


def test_l1_of_a_view_and_its_tmdl_matches_the_relationship():  # rolefid
    text = oc.load(oc.entry("view/sales_view.yaml"))
    files, _ = mo.ossie_to_tmdl(text)
    report = semantic_parity.parity_l1(text, files, left_kind="semantic_view", right_kind="tmdl")
    rels = [o for o in report["objects"] if o["kind"] == "relationship"]
    assert rels and all(o["ids"]["right"] is not None and o["result"] == semantic_parity.CEILING for o in rels)


def test_roles_come_back_preserved_through_extensions_on_import_with_a_security_warning():  # rolefid
    doc = mo.parse(oc.load(oc.entry("ossie/v02_04_powerbi.yaml")))
    bim, report = mo.to_tmsl(doc)
    fid = mo.fidelity(doc, bim, report)
    assert fid["flagged"]["roles"] == [{"object": "role 'EU readers'", "class": "preserved_through_extensions"}]
    assert [w["object"] for w in fid["warnings"] if w["severity"] == "security"] == ["role 'EU readers'"]


def test_roles_export_only_in_the_extension_with_a_security_warning(results):
    classes = results["tmsl/02_rich.bim"]["export"][1]["fidelity"]
    assert classes["role 'EU readers'"] == "preserved_through_extensions"
    out = mo.export(oc.load(oc.entry("tmsl/02_rich.bim")), translate_dax=False)
    assert out["fidelity"]["warnings"][0]["severity"] == "security"


def test_tmdl_and_l1_refuse_wrong_typed_bim_parts_as_export_does(results):  # fixed in harden
    for name in ("wrong_tables_text.bim", "wrong_column_name_number.bim", "wrong_role_text.bim"):
        e = oc.entry("hostile/" + name)
        points = results[e["path"]]
        assert points["export"][0] == points["tmdl"][0] == oc.REFUSED
        assert e["says"] in points["tmdl"][1]["message"]
        assert points["tmdl"][1]["message"].startswith("This model couldn't be written as TMDL, because part of it")
        assert points["parity_l1"][0] == semantic_parity.TEST_FAILED
        error = points["parity_l1"][1]["error"]
        assert error.startswith("the left side couldn't be read: ValueError: This model couldn't be compared")
        assert e["says"] in error


def test_the_view_refuses_wrong_typed_parts_by_path():  # fixed in harden
    e = oc.entry("hostile/wrong_numeric_name.yaml")
    with pytest.raises(ValueError, match=r"^The Ossie file couldn't be converted, because part of it") as exc:
        semantic_view.semantic_view(mo.parse(oc.load(e)), view_name="corpus_view")
    assert e["says"] in str(exc.value)


# ---- versions and the TMDL reader

def test_0_3_and_snowflake_files_are_read_and_checked_never_converted(monkeypatch):
    mo.require()
    called = []
    monkeypatch.setattr(mo, "convert_ossie_to_semantic_model", lambda *a, **k: called.append(a))
    for path, status in (("versions/v03_star.yaml", "Valid"), ("versions/snowflake_011.yaml", "Unverified")):
        text = oc.load(oc.entry(path))
        assert ossie_validate.validate(text)["status"] == status
        with pytest.raises(ValueError, match="can't convert it yet"):
            mo.to_tmsl(text)
    assert called == []


def test_an_unknown_version_is_refused_by_name_everywhere(results):
    for word, detail in results["versions/unknown_version.yaml"].values():
        assert word in (oc.REFUSED, "Not checked", semantic_parity.TEST_FAILED)
        assert "0.9.0" in detail.get("message", "") + detail.get("error", "") or word == "Not checked"


def test_the_tmdl_reader_names_lines_it_cant_read(results):
    report = results["tmdl/unknown_lines.SemanticModel"]["tmdl"][1]["report"]
    assert report == ["calendar 'Fiscal year'"]


def test_the_manifest_is_valid_json_with_findings_as_plain_sentences():
    doc = json.loads(oc.MANIFEST.read_text(encoding="utf-8"))
    findings = {f for e in doc["files"] for f in e.get("findings", [])}
    assert len(findings) == 6
    fixed = {f for f in findings if f.startswith(("Fixed in typed-errors: ", "Fixed in harden: "))}
    assert len(fixed) == 4 and len(findings - fixed) == 2
    assert all(f.startswith("Fixed in rolefid: ") for f in findings - fixed)
    for e in doc["files"]:  # a hardening fix is expected as a refusal, never as an error
        if fixed & set(e.get("findings", [])):
            assert oc.REFUSED in e["expect"].values() and not any(w.startswith("error:") for w in e["expect"].values())
    assert all(re.fullmatch(r"\S.{40,400}\.", f) for f in findings)
