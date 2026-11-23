"""Hardening follow-ups to the Ossie fixture corpus: wrong-typed parts refused everywhere export refuses them."""

import json

import pytest

import ossie_corpus as oc
from fabric_desktop import model_ossie as mo
from fabric_desktop import semantic_parity, semantic_view, tmdl

WRONG_BIMS = ["hostile/wrong_tables_text.bim", "hostile/wrong_column_name_number.bim", "hostile/wrong_role_text.bim"]


# ---- tmdl.from_bim and L1 run export's model.bim shape check

@pytest.mark.parametrize("path", WRONG_BIMS, ids=lambda p: p.rsplit("/", 1)[-1])
def test_from_bim_refuses_what_export_refuses_with_the_same_path(path):
    e = oc.entry(path)
    with pytest.raises(ValueError) as export:
        mo.export(oc.load(e))
    with pytest.raises(ValueError) as written:
        tmdl.from_bim(oc.load(e))
    assert e["says"] in str(export.value) and e["says"] in str(written.value)
    assert str(written.value).split(": ", 1)[1] == str(export.value).split(": ", 1)[1]


@pytest.mark.parametrize("path", WRONG_BIMS, ids=lambda p: p.rsplit("/", 1)[-1])
@pytest.mark.parametrize("which", ["left", "right"])
def test_l1_names_the_wrong_typed_side_and_its_path(path, which):
    e = oc.entry(path)
    good = oc.load(oc.entry("tmsl/01_minimal.bim"))
    pair = (oc.load(e), good) if which == "left" else (good, oc.load(e))
    report = semantic_parity.parity_l1(*pair)
    assert report["summary"] == semantic_parity.TEST_FAILED
    assert report["error"].startswith(f"the {which} side couldn't be read: ValueError: This model couldn't be compared")
    assert e["says"] in report["error"] and e["says"] in report["sides"][which]["error"]
    assert report["sides"][which]["read"] is False
    other = "right" if which == "left" else "left"
    assert report["sides"][other]["read"] is True


def test_l1_refuses_a_wrong_typed_model_bim_given_as_text_and_l2_does_too():
    text = json.dumps(oc.load(oc.entry("hostile/wrong_role_text.bim")))
    for call in (semantic_parity.parity_l1, semantic_parity.parity_l2):
        report = call(text, text, left_kind="tmsl", right_kind="tmsl")
        assert report["summary"] == semantic_parity.TEST_FAILED
        assert "$.model.roles[0] is text where it should be an object" in report["error"]


def test_l1_of_tmdl_refuses_a_part_the_reader_reads_as_the_wrong_type_as_export_does():
    files = {"definition/model.tmdl": "model Model\n\ttables: x\n"}  # a property where the tables go
    with pytest.raises(ValueError, match=r"\$\.model\.tables is text where it should be a list"):
        mo.tmdl_to_ossie(files, "m")
    report = semantic_parity.parity_l1(files, files)
    assert report["summary"] == semantic_parity.TEST_FAILED
    assert "This model couldn't be compared" in report["error"]
    assert "$.model.tables is text where it should be a list" in report["error"]


@pytest.mark.parametrize("path", ["tmsl/01_minimal.bim", "tmsl/02_rich.bim", "tmdl/rich.SemanticModel"],
                         ids=["minimal", "rich", "tmdl"])
def test_valid_models_are_written_and_compared_as_before(path):
    e = oc.entry(path)
    value = oc.load(e)
    bim = value if e["kind"] == "tmsl" else tmdl.to_bim(value, "m")[0]
    files, report = tmdl.from_bim(bim)
    assert files and all(r["kind"] == "lost" for r in report)
    l1 = semantic_parity.parity_l1(value, value)
    assert l1["summary"] == semantic_parity.CEILING and l1["error"] == ""


def test_a_model_that_isnt_an_object_keeps_its_own_message():
    with pytest.raises(ValueError, match="This isn't a semantic model: it should be one object"):
        tmdl.from_bim({"name": "m", "model": "text"})
    with pytest.raises(ValueError, match="This isn't a semantic model: it should be one object"):
        tmdl.from_bim([])


# ---- the Semantic View emitter runs the shape check, and a view with no dataset left is never Valid

WRONG_OSSIE = [e["path"] for e in oc.manifest() if e["path"].startswith("hostile/wrong_") and e["kind"] == "ossie"
               and e["expect"]["to_tmsl"] == oc.REFUSED and "is " in e["says"] and "where it should be" in e["says"]]


def _view(doc):
    return semantic_view.semantic_view(doc, view_name="v")


@pytest.mark.parametrize("path", WRONG_OSSIE, ids=lambda p: p.rsplit("/", 1)[-1])
def test_the_view_refuses_a_wrong_typed_part_with_the_converters_path(path):
    e = oc.entry(path)
    with pytest.raises(ValueError) as converted:
        mo.to_tmsl(oc.load(e))
    for source in (oc.load(e), mo.parse(oc.load(e))):  # the text, or what parse made of it
        with pytest.raises(ValueError) as viewed:
            _view(source)
        assert str(viewed.value) == str(converted.value) and e["says"] in str(viewed.value)


def test_the_wrong_typed_files_are_the_ones_the_finding_named():
    assert len(WRONG_OSSIE) >= 5


@pytest.mark.parametrize("dataset", [{"name": "q", "source": "SELECT 1 AS a"}, {"source": "dbo.t"}],
                         ids=["query", "nameless"])
def test_a_view_with_no_dataset_left_is_never_valid(dataset):
    out = _view({"version": "0.2.0.dev0", "name": "m", "datasets": [dataset]})
    report = out["report"]
    assert report["excluded"] and not report["valid"]
    assert report["validation"]["status"] == "Invalid" and not report["validation"]["valid"]
    rules = [d["rule"] for d in report["validation"]["diagnostics"]]
    assert rules == ["semantic-view/no-datasets"]
    assert "each of the source's datasets was excluded" in report["validation"]["diagnostics"][0]["message"]


def test_a_view_that_keeps_one_dataset_is_still_valid():
    out = _view({"version": "0.2.0.dev0", "name": "m", "datasets": [
        {"name": "q", "source": "SELECT 1 AS a"}, {"name": "t", "source": "dbo.t"}]})
    assert out["report"]["valid"] and [x["object"] for x in out["report"]["excluded"]] == ["dataset 'q'"]


@pytest.mark.parametrize("path", ["ossie/v02_03_rich.yaml", "ossie/v02_04_powerbi.yaml", "view/sales_view.yaml",
                                  "tmsl/02_rich.bim"], ids=["rich", "powerbi", "view", "bim"])
def test_valid_sources_still_make_a_valid_view(path):
    out = _view(oc.load(oc.entry(path)))
    assert out["report"]["valid"] and "semantic-view/no-datasets" not in str(out["report"]["validation"])
