"""The shape check before an Ossie conversion (``model_ossie._refuse_shape``): a key the conversion reads that holds
a value of the wrong type (text for the datasets, a list for a name, a number for an object) is refused with a
ValueError naming where it is, never a TypeError, AttributeError or KeyError from inside the converter."""

import copy
import re

import pytest
import yaml

from fabric_desktop import model_ossie, ossie_validate, semantic_parity, semantic_view, tmdl
from tests.test_model_ossie import _bim, _ossie

pytestmark = pytest.mark.skipif(bool(model_ossie.unavailable()), reason="the Ossie converter can't run here")


def _doc():
    return yaml.safe_load(_ossie())


def _set(doc, at, value):
    """``doc`` with the part at ``at`` (a tuple of keys and indexes) set to ``value``; "+" as the last key appends."""
    parent = doc
    for k in at[:-1]:
        parent = parent[k]
    if at[-1] == "+":
        parent.append(value)
    else:
        parent[at[-1]] = value
    return doc


def _refused(fn, path):
    with pytest.raises(ValueError, match=r"^(The Ossie file couldn't be converted|This model couldn't be converted to "
                                         r"Ossie|The model couldn't be updated), because part of it isn't the shape "
                                         r".*\. " + re.escape(path) + " is "):
        fn()


DS, CH = "$.datasets['fact_sales']", "$.datasets['dim_channel']"
AMOUNT = f"{DS}.fields['amount']"

OSSIE_CASES = [
    pytest.param(("datasets",), "x", "$.datasets", id="datasets-text"),
    pytest.param(("datasets", 0), "x", "$.datasets[0]", id="dataset-text"),
    pytest.param(("datasets", "+"), 5, "$.datasets[2]", id="dataset-number"),
    pytest.param(("name",), 5, "$.name", id="name-number"),
    pytest.param(("description",), ["a"], "$.description", id="description-list"),
    pytest.param(("datasets", 0, "name"), 2024, "$.datasets[0].name", id="dataset-name-number"),
    pytest.param(("datasets", 0, "source"), ["a"], f"{DS}.source", id="source-list"),
    pytest.param(("datasets", 0, "fields"), "x", f"{DS}.fields", id="fields-text"),
    pytest.param(("datasets", 0, "fields", "+"), [1], f"{DS}.fields[3]", id="field-list"),
    pytest.param(("datasets", 0, "fields", 1, "name"), ["a"], f"{DS}.fields[1].name", id="field-name-list"),
    pytest.param(("datasets", 0, "fields", 1, "datatype"), 5, f"{AMOUNT}.datatype", id="datatype-number"),
    pytest.param(("datasets", 0, "fields", 1, "expression"), "amount", f"{AMOUNT}.expression", id="expr-text"),
    pytest.param(("datasets", 0, "fields", 1, "expression", "dialects"), {"a": 1}, f"{AMOUNT}.expression.dialects",
                 id="dialects-object"),
    pytest.param(("datasets", 0, "fields", 1, "expression", "dialects", 0), "x",
                 f"{AMOUNT}.expression.dialects[0]", id="dialect-text"),
    pytest.param(("datasets", 0, "fields", 1, "expression", "dialects", 0, "expression"), [1],
                 f"{AMOUNT}.expression.dialects[0].expression", id="dialect-expr-list"),
    pytest.param(("datasets", 0, "fields", 1, "dimension"), "x", f"{AMOUNT}.dimension", id="dimension-text"),
    pytest.param(("datasets", 0, "metrics", 0, "expression"), 5, f"{DS}.metrics['net revenue'].expression",
                 id="metric-expr-number"),
    pytest.param(("datasets", 0, "metrics", "+"), "x", f"{DS}.metrics[1]", id="metric-text"),
    pytest.param(("datasets", 0, "custom_extensions"), "x", f"{DS}.custom_extensions", id="extensions-text"),
    pytest.param(("datasets", 1, "primary_key", 0), 5, f"{CH}.primary_key[0]", id="primary-key-number"),
    pytest.param(("datasets", 1, "unique_keys"), ["a"], f"{CH}.unique_keys[0]", id="unique-key-text"),
    pytest.param(("metrics",), "x", "$.metrics", id="metrics-text"),
    pytest.param(("relationships",), {"a": 1}, "$.relationships", id="relationships-object"),
    pytest.param(("relationships", 0, "from"), 5, "$.relationships['sales_channel'].from", id="from-number"),
    pytest.param(("relationships", 0, "to_columns", 0), True, "$.relationships['sales_channel'].to_columns[0]",
                 id="to-column-bool"),
]


@pytest.mark.parametrize("at, value, path", OSSIE_CASES)
def test_a_wrong_typed_part_of_an_ossie_file_is_refused_by_path(at, value, path):
    doc = _set(_doc(), at, value)
    _refused(lambda: model_ossie.to_tmsl(doc), path)


TABLE = "$.model.tables['fact_sales']"

BIM_CASES = [
    pytest.param(("model",), "x", "$.model", id="model-text"),
    pytest.param(("model", "tables"), "x", "$.model.tables", id="tables-text"),
    pytest.param(("model", "tables", "+"), 5, "$.model.tables[2]", id="table-number"),
    pytest.param(("model", "tables", 0, "name"), 5, "$.model.tables[0].name", id="table-name-number"),
    pytest.param(("model", "tables", 0, "columns"), {"a": 1}, f"{TABLE}.columns", id="columns-object"),
    pytest.param(("model", "tables", 0, "columns", 0), "x", f"{TABLE}.columns[0]", id="column-text"),
    pytest.param(("model", "tables", 0, "columns", 1, "dataType"), 5, f"{TABLE}.columns['amount'].dataType",
                 id="datatype-number"),
    pytest.param(("model", "tables", 0, "measures", 0, "expression"), {"a": 1},
                 f"{TABLE}.measures['fact_sales rows'].expression", id="measure-expr-object"),
    pytest.param(("model", "tables", 0, "partitions", 0, "source"), "x", f"{TABLE}.partitions['fact_sales'].source",
                 id="partition-source-text"),
    pytest.param(("model", "relationships", 0, "fromColumn"), [1], "$.model.relationships['r1'].fromColumn",
                 id="rel-column-list"),
    pytest.param(("model", "roles", "+"), "x", "$.model.roles[1]", id="role-text"),
    pytest.param(("compatibilityLevel",), True, "$.compatibilityLevel", id="compat-bool"),
]


@pytest.mark.parametrize("at, value, path", BIM_CASES)
def test_a_wrong_typed_part_of_a_model_is_refused_by_path(at, value, path):
    bim = _set(_bim(), at, value)
    _refused(lambda: model_ossie.export(bim, translate_dax=False), path)


def test_every_entry_point_checks_its_input():
    bad_doc = _set(_doc(), ("datasets", 0, "fields", "+"), "x")
    bad_bim = _set(_bim(), ("model", "tables", 0, "columns"), "x")
    good = _bim()
    for fn in (lambda: model_ossie.to_tmsl(bad_doc), lambda: model_ossie.stashed(bad_doc),
               lambda: model_ossie.ossie_to_tmdl(yaml.safe_dump(bad_doc))):
        _refused(fn, f"{DS}.fields[3]")
    for fn in (lambda: model_ossie.export(bad_bim), lambda: model_ossie.merge(bad_bim, good),
               lambda: model_ossie.merge(good, bad_bim), lambda: model_ossie.plan(bad_bim, good),
               lambda: model_ossie.plan(None, bad_bim), lambda: model_ossie.removable(bad_bim, good)):
        _refused(fn, f"{TABLE}.columns")


def test_valid_documents_pass_unchanged():
    doc, bim = _doc(), _bim()
    before = copy.deepcopy((doc, bim))
    model_ossie._refuse_shape(doc, model_ossie._OSSIE_SHAPE, "ossie")
    model_ossie._refuse_shape(bim, model_ossie._BIM_SHAPE, "update")
    assert (doc, bim) == before
    tmsl, _ = model_ossie.to_tmsl(doc)
    assert model_ossie.export(bim, translate_dax=False)["counts"]["datasets"] == 2
    model_ossie._refuse_shape(tmsl, model_ossie._BIM_SHAPE, "update")  # what the converter writes passes too
    assert (doc, bim) == before


def test_text_as_lines_and_empty_values_pass():
    bim = _bim()
    bim["model"]["tables"][0]["description"] = ["line one", "line two"]
    bim["model"]["expressions"][0]["expression"] = ["let", "in x"]
    bim["model"]["annotations"] = []
    bim["description"] = ""
    model_ossie._refuse_shape(bim, model_ossie._BIM_SHAPE, "update")
    doc = _doc()
    doc["datasets"][0]["fields"][0]["expression"] = {}
    doc["datasets"][0]["description"] = None
    model_ossie._refuse_shape(doc, model_ossie._OSSIE_SHAPE, "ossie")


def test_the_message_names_the_place_never_the_value():
    doc = _set(_doc(), ("datasets", 0, "fields", 1, "description"), ["secret-value-42"])
    with pytest.raises(ValueError) as exc:
        model_ossie.to_tmsl(doc)
    assert "secret-value-42" not in str(exc.value)
    assert "is a list where it should be text" in str(exc.value)


def test_a_number_where_a_name_goes_says_to_quote_it():
    doc = _set(_doc(), ("relationships", 0, "from"), 2024)
    with pytest.raises(ValueError, match=r"a name that is a number goes in quotes"):
        model_ossie.to_tmsl(doc)


def test_a_long_name_in_the_path_is_cut_short():
    doc = _doc()
    doc["datasets"][0]["name"] = "n" * 5000
    doc["datasets"][0]["fields"][0] = 5
    with pytest.raises(ValueError, match=r"\(5000 characters\)'\]\.fields\[0\] is a number") as exc:
        model_ossie._refuse_shape(doc, model_ossie._OSSIE_SHAPE, "ossie")
    assert len(str(exc.value)) < 1300


def _deep(n, leaf, wrap):
    out = leaf
    for _ in range(n):
        out = wrap(out)
    return out


def test_deep_nesting_is_never_walked():
    doc = _doc()
    doc["datasets"][0]["fields"][0]["dimension"] = _deep(100_000, {}, lambda v: {"a": v})
    model_ossie._refuse_shape(doc, model_ossie._OSSIE_SHAPE, "ossie")  # an object where one goes; not walked
    bim = _bim()
    bim["model"]["tables"][0]["calculationGroup"] = _deep(100_000, {}, lambda v: {"a": v})
    model_ossie._refuse_shape(bim, model_ossie._BIM_SHAPE, "update")


def test_deeply_nested_wrong_types_are_refused_before_conversion():
    doc = _set(_doc(), ("datasets", 0, "fields"), _deep(100_000, [], lambda v: [v]))
    _refused(lambda: model_ossie.to_tmsl(doc), f"{DS}.fields[0]")
    bim = _set(_bim(), ("model", "tables"), _deep(100_000, [], lambda v: [v]))
    _refused(lambda: model_ossie.export(bim), "$.model.tables[0]")


# ---- a root that isn't one object, and nesting past the schema check's limit

ROOTS = [pytest.param("- version: 0.2.0.dev0\n", id="list"), pytest.param("", id="empty"),
         pytest.param("\n# nothing\n", id="comment-only"), pytest.param("2024\n", id="number"),
         pytest.param("just text\n", id="text"), pytest.param("null\n", id="null")]


@pytest.mark.parametrize("text", ROOTS)
def test_a_root_that_isnt_one_object_is_a_plain_value_error(text):
    for fn in (model_ossie.parse, model_ossie.to_tmsl, model_ossie.ossie_to_tmdl, model_ossie.stashed,
               lambda t: semantic_view.semantic_view(t, view_name="v")):
        with pytest.raises(ValueError, match="^" + re.escape(model_ossie.NOT_A_MODEL) + "$"):
            fn(text)


@pytest.mark.parametrize("root", [None, [], [{"name": "x"}], "x", 5, 1.5, True], ids=repr)
def test_every_public_entry_point_refuses_a_root_of_the_wrong_type_with_a_value_error(root):
    calls = {
        "to_tmsl": lambda: model_ossie.to_tmsl(root), "stashed": lambda: model_ossie.stashed(root),
        "export": lambda: model_ossie.export(root), "round_trip": lambda: model_ossie.round_trip(root),
        "merge current": lambda: model_ossie.merge(root, _bim()), "merge incoming": lambda: model_ossie.merge(_bim(), root),
        "plan": lambda: model_ossie.plan(_bim(), root), "removable": lambda: model_ossie.removable(_bim(), root),
        "fidelity": lambda: model_ossie.fidelity(root, _bim()), "tmdl_to_ossie": lambda: model_ossie.tmdl_to_ossie(root, "m"),
        "to_bim": lambda: tmdl.to_bim(root), "from_bim": lambda: tmdl.from_bim(root),
        "semantic_view": lambda: semantic_view.semantic_view(root, view_name="v"),
    }
    for name, call in calls.items():
        try:
            call()
        except ValueError:
            continue
        except Exception as exc:  # noqa: BLE001 - any other exception is the failure this test names
            pytest.fail(f"{name}: {type(exc).__name__}: {exc}")
    assert model_ossie.model_name(root) == ""
    assert model_ossie.safe_fidelity(root, _bim())["status"] == "unverified"
    report = semantic_parity.parity_l1(root, _bim())
    assert report["summary"] == semantic_parity.TEST_FAILED and "TypeError" not in report["error"]


def test_tmdl_files_of_the_wrong_type_are_a_value_error():
    for files in ({"definition/model.tmdl": 5}, {5: "model Model"}, "model Model"):
        with pytest.raises(ValueError, match="path -> text"):
            tmdl.to_bim(files)


def test_conversion_and_the_schema_check_share_one_depth_limit():
    assert model_ossie.MAX_DEPTH == ossie_validate.MAX_DEPTH == tmdl.MAX_DEPTH == 64


@pytest.mark.parametrize("levels", [model_ossie.MAX_DEPTH - 1, model_ossie.MAX_DEPTH, model_ossie.MAX_DEPTH + 1,
                                    100_000])
def test_conversion_refuses_nesting_exactly_where_the_schema_check_does(levels):
    doc = _doc()
    doc["ai_context"] = _deep(levels - 1, [], lambda v: [v])  # the list itself is one level under the root
    too_deep = any(d["rule"] == "depth" for d in ossie_validate.validate(doc)["diagnostics"])
    assert too_deep == (levels > model_ossie.MAX_DEPTH)
    if not too_deep:
        model_ossie.to_tmsl(doc)
        return
    says = rf"nested too deeply \(more than {model_ossie.MAX_DEPTH} levels\)"
    for call in (lambda: model_ossie.to_tmsl(doc), lambda: model_ossie.ossie_to_tmdl(doc),
                 lambda: semantic_view.semantic_view(doc, view_name="v")):
        with pytest.raises(ValueError, match=says):
            call()
    if levels < 900:  # YAML text deeper than this stops PyYAML first ("nested too deeply" all the same)
        text = yaml.safe_dump(doc)
        for call in (lambda: model_ossie.to_tmsl(text), lambda: model_ossie.ossie_to_tmdl(text)):
            with pytest.raises(ValueError, match=says):
                call()
    assert "nested too deeply" in semantic_parity.parity_l1(doc, doc)["error"]


@pytest.mark.parametrize("levels", [model_ossie.MAX_DEPTH, model_ossie.MAX_DEPTH + 1, 100_000])
def test_a_model_bim_nested_past_the_limit_is_refused_by_export_and_tmdl(levels):
    bim = _bim()
    # model (1), tables (2), the table (3), columns (4), the column (5), extendedProperties (6), its item (7), the
    # value (8) and the lists inside it, the last at level `levels`
    bim["model"]["tables"][0]["columns"][0]["extendedProperties"] = [
        {"name": "deep", "type": "json", "value": _deep(levels - 8, [], lambda v: [v])}]
    if levels <= model_ossie.MAX_DEPTH:
        assert model_ossie.export(bim, translate_dax=False)["yaml"]
        assert tmdl.from_bim(bim)[0]
        return
    with pytest.raises(ValueError, match=rf"nested too deeply to read \(more than {model_ossie.MAX_DEPTH} levels\)"):
        model_ossie.export(bim)
    with pytest.raises(ValueError, match=rf"nested too deeply to be a semantic model \(more than {model_ossie.MAX_DEPTH}"):
        tmdl.from_bim(bim)
    check = model_ossie.round_trip_check(bim)
    assert check["ran"] is False and "nested too deeply" in check["error"]


def test_a_real_model_with_cultures_and_annotations_is_well_inside_the_limit():
    bim = _bim()
    bim["model"]["cultures"] = [{"name": "de-DE", "translations": {"model": {"name": "Model", "tables": [
        {"name": "fact_sales", "columns": [{"name": "amount", "translatedCaption": "Betrag"}]}]}}}]
    assert not tmdl.too_deep(bim) and not tmdl.too_deep(yaml.safe_load(model_ossie.export(bim)["yaml"]))
